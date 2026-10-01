using System.Net.WebSockets;
using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TeamsMeetingMediaBridge;

public interface IRealtimeWebSocketClientFactory
{
    IRealtimeWebSocketClient Create();
}

public interface IRealtimeWebSocketClient : IAsyncDisposable
{
    Task ConnectAsync(
        Uri uri,
        IReadOnlyDictionary<string, string> headers,
        CancellationToken cancellationToken);

    Task SendTextAsync(string text, CancellationToken cancellationToken);

    Task<string?> ReceiveTextAsync(CancellationToken cancellationToken);

    Task CloseAsync(CancellationToken cancellationToken);
}

public sealed class ClientWebSocketRealtimeClientFactory : IRealtimeWebSocketClientFactory
{
    public IRealtimeWebSocketClient Create() => new ClientWebSocketRealtimeClient();
}

public sealed class OpenAIRealtimeSpeechAdapter : IMeetingSpeechAdapter
{
    private readonly OpenAIRealtimeOptions options;
    private readonly IRealtimeWebSocketClientFactory webSocketFactory;

    public OpenAIRealtimeSpeechAdapter(
        OpenAIRealtimeOptions options,
        IRealtimeWebSocketClientFactory webSocketFactory)
    {
        this.options = options;
        this.webSocketFactory = webSocketFactory;
    }

    public async Task<IMeetingSpeechSession> StartAsync(
        MeetingSpeechStartRequest request,
        CancellationToken cancellationToken)
    {
        if (request.AudioSession is null)
        {
            throw new InvalidOperationException(
                "OpenAI Realtime Teams mode requires a live meeting audio session.");
        }

        var settings = OpenAIRealtimeSessionSettings.From(options, request);
        var session = new OpenAIRealtimeMeetingSpeechSession(
            settings,
            webSocketFactory.Create(),
            request.AudioSession,
            request.OnFinalTranscript,
            request.OnError,
            request.OnSpeechStatus,
            request.QueryOriginAgent);
        try
        {
            await session.StartAsync(cancellationToken);
            return session;
        }
        catch { await session.DisposeAsync(); throw; }
    }

    private sealed class OpenAIRealtimeMeetingSpeechSession : IMeetingSpeechSession
    {
        private static readonly JsonSerializerOptions JsonOptions = new()
        {
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };

        private readonly OpenAIRealtimeSessionSettings settings;
        private readonly IRealtimeWebSocketClient socket;
        private readonly IMeetingAudioSession audioSession;
        private readonly Func<string, CancellationToken, Task> onFinalTranscript;
        private readonly Func<string, CancellationToken, Task> onError;
        private readonly SpeechStatusCallback onSpeechStatus;
        private readonly Func<string, string, string, CancellationToken, Task<string>>? queryOrigin;
        private readonly ConcurrentDictionary<string, Task> queries = new();
        private readonly CancellationTokenSource receiveLoopCts = new();
        private readonly SemaphoreSlim sendLock = new(1, 1);
        private readonly SemaphoreSlim responseSignal = new(0, 1);
        private readonly object activeResponseLock = new();
        private Task? receiveLoopTask;
        private Task? responseLoopTask;
        private string? activeResponseId;
        private string? activeOperationId;
        private bool activeResponseTranscriptCompleted;
        private bool responseCreatePending;
        private bool responseRequested;
        private bool callerSpeaking;
        private bool disposed;

        public OpenAIRealtimeMeetingSpeechSession(
            OpenAIRealtimeSessionSettings settings,
            IRealtimeWebSocketClient socket,
            IMeetingAudioSession audioSession,
            Func<string, CancellationToken, Task> onFinalTranscript,
            Func<string, CancellationToken, Task> onError,
            SpeechStatusCallback onSpeechStatus,
            Func<string, string, string, CancellationToken, Task<string>>? queryOrigin)
        {
            this.settings = settings;
            this.socket = socket;
            this.audioSession = audioSession;
            this.onFinalTranscript = onFinalTranscript;
            this.onError = onError;
            this.onSpeechStatus = onSpeechStatus;
            this.queryOrigin = queryOrigin;
        }

