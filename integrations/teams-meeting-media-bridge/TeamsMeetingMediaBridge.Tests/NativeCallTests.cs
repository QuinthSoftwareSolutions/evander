using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Tokens;
using TeamsMeetingMediaBridge;

internal static class NativeCallTests
{
    private const string Tenant = "11111111-1111-4111-8111-111111111111";
    private const string Caller = "22222222-2222-4222-8222-222222222222";

    public static async Task CommunicationsAuthentication()
    {
        using var rsa = RSA.Create(2048);
        var key = new RsaSecurityKey(rsa) { KeyId = "synthetic-signing-key" };
        string Mint(string issuer = "https://graph.microsoft.com", string audience = "app",
            string? tenant = Tenant, bool expired = false) => new JsonWebTokenHandler().CreateToken(new SecurityTokenDescriptor
            {
                Issuer = issuer, Audience = audience,
                Claims = tenant is null ? new Dictionary<string, object>() : new() { ["tid"] = tenant },
                NotBefore = DateTime.UtcNow.AddMinutes(-20),
                Expires = DateTime.UtcNow.AddMinutes(expired ? -10 : 10),
                SigningCredentials = new SigningCredentials(key, SecurityAlgorithms.RsaSha256),
            });
        Check((await InboundCallTokenValidator.ValidateAsync(Mint(), [key], "app", Tenant)).IsValid, "Graph signing token rejected");
        Check((await InboundCallTokenValidator.ValidateAsync(Mint(issuer: "https://api.botframework.com"), [key], "app", Tenant)).IsValid, "BotFramework signing token rejected");
        foreach (var token in new[] { Mint(issuer: "https://login.microsoftonline.com/tenant/v2.0"),
            Mint(audience: "other-app"), Mint(tenant: null), Mint(tenant: Caller), Mint(expired: true) })
            Check(!(await InboundCallTokenValidator.ValidateAsync(token, [key], "app", Tenant)).IsValid, "Invalid notification accepted");
        using var wrongRsa = RSA.Create(2048);
        Check(!(await InboundCallTokenValidator.ValidateAsync(Mint(), [new RsaSecurityKey(wrongRsa)], "app", Tenant)).IsValid, "Wrong signature accepted");
    }

