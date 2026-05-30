using System.Net.WebSockets;
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
            request.OnSpeechStatus);
        await session.StartAsync(cancellationToken);
        return session;
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
        private readonly CancellationTokenSource receiveLoopCts = new();
        private readonly SemaphoreSlim sendLock = new(1, 1);
        private readonly object activeResponseLock = new();
        private Task? receiveLoopTask;
        private string? activeResponseId;
        private string? activeOperationId;
        private bool activeResponseTranscriptCompleted;
        private bool disposed;

        public OpenAIRealtimeMeetingSpeechSession(
            OpenAIRealtimeSessionSettings settings,
            IRealtimeWebSocketClient socket,
            IMeetingAudioSession audioSession,
            Func<string, CancellationToken, Task> onFinalTranscript,
            Func<string, CancellationToken, Task> onError,
            SpeechStatusCallback onSpeechStatus)
        {
            this.settings = settings;
            this.socket = socket;
            this.audioSession = audioSession;
            this.onFinalTranscript = onFinalTranscript;
            this.onError = onError;
            this.onSpeechStatus = onSpeechStatus;
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
                await socket.CloseAsync(CancellationToken.None);
            }
            catch
            {
                // Best effort close; disposal should not hide the meeting stop path.
            }

            if (receiveLoopTask is not null)
            {
                try
                {
                    await receiveLoopTask.WaitAsync(TimeSpan.FromSeconds(2));
                }
                catch
                {
                    // The close path above is already best effort.
                }
            }

            await socket.DisposeAsync();
            receiveLoopCts.Dispose();
            sendLock.Dispose();
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
                                ["create_response"] = true,
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
                    await CancelActiveResponseAsync("user_speech_started", cancellationToken);
                    break;
                case "response.done":
                    await HandleResponseDoneAsync(root, cancellationToken);
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
            }
        }

        private async Task CancelActiveResponseAsync(string reason, CancellationToken cancellationToken)
        {
            var operationId = CurrentOperationId();
            if (operationId is not null)
            {
                await SendRealtimeEventAsync(
                    new Dictionary<string, object?> { ["type"] = "response.cancel" },
                    cancellationToken);
                await audioSession.ClearPendingAudioAsync(cancellationToken);
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
                activeResponseId = null;
                activeOperationId = null;
                activeResponseTranscriptCompleted = false;
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
                    "You are a live Truman agent in a Microsoft Teams meeting. Listen to participants, respond concisely, and preserve meeting decisions for the originating text session.");
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