        public async Task StartAsync(CancellationToken cancellationToken)
        {
            await socket.ConnectAsync(
                settings.WebSocketUri,
                new Dictionary<string, string>
                {
                    ["Authorization"] = $"Bearer {settings.ApiKey}",
                },
                cancellationToken);
            await SendSessionUpdateAsync(cancellationToken);
            if (queryOrigin is not null)
                responseLoopTask = Task.Run(() => RunResponseLoopAsync(receiveLoopCts.Token), CancellationToken.None);
            receiveLoopTask = Task.Run(
                () => RunReceiveLoopAsync(receiveLoopCts.Token),
                CancellationToken.None);
        }

        public async Task PushAudioAsync(byte[] pcmAudio, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (disposed || pcmAudio.Length == 0)
            {
                return;
            }

            var realtimeAudio = PcmAudio.ResamplePcm16Mono(
                pcmAudio,
                PcmAudio.TeamsSampleRate,
                PcmAudio.RealtimeSampleRate);
            await SendRealtimeEventAsync(
                new Dictionary<string, object?>
                {
                    ["type"] = "input_audio_buffer.append",
                    ["audio"] = Convert.ToBase64String(realtimeAudio),
                },
                cancellationToken);
        }

        public async Task<SpeechPlaybackResult> SpeakAsync(
            string text,
            string? voice,
            bool interrupt,
            CancellationToken cancellationToken)
        {
            _ = voice;
            cancellationToken.ThrowIfCancellationRequested();
            var operationId = $"speech-{Guid.NewGuid():N}";
            if (interrupt)
            {
                await CancelActiveResponseAsync("explicit_interrupt", cancellationToken);
            }

            lock (activeResponseLock)
            {
                activeOperationId = operationId;
                activeResponseId = null;
                activeResponseTranscriptCompleted = false;
            }

            await SendRealtimeEventAsync(
                new Dictionary<string, object?>
                {
                    ["type"] = "conversation.item.create",
                    ["item"] = new Dictionary<string, object?>
                    {
                        ["type"] = "message",
                        ["role"] = "user",
                        ["content"] = new object[]
                        {
                            new Dictionary<string, object?>
                            {
                                ["type"] = "input_text",
                                ["text"] = text,
                            },
                        },
                    },
                },
                cancellationToken);
            await SendRealtimeEventAsync(
                new Dictionary<string, object?>
                {
                    ["type"] = "response.create",
                    ["response"] = new Dictionary<string, object?>
                    {
                        ["output_modalities"] = new[] { "audio" },
                    },
                },
                cancellationToken);
            return new SpeechPlaybackResult(
                "queued",
                0,
                operationId,
                new Dictionary<string, object>
                {
                    ["source"] = "openai_realtime",
                    ["voice"] = settings.Voice,
                });
        }

        public async ValueTask DisposeAsync()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            receiveLoopCts.Cancel();
            try
            {
                using var closeTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                await socket.CloseAsync(closeTimeout.Token);
            }
            catch
            {
                // Best effort close; disposal should not hide the meeting stop path.
            }

            foreach (var loop in new[] { receiveLoopTask, responseLoopTask })
            {
                if (loop is null) continue;
                try
                {
                    await loop.WaitAsync(TimeSpan.FromSeconds(2));
                }
                catch
                {
                    // The close path above is already best effort.
                }
            }

            await socket.DisposeAsync();
            try { await Task.WhenAll(queries.Values).WaitAsync(TimeSpan.FromSeconds(2)); }
            catch { /* Polling stops on hangup; accepted origin work remains durable. */ }
            receiveLoopCts.Dispose();
            sendLock.Dispose();
            responseSignal.Dispose();
        }