    public static async Task RealtimeDelegationAndInterruption()
    {
        var socket = new FakeRealtimeWebSocketClient();
        var audio = new FakeMeetingAudioSession();
        var result = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        var requested = 0;
        var transcripts = 0;
        var adapter = Adapter(socket);
        await using var session = await adapter.StartAsync(new MeetingSpeechStartRequest(
            "voice-bound", null, audio, null, null, null,
            (_, _) => { Interlocked.Increment(ref transcripts); return Task.CompletedTask; },
            (error, _) => throw new InvalidOperationException(error),
            (_, _, _, _, _) => Task.CompletedTask,
            (question, reason, requestId, token) =>
            {
                Check(question == "Prepare the report" && requestId == "function-1", "Delegated query changed");
                Interlocked.Increment(ref requested);
                return result.Task.WaitAsync(token);
            }), CancellationToken.None);
        using (var update = JsonDocument.Parse(socket.SentMessages[0]))
        {
            var tools = update.RootElement.GetProperty("session").GetProperty("tools");
            Check(tools.GetArrayLength() == 1 && tools[0].GetProperty("name").GetString() == "query_origin_agent", "Unexpected voice tools");
        }
        var function = new { type = "function_call", name = "query_origin_agent", call_id = "function-1",
            arguments = "{\"question\":\"Prepare the report\",\"reason\":\"action\"}" };
        socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "response.output_item.done", item = function }));
        socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "response.done", response = new { status = "completed", output = new[] { function } } }));
        socket.EnqueueIncoming("{\"type\":\"response.created\",\"response\":{\"id\":\"interrupt-response\"}}");
        socket.EnqueueIncoming("{\"type\":\"conversation.item.input_audio_transcription.completed\",\"transcript\":\"Wait\"}");
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_started\"}");
        await Until(() => audio.ClearCount == 1 && transcripts == 1);
        Check(requested == 1, "Replayed function executed twice");
        Check(!socket.SentMessages.Any(message => message.Contains("function_call_output")), "Query invented an early result");
        result.SetResult("{\"status\":\"answered\",\"answer\":\"Prepared\"}");
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_stopped\"}");
        await Until(() => socket.SentMessages.Any(message => message.Contains("function_call_output")));
        using var reply = JsonDocument.Parse(socket.SentMessages.Single(message => message.Contains("function_call_output")));
        Check(reply.RootElement.GetProperty("item").GetProperty("call_id").GetString() == "function-1", "Result correlated to wrong function");
        socket.EnqueueIncoming("{\"type\":\"response.done\",\"response\":{\"status\":\"completed\",\"output\":[]}}");
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_started\"}");
        await Until(() => audio.ClearCount == 2);
    }

    public static async Task HangupStopsQueryPolling()
    {
        var socket = new FakeRealtimeWebSocketClient { StallClose = true };
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var session = await Adapter(socket).StartAsync(new MeetingSpeechStartRequest(
            "voice-bound", null, new FakeMeetingAudioSession(), null, null, null,
            (_, _) => Task.CompletedTask, (_, _) => Task.CompletedTask,
            (_, _, _, _, _) => Task.CompletedTask,
            async (_, _, _, token) =>
            {
                started.SetResult();
                try { await Task.Delay(Timeout.Infinite, token); }
                catch (OperationCanceledException) { cancelled.SetResult(); throw; }
                return "unreachable";
            }), CancellationToken.None);
        socket.EnqueueIncoming("{\"type\":\"response.function_call_arguments.done\",\"name\":\"query_origin_agent\",\"call_id\":\"function-1\",\"arguments\":\"{\\\"question\\\":\\\"Report\\\"}\"}");
        await started.Task.WaitAsync(TimeSpan.FromSeconds(3));
        await session.DisposeAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(4));
        await cancelled.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Check(socket.Disposed, "Unresponsive websocket kept call resources after hangup");
    }

    public static async Task ConcurrentQueryResultsSerializeResponses()
    {
        var socket = new FakeRealtimeWebSocketClient();
        await using var session = await Adapter(socket).StartAsync(new MeetingSpeechStartRequest(
            "voice-bound", null, new FakeMeetingAudioSession(), null, null, null,
            (_, _) => Task.CompletedTask, (_, _) => Task.CompletedTask,
            (_, _, _, _, _) => Task.CompletedTask,
            (_, _, _, _) => Task.FromResult("confirmed")), CancellationToken.None);
        foreach (var callId in new[] { "function-1", "function-2" })
            socket.EnqueueIncoming(JsonSerializer.Serialize(new
            {
                type = "response.function_call_arguments.done", name = "query_origin_agent", call_id = callId,
                arguments = "{\"question\":\"Report\"}",
            }));
        int Responses() => socket.SentMessages.Count(message => message.Contains("response.create"));
        await Until(() => socket.SentMessages.Count(message => message.Contains("function_call_output")) == 2);
        Check(Responses() == 1, "Concurrent tools started overlapping responses");
        socket.EnqueueIncoming("{\"type\":\"response.created\",\"response\":{\"id\":\"first-response\"}}");
        socket.EnqueueIncoming("{\"type\":\"response.done\",\"response\":{\"status\":\"completed\",\"output\":[]}}");
        await Until(() => Responses() == 2);
    }

    public static async Task NativeLifecycleAndReplay()
    {
        var plane = new FakePlane();
        var speech = new FakeSpeech();
        var call = new FakeCall();
        var coordinator = new NativeCallCoordinator(plane, speech, NullLogger<NativeCallCoordinator>.Instance);
        var running = coordinator.HandleAsync(call);
        Check(ReferenceEquals(running, coordinator.HandleAsync(call)), "Call replay starts another lifecycle");
        await Until(() => speech.Request is not null);
        Check(plane.Admissions == 1 && call.Answers == 1 && call.Recording, "Call admission or recording was skipped");
        await speech.Request!.OnFinalTranscript("Prepare report", CancellationToken.None);
        await speech.Request.OnSpeechStatus("speech", "completed", "Prepared", null, CancellationToken.None);
        await speech.Request.QueryOriginAgent!("Prepare report", "action", "function", CancellationToken.None);
        Check(plane.QueryVoiceId == "voice-selected", "Query lost the admitted binding");
        call.End.SetResult();
        await running.WaitAsync(TimeSpan.FromSeconds(3));
        Check(speech.Disposed && plane.Completions == 1, "Call resources or completion leaked");
        var snapshot = coordinator.GetSnapshot("voice-selected")!;
        Check(snapshot.Status == "ended" && snapshot.TranscriptEvents.Count == 2, "Terminal snapshot unavailable");
        Check(snapshot.TranscriptEvents.Select(item => item.Speaker).SequenceEqual(new[] { "caller", "assistant" }), "Speaker context lost");
    }

    public static async Task RejectedCallsNeverStartSpeech()
    {
        foreach (var groupCall in new[] { false, true })
        {
            var plane = new FakePlane { Deny = true };
            var speech = new FakeSpeech();
            var call = new FakeCall { Personal = !groupCall };
            var coordinator = new NativeCallCoordinator(plane, speech, NullLogger<NativeCallCoordinator>.Instance);
            await coordinator.HandleAsync(call).WaitAsync(TimeSpan.FromSeconds(3));
            Check(call.Rejections == 1 && call.Answers == 0 && speech.Request is null, "Declined call opened media");
            if (groupCall) Check(plane.Admissions == 0, "Group call reached personal assistant admission");
        }
    }

    public static async Task RecordingFailurePreventsNativeTranscription()
    {
        var plane = new FakePlane();
        var speech = new FakeSpeech();
        var call = new FakeCall { FailRecording = true };
        var coordinator = new NativeCallCoordinator(plane, speech, NullLogger<NativeCallCoordinator>.Instance);
        await coordinator.HandleAsync(call).WaitAsync(TimeSpan.FromSeconds(3));
        var snapshot = coordinator.GetSnapshot("voice-selected")!;
        Check(speech.Request is null && snapshot.TranscriptEvents.Count == 0 && snapshot.Status == "failed", "Transcription started without recording status");
    }

    public static async Task PlaybackFailureEndsNativeCall()
    {
        var plane = new FakePlane();
        var speech = new FakeSpeech { FailDisposal = true };
        var audio = new FailedPlaybackAudio();
        var call = new FakeCall { Audio = audio };
        var coordinator = new NativeCallCoordinator(plane, speech, NullLogger<NativeCallCoordinator>.Instance);
        await coordinator.HandleAsync(call).WaitAsync(TimeSpan.FromSeconds(3));
        Check(coordinator.GetSnapshot("voice-selected")!.Status == "failed", "Broken playback left call active");
        Check(call.Rejections == 1 && speech.Disposed && audio.Disposed && plane.Completions == 1,
            "Broken playback did not release and reconcile the call");
    }

    public static async Task ControlPlaneRetryPreservesRequestId()
    {
        var handler = new RetryHandler();
        var client = new ControlPlaneVoiceClient(new HttpClient(handler), new Uri("https://control.example.test/"), "synthetic-token");
        var reply = await client.QueryAsync("voice-bound", "Report", "action", "function-1", CancellationToken.None);
        Check(handler.RequestIds.SequenceEqual(new[] { "function-1", "function-1" }), "Retry duplicated query identity");
        Check(reply.Contains("Prepared"), "Confirmed query response lost");
    }

    public static async Task ControlPlaneHonorsStoredDeadlineAndPermanentDenial()
    {
        var expired = new ExpiredQueryHandler();
        var client = new ControlPlaneVoiceClient(new HttpClient(expired), new Uri("https://control.example.test/"), "synthetic-token");
        var reply = await client.QueryAsync("voice-bound", "Report", "action", "function-1", CancellationToken.None)
            .WaitAsync(TimeSpan.FromSeconds(3));
        Check(expired.Timeouts == 1 && reply.Contains("Accepted work may continue"), "Retry extended the durable query deadline");
        expired.AnswerOnTimeout = true;
        var confirmed = await client.QueryAsync("voice-bound", "Report", "action", "function-1", CancellationToken.None);
        Check(confirmed.Contains("Prepared"), "Timeout lost a concurrently confirmed answer");

        var denied = new DeniedQueryHandler();
        var deniedClient = new ControlPlaneVoiceClient(new HttpClient(denied), new Uri("https://control.example.test/"), "synthetic-token");
        try
        {
            await deniedClient.QueryAsync("voice-bound", "Report", "action", "function-1", CancellationToken.None);
            throw new InvalidOperationException("Permanent denial was accepted");
        }
        catch (HttpRequestException ex) when (ex.StatusCode == HttpStatusCode.Forbidden) { }
        Check(denied.Requests == 1, "Permanent authorization failure was retried");
    }

    private static OpenAIRealtimeSpeechAdapter Adapter(FakeRealtimeWebSocketClient socket) => new(
        new OpenAIRealtimeOptions("synthetic-key", new Uri("wss://api.example.test/realtime"),
            "gpt-realtime-2", "marin", "semantic_vad", "gpt-4o-transcribe"), new FakeRealtimeWebSocketClientFactory(socket));

    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        while (!condition()) await Task.Delay(10, timeout.Token);
    }
    private static void Check(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }

    private sealed class FakePlane : IControlPlaneVoiceClient
    {
        public int Admissions, Completions;
        public string? QueryVoiceId;
        public bool Deny;
        public Task<TeamsCallAdmission> AdmitAsync(string tenant, string caller, string call, CancellationToken token)
        {
            Admissions++;
            if (Deny) throw new InvalidOperationException("Unmapped caller");
            return Task.FromResult(new TeamsCallAdmission("voice-selected", "connecting", new(), new()));
        }
        public Task<string> QueryAsync(string voiceSessionId, string question, string reason, string requestId, CancellationToken token)
        {
            QueryVoiceId = voiceSessionId;
            return Task.FromResult("confirmed");
        }
        public Task CompleteAsync(string voiceSessionId, NativeCallSnapshot snapshot, CancellationToken token)
        { Completions++; return Task.CompletedTask; }
    }
    private sealed class FakeCall : IIncomingTeamsCall
    {
        public int Answers, Rejections;
        public bool Personal = true, Recording, FailRecording;
        public IMeetingAudioSession Audio = new FakeMeetingAudioSession();
        public TaskCompletionSource End = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public string CallId => "graph-call";
        public string TenantId => Tenant;
        public string CallerUserId => Caller;
        public bool IsPersonalAudioCall => Personal;
        public Task Ended => End.Task;
        public Task<IMeetingAudioSession> AnswerAsync(CancellationToken token)
        { Answers++; return Task.FromResult(Audio); }
        public Task SetRecordingAsync(bool recording, CancellationToken token)
        {
            if (FailRecording) throw new InvalidOperationException("Recording status failed");
            Check(Answers == 1, "Recording before call answer"); Recording = recording;
            return Task.CompletedTask;
        }
        public Task RejectAsync(CancellationToken token) { Rejections++; return Task.CompletedTask; }
    }
    private sealed class FakeSpeech : IMeetingSpeechAdapter, IMeetingSpeechSession
    {
        public MeetingSpeechStartRequest? Request;
        public bool Disposed, FailDisposal;
        public Task<IMeetingSpeechSession> StartAsync(MeetingSpeechStartRequest request, CancellationToken token)
        { Request = request; return Task.FromResult<IMeetingSpeechSession>(this); }
        public Task PushAudioAsync(byte[] audio, CancellationToken token) => Task.CompletedTask;
        public Task<SpeechPlaybackResult> SpeakAsync(string text, string? voice, bool interrupt, CancellationToken token)
            => Task.FromResult(new SpeechPlaybackResult("queued", 0));
        public ValueTask DisposeAsync()
        {
            Disposed = true;
            return FailDisposal ? ValueTask.FromException(new IOException("Speech cleanup failed")) : ValueTask.CompletedTask;
        }
    }
    private sealed class RetryHandler : HttpMessageHandler
    {
        public List<string> RequestIds = new();
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Check(request.Headers.Authorization?.ToString() == "Bearer synthetic-token", "Controller authentication missing");
            Check(request.RequestUri!.AbsolutePath == "/voice-sessions/voice-bound/queries", "Query lost binding");
            using var payload = JsonDocument.Parse(await request.Content!.ReadAsStringAsync(token));
            RequestIds.Add(payload.RootElement.GetProperty("request_id").GetString()!);
            return RequestIds.Count == 1 ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
                : new HttpResponseMessage(HttpStatusCode.Created) { Content = new StringContent("{\"query_id\":\"query-1\",\"status\":\"answered\",\"answer\":\"Prepared\"}", System.Text.Encoding.UTF8, "application/json") };
        }
    }

    private sealed class FailedPlaybackAudio : IMeetingAudioSession, IPlaybackMonitoredAudioSession
    {
        public bool Disposed;
        public event EventHandler<byte[]>? AudioReceived { add { } remove { } }
        public Task PlaybackCompletion => Task.FromException(new IOException("Playback socket lost"));
        public Task EnqueueAudioAsync(string operationId, IReadOnlyList<byte[]> frames, CancellationToken token) => Task.CompletedTask;
        public Task ClearPendingAudioAsync(CancellationToken token) => Task.CompletedTask;
        public ValueTask DisposeAsync() { Disposed = true; return ValueTask.CompletedTask; }
    }

    private sealed class ExpiredQueryHandler : HttpMessageHandler
    {
        public int Timeouts;
        public bool AnswerOnTimeout;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Check(request.Method == HttpMethod.Post, "Expired durable query was polled again");
            var isTimeout = request.RequestUri!.AbsolutePath.EndsWith("/query-1/timeout");
            if (isTimeout) Timeouts++;
            else Check(request.RequestUri.AbsolutePath.EndsWith("/queries"), "Unexpected query request");
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(isTimeout
                    ? AnswerOnTimeout ? "{\"status\":\"answered\",\"answer\":\"Prepared\"}" : "{\"status\":\"timeout\"}"
                    : JsonSerializer.Serialize(new
                {
                    query_id = "query-1", status = "pending", deadline_at = DateTimeOffset.UtcNow.AddSeconds(-1),
                }), System.Text.Encoding.UTF8, "application/json"),
            });
        }
    }

    private sealed class DeniedQueryHandler : HttpMessageHandler
    {
        public int Requests;
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Requests++;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Forbidden));
        }
    }
}