        private async Task SendSessionUpdateAsync(CancellationToken cancellationToken)
        {
            var payload = new Dictionary<string, object?>
            {
                ["type"] = "session.update",
                ["session"] = new Dictionary<string, object?>
                {
                    ["type"] = "realtime",
                    ["model"] = settings.Model,
                    ["instructions"] = settings.Instructions,
                    ["tools"] = queryOrigin is null ? Array.Empty<object>() : new object[]
                    {
                        new
                        {
                            type = "function", name = "query_origin_agent",
                            description = "Ask your original assistant for current memory, facts, or substantive actions. Never guess tool outcomes.",
                            parameters = new
                            {
                                type = "object",
                                properties = new { question = new { type = "string" }, reason = new { type = "string" } },
                                required = new[] { "question", "reason" }, additionalProperties = false,
                            },
                        },
                    },
                    ["audio"] = new Dictionary<string, object?>
                    {
                        ["input"] = new Dictionary<string, object?>
                        {
                            ["format"] = new Dictionary<string, object?>
                            {
                                ["type"] = "audio/pcm",
                                ["rate"] = PcmAudio.RealtimeSampleRate,
                            },
                            ["transcription"] = new Dictionary<string, object?>
                            {
                                ["model"] = settings.TranscriptionModel,
                            },
                            ["turn_detection"] = new Dictionary<string, object?>
                            {
                                ["type"] = settings.TurnDetection,
                                // Native calls schedule caller turns and tool replies together.
                                ["create_response"] = queryOrigin is null,
                                ["interrupt_response"] = true,
                            },
                        },
                        ["output"] = new Dictionary<string, object?>
                        {
                            ["format"] = new Dictionary<string, object?>
                            {
                                ["type"] = "audio/pcm",
                                ["rate"] = PcmAudio.RealtimeSampleRate,
                            },
                            ["voice"] = settings.Voice,
                        },
                    },
                },
            };
            await SendRealtimeEventAsync(payload, cancellationToken);
        }

        private async Task SendRealtimeEventAsync(
            Dictionary<string, object?> payload,
            CancellationToken cancellationToken)
        {
            var json = JsonSerializer.Serialize(payload, JsonOptions);
            await sendLock.WaitAsync(cancellationToken);
            try
            {
                await socket.SendTextAsync(json, cancellationToken);
            }
            finally
            {
                sendLock.Release();
            }
        }

        private async Task RunReceiveLoopAsync(CancellationToken cancellationToken)
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                string? message;
                try
                {
                    message = await socket.ReceiveTextAsync(cancellationToken);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception ex)
                {
                    await onError($"OpenAI Realtime receive failed: {ex.Message}", CancellationToken.None);
                    return;
                }

                if (message is null)
                {
                    if (queryOrigin is not null && !cancellationToken.IsCancellationRequested)
                        await onError("OpenAI Realtime connection closed", CancellationToken.None);
                    return;
                }

                try
                {
                    await HandleRealtimeEventAsync(message, cancellationToken);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception ex)
                {
                    await onError($"OpenAI Realtime event handling failed: {ex.Message}", CancellationToken.None);
                }
            }
        }

        private async Task HandleRealtimeEventAsync(string message, CancellationToken cancellationToken)
        {
            using var document = JsonDocument.Parse(message);
            var root = document.RootElement;
            var type = OptionalString(root, "type");
            switch (type)
            {
                case "response.created":
                    CaptureResponseId(root);
                    if (queryOrigin is not null && callerSpeaking)
                        await CancelActiveResponseAsync("response_started_during_speech", cancellationToken);
                    break;
                case "response.output_audio.delta":
                case "response.audio.delta":
                    await HandleOutputAudioDeltaAsync(root, cancellationToken);
                    break;
                case "response.output_audio_transcript.done":
                case "response.audio_transcript.done":
                    await HandleAssistantTranscriptDoneAsync(root, cancellationToken);
                    break;
                case "conversation.item.input_audio_transcription.completed":
                case "conversation.item.input_audio_transcription.done":
                    await HandleInputTranscriptDoneAsync(root, cancellationToken);
                    break;
                case "input_audio_buffer.speech_started":
                    lock (activeResponseLock) callerSpeaking = true;
                    await CancelActiveResponseAsync("user_speech_started", cancellationToken);
                    break;
                case "input_audio_buffer.speech_stopped":
                    lock (activeResponseLock) callerSpeaking = false;
                    if (queryOrigin is not null) RequestResponse();
                    break;
                case "response.done":
                    await HandleResponseDoneAsync(root, cancellationToken);
                    break;
                case "response.function_call_arguments.done":
                    StartOriginQuery(root);
                    break;
                case "response.output_item.done":
                    if (OptionalObject(root, "item") is { } item
                        && OptionalString(item, "type") == "function_call") StartOriginQuery(item);
                    break;
                case "error":
                    await onError(ParseError(root), cancellationToken);
                    break;
            }
        }

        private void CaptureResponseId(JsonElement root)
        {
            var response = OptionalObject(root, "response");
            var responseId = response.HasValue
                ? OptionalString(response.Value, "id")
                : OptionalString(root, "response_id");
            lock (activeResponseLock)
            {
                activeResponseId = responseId;
                responseCreatePending = false;
                activeOperationId ??= string.IsNullOrWhiteSpace(responseId)
                    ? $"speech-{Guid.NewGuid():N}"
                    : $"realtime-{responseId}";
                activeResponseTranscriptCompleted = false;
            }
        }

        private async Task HandleOutputAudioDeltaAsync(
            JsonElement root,
            CancellationToken cancellationToken)
        {
            var delta = OptionalString(root, "delta");
            if (string.IsNullOrWhiteSpace(delta))
            {
                return;
            }

            var operationId = EnsureActiveOperation(root);
            var realtimePcm = Convert.FromBase64String(delta);
            var teamsPcm = PcmAudio.ResamplePcm16Mono(
                realtimePcm,
                PcmAudio.RealtimeSampleRate,
                PcmAudio.TeamsSampleRate);
            var frames = PcmAudio.ChunkTeamsFrames(teamsPcm);
            if (frames.Count > 0)
            {
                await audioSession.EnqueueAudioAsync(operationId, frames, cancellationToken);
            }
        }

        private async Task HandleAssistantTranscriptDoneAsync(
            JsonElement root,
            CancellationToken cancellationToken)
        {
            var transcript = OptionalString(root, "transcript") ?? OptionalString(root, "text");
            var operationId = EnsureActiveOperation(root);
            await onSpeechStatus(
                operationId,
                "completed",
                string.IsNullOrWhiteSpace(transcript) ? null : transcript.Trim(),
                new Dictionary<string, object> { ["source"] = "openai_realtime" },
                cancellationToken);
            lock (activeResponseLock)
            {
                activeResponseTranscriptCompleted = true;
            }
        }

        private async Task HandleInputTranscriptDoneAsync(
            JsonElement root,
            CancellationToken cancellationToken)
        {
            var transcript = OptionalString(root, "transcript") ?? OptionalString(root, "text");
            if (!string.IsNullOrWhiteSpace(transcript))
            {
                await onFinalTranscript(transcript.Trim(), cancellationToken);
            }
        }

        private async Task HandleResponseDoneAsync(
            JsonElement root,
            CancellationToken cancellationToken)
        {
            var response = OptionalObject(root, "response");
            var status = response.HasValue ? OptionalString(response.Value, "status") : null;
            var operationId = CurrentOperationId();
            var transcriptCompleted = ActiveTranscriptCompleted();
            if (string.Equals(status, "failed", StringComparison.OrdinalIgnoreCase))
            {
                await onError(ParseError(response ?? root), cancellationToken);
            }
            else if (operationId is not null
                && (!transcriptCompleted
                    || string.Equals(status, "cancelled", StringComparison.OrdinalIgnoreCase)))
            {
                await onSpeechStatus(
                    operationId,
                    string.Equals(status, "cancelled", StringComparison.OrdinalIgnoreCase)
                        ? "cancelled"
                        : "completed",
                    null,
                    new Dictionary<string, object> { ["source"] = "openai_realtime" },
                    cancellationToken);
            }

            lock (activeResponseLock)
            {
                activeResponseId = null;
                activeOperationId = null;
                activeResponseTranscriptCompleted = false;
                responseCreatePending = false;
                if (queryOrigin is not null) SignalResponseLoop();
            }
            if (response is { } value && value.TryGetProperty("output", out var output)
                && output.ValueKind == JsonValueKind.Array)
                foreach (var item in output.EnumerateArray())
                    if (OptionalString(item, "type") == "function_call") StartOriginQuery(item);
        }

        private void StartOriginQuery(JsonElement item)
        {
            var callId = OptionalString(item, "call_id");
            var name = OptionalString(item, "name");
            var arguments = OptionalString(item, "arguments") ?? "{}";
            if (string.IsNullOrWhiteSpace(callId) || !queries.TryAdd(callId, Task.CompletedTask)) return;
            // Do not block the receive loop: caller audio and interruptions continue during delegation.
            queries[callId] = RunOriginQueryAsync(callId, name, arguments, receiveLoopCts.Token);
        }

        private async Task RunOriginQueryAsync(string callId, string? name, string arguments, CancellationToken token)
        {
            string result;
            try
            {
                if (queryOrigin is null || name != "query_origin_agent")
                    throw new InvalidOperationException("Unknown voice tool.");
                using var parsed = JsonDocument.Parse(arguments);
                var question = OptionalString(parsed.RootElement, "question");
                if (string.IsNullOrWhiteSpace(question)) throw new ArgumentException("question is required");
                var reason = OptionalString(parsed.RootElement, "reason") ?? "";
                result = await queryOrigin(question, reason, callId, token);
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { return; }
            catch (Exception)
            {
                result = "{\"status\":\"failed\",\"message\":\"No assistant result confirmed. Do not claim work succeeded.\"}";
            }
            try
            {
                await SendRealtimeEventAsync(new Dictionary<string, object?>
                {
                    ["type"] = "conversation.item.create",
                    ["item"] = new { type = "function_call_output", call_id = callId, output = result },
                }, token);
                RequestResponse();
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { }
            catch (Exception ex) { await onError($"Origin query reply failed: {ex.GetType().Name}", CancellationToken.None); }
        }

        private void RequestResponse()
        {
            lock (activeResponseLock)
            {
                responseRequested = true;
                SignalResponseLoop();
            }
        }

        // Called under activeResponseLock; the signal coalesces pending caller turns/tool results.
        private void SignalResponseLoop()
        {
            if (responseSignal.CurrentCount == 0) responseSignal.Release();
        }

        private async Task RunResponseLoopAsync(CancellationToken token)
        {
            try
            {
                while (true)
                {
                    await responseSignal.WaitAsync(token);
                    lock (activeResponseLock)
                    {
                        if (!responseRequested || callerSpeaking || responseCreatePending
                            || activeOperationId is not null) continue;
                        responseRequested = false;
                        responseCreatePending = true;
                    }
                    await SendRealtimeEventAsync(new Dictionary<string, object?> { ["type"] = "response.create" }, token);
                }
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { }
            catch (Exception ex) { await onError($"Voice response failed: {ex.GetType().Name}", CancellationToken.None); }
        }

        private async Task CancelActiveResponseAsync(string reason, CancellationToken cancellationToken)
        {
            var operationId = CurrentOperationId();
            // Audio can still be queued after response.done has cleared the operation ID.
            await audioSession.ClearPendingAudioAsync(cancellationToken);
            if (operationId is not null)
            {
                string? responseId;
                lock (activeResponseLock) responseId = activeResponseId;
                // VAD's interrupt_response already cancels the server response on caller speech.
                if (reason != "user_speech_started" && responseId is not null)
                    await SendRealtimeEventAsync(
                        new Dictionary<string, object?> { ["type"] = "response.cancel", ["response_id"] = responseId },
                        cancellationToken);
                await onSpeechStatus(
                    operationId,
                    "interrupted",
                    null,
                    new Dictionary<string, object>
                    {
                        ["source"] = "openai_realtime",
                        ["reason"] = reason,
                    },
                    cancellationToken);
            }

            lock (activeResponseLock)
            {
                // A native response still occupies the server conversation until response.done,
                // even when VAD has cancelled it or response.created is still in flight.
                if (queryOrigin is null)
                {
                    activeResponseId = null;
                    activeOperationId = null;
                    activeResponseTranscriptCompleted = false;
                }
            }
        }

        private string EnsureActiveOperation(JsonElement root)
        {
            lock (activeResponseLock)
            {
                if (!string.IsNullOrWhiteSpace(activeOperationId))
                {
                    return activeOperationId;
                }

                var responseId = OptionalString(root, "response_id")
                    ?? OptionalString(OptionalObject(root, "response") ?? root, "id");
                activeOperationId = string.IsNullOrWhiteSpace(responseId)
                    ? $"speech-{Guid.NewGuid():N}"
                    : $"realtime-{responseId}";
                activeResponseId = responseId;
                activeResponseTranscriptCompleted = false;
                return activeOperationId;
            }
        }

        private string? CurrentOperationId()
        {
            lock (activeResponseLock)
            {
                return activeOperationId;
            }
        }

        private bool ActiveTranscriptCompleted()
        {
            lock (activeResponseLock)
            {
                return activeResponseTranscriptCompleted;
            }
        }

        private static string ParseError(JsonElement root)
        {
            var error = OptionalObject(root, "error");
            if (error.HasValue)
            {
                return OptionalString(error.Value, "message")
                    ?? OptionalString(error.Value, "code")
                    ?? "OpenAI Realtime error";
            }

            return OptionalString(root, "message")
                ?? OptionalString(root, "status_details")
                ?? "OpenAI Realtime error";
        }

        private static JsonElement? OptionalObject(JsonElement element, string propertyName)
        {
            return element.ValueKind == JsonValueKind.Object
                && element.TryGetProperty(propertyName, out var value)
                && value.ValueKind == JsonValueKind.Object
                ? value
                : null;
        }

        private static string? OptionalString(JsonElement element, string propertyName)
        {
            if (element.ValueKind != JsonValueKind.Object
                || !element.TryGetProperty(propertyName, out var value))
            {
                return null;
            }

            return value.ValueKind switch
            {
                JsonValueKind.String => value.GetString(),
                JsonValueKind.Number => value.GetRawText(),
                JsonValueKind.True => "true",
                JsonValueKind.False => "false",
                _ => null,
            };
        }
    }

    private sealed class OpenAIRealtimeSessionSettings
    {
        private OpenAIRealtimeSessionSettings(
            string apiKey,
            Uri webSocketUri,
            string model,
            string voice,
            string turnDetection,
            string transcriptionModel,
            string instructions)
        {
            ApiKey = apiKey;
            WebSocketUri = webSocketUri;
            Model = model;
            Voice = voice;
            TurnDetection = turnDetection;
            TranscriptionModel = transcriptionModel;
            Instructions = instructions;
        }

        public string ApiKey { get; }

        public Uri WebSocketUri { get; }

        public string Model { get; }

        public string Voice { get; }

        public string TurnDetection { get; }

        public string TranscriptionModel { get; }

        public string Instructions { get; }

        public static OpenAIRealtimeSessionSettings From(
            OpenAIRealtimeOptions options,
            MeetingSpeechStartRequest request)
        {
            var model = FirstString(request.Realtime, "model") ?? options.Model;
            var voice = FirstString(request.Realtime, "voice") ?? options.DefaultVoice;
            var turnDetection = FirstString(request.Realtime, "turn_detection") ?? options.TurnDetection;
            var transcriptionModel = FirstString(request.Realtime, "transcription_model")
                ?? options.TranscriptionModel;
            return new OpenAIRealtimeSessionSettings(
                options.ApiKey,
                BuildRealtimeUri(options.BaseUrl, model),
                model,
                voice,
                turnDetection,
                transcriptionModel,
                BuildInstructions(request));
        }

        private static Uri BuildRealtimeUri(Uri baseUrl, string model)
        {
            var builder = new UriBuilder(baseUrl);
            var query = builder.Query.TrimStart('?');
            var modelParameter = $"model={Uri.EscapeDataString(model)}";
            builder.Query = QueryContainsKey(query, "model")
                ? query
                : string.IsNullOrWhiteSpace(query)
                    ? modelParameter
                    : $"{query}&{modelParameter}";
            return builder.Uri;
        }

        private static bool QueryContainsKey(string query, string key)
        {
            return query
                .Split('&', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Select(part => part.Split('=', 2)[0])
                .Any(part => string.Equals(Uri.UnescapeDataString(part), key, StringComparison.Ordinal));
        }

        private static string BuildInstructions(MeetingSpeechStartRequest request)
        {
            var prompt = FirstString(request.Projection, "prompt")
                ?? FirstString(request.Projection, "instructions");
            var builder = new StringBuilder();
            if (string.IsNullOrWhiteSpace(prompt))
            {
                builder.AppendLine(
                    "You are a live Evander agent in a Microsoft Teams meeting. Listen to participants, respond concisely, and preserve meeting decisions for the originating text session.");
            }
            else
            {
                builder.AppendLine(prompt.Trim());
            }

            if (request.Projection is not null && request.Projection.Count > 0)
            {
                builder.AppendLine();
                builder.AppendLine("Projection metadata:");
                builder.AppendLine(JsonSerializer.Serialize(request.Projection));
            }

            return builder.ToString().Trim();
        }

        private static string? FirstString(IReadOnlyDictionary<string, object>? values, string key)
        {
            if (values is null || !values.TryGetValue(key, out var value) || value is null)
            {
                return null;
            }

            if (value is JsonElement element)
            {
                return element.ValueKind switch
                {
                    JsonValueKind.String => Clean(element.GetString()),
                    JsonValueKind.Number => element.GetRawText(),
                    JsonValueKind.True => "true",
                    JsonValueKind.False => "false",
                    _ => null,
                };
            }

            return Clean(Convert.ToString(value));
        }

        private static string? Clean(string? value)
        {
            return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
        }
    }
}

internal sealed class ClientWebSocketRealtimeClient : IRealtimeWebSocketClient
{
    private readonly ClientWebSocket socket = new();

    public async Task ConnectAsync(
        Uri uri,
        IReadOnlyDictionary<string, string> headers,
        CancellationToken cancellationToken)
    {
        foreach (var header in headers)
        {
            socket.Options.SetRequestHeader(header.Key, header.Value);
        }

        await socket.ConnectAsync(uri, cancellationToken);
    }

    public async Task SendTextAsync(string text, CancellationToken cancellationToken)
    {
        var bytes = Encoding.UTF8.GetBytes(text);
        await socket.SendAsync(
            new ArraySegment<byte>(bytes),
            WebSocketMessageType.Text,
            endOfMessage: true,
            cancellationToken);
    }

    public async Task<string?> ReceiveTextAsync(CancellationToken cancellationToken)
    {
        var buffer = new byte[8192];
        using var message = new MemoryStream();
        while (true)
        {
            var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), cancellationToken);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                return null;
            }

            if (result.Count > 0)
            {
                message.Write(buffer, 0, result.Count);
            }

            if (result.EndOfMessage)
            {
                return Encoding.UTF8.GetString(message.ToArray());
            }
        }
    }

    public async Task CloseAsync(CancellationToken cancellationToken)
    {
        if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
        {
            await socket.CloseAsync(
                WebSocketCloseStatus.NormalClosure,
                "closing",
                cancellationToken);
        }
    }

    public ValueTask DisposeAsync()
    {
        socket.Dispose();
        return ValueTask.CompletedTask;
    }
}
