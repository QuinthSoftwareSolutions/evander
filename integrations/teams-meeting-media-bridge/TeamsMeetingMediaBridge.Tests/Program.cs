using System.Text.Json;
using Microsoft.Extensions.Configuration;
using TeamsMeetingMediaBridge;

var tests = new (string Name, Func<Task> Run)[]
{
    ("communications callback authentication", NativeCallTests.CommunicationsAuthentication),
    ("realtime delegation permits interruption and deduplicates functions", NativeCallTests.RealtimeDelegationAndInterruption),
    ("hangup stops query polling", NativeCallTests.HangupStopsQueryPolling),
    ("concurrent delegated results serialize responses", NativeCallTests.ConcurrentQueryResultsSerializeResponses),
    ("native call binding lifecycle and replay", NativeCallTests.NativeLifecycleAndReplay),
    ("rejected calls never start speech", NativeCallTests.RejectedCallsNeverStartSpeech),
    ("native recording failure prevents transcription", NativeCallTests.RecordingFailurePreventsNativeTranscription),
    ("native playback failure releases and reconciles call", NativeCallTests.PlaybackFailureEndsNativeCall),
    ("control plane retries preserve request identity", NativeCallTests.ControlPlaneRetryPreservesRequestId),
    ("control plane preserves stored deadline and permanent denial", NativeCallTests.ControlPlaneHonorsStoredDeadlineAndPermanentDenial),
    ("adapter selection defaults to in-memory", AdapterSelectionDefaultsToInMemory),
    ("in-memory mode honors configured default voice", InMemoryModeHonorsConfiguredDefaultVoice),
    ("graph mode validates required config", GraphModeValidatesRequiredConfig),
    ("graph mode can select OpenAI Realtime speech", GraphModeCanSelectOpenAIRealtimeSpeech),
    ("join request accepts projection and realtime metadata", JoinRequestAcceptsProjectionAndRealtimeMetadata),
    ("join records active meeting state", JoinRecordsActiveMeetingState),
    ("transcript events are emitted after cursor", TranscriptEventsAreEmittedAfterCursor),
    ("speak synthesizes and injects audio", SpeakSynthesizesAndInjectsAudio),
    ("interrupt clears pending speech queue", InterruptClearsPendingSpeechQueue),
    ("graph join resolves url and chunks synthesized audio", GraphJoinResolvesUrlAndChunksSynthesizedAudio),
    ("realtime adapter streams audio and transcripts over websocket", RealtimeAdapterStreamsAudioAndTranscriptsOverWebSocket),
    ("recording status failure prevents transcript persistence", RecordingStatusFailurePreventsTranscriptPersistence),
    ("leave marks meeting stopped and disposes live resources", LeaveMarksMeetingStoppedAndDisposesResources),
};

foreach (var test in tests)
{
    await test.Run();
    Console.WriteLine($"PASS {test.Name}");
}

static Task AdapterSelectionDefaultsToInMemory()
{
    var originalDefaultVoice = Environment.GetEnvironmentVariable("AZURE_SPEECH_DEFAULT_VOICE");
    Environment.SetEnvironmentVariable("AZURE_SPEECH_DEFAULT_VOICE", null);
    try
    {
        var options = BridgeOptions.FromConfiguration(new ConfigurationBuilder().Build());

        AssertEqual(BridgeAdapterMode.InMemory, options.AdapterMode);
        AssertEqual("en-US-JennyNeural", options.DefaultVoice);
    }
    finally
    {
        Environment.SetEnvironmentVariable("AZURE_SPEECH_DEFAULT_VOICE", originalDefaultVoice);
    }

    return Task.CompletedTask;
}

static Task InMemoryModeHonorsConfiguredDefaultVoice()
{
    var config = new ConfigurationBuilder()
        .AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["AzureSpeech:DefaultVoice"] = "en-US-AvaNeural",
        })
        .Build();

    var options = BridgeOptions.FromConfiguration(config);

    AssertEqual(BridgeAdapterMode.InMemory, options.AdapterMode);
    AssertEqual("en-US-AvaNeural", options.DefaultVoice);
    AssertEqual(null, options.AzureSpeech);
    return Task.CompletedTask;
}

static Task GraphModeValidatesRequiredConfig()
{
    var config = new ConfigurationBuilder()
        .AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["Bridge:AdapterMode"] = "Graph",
        })
        .Build();

    AssertThrows<InvalidOperationException>(() => BridgeOptions.FromConfiguration(config));
    return Task.CompletedTask;
}

static Task GraphModeCanSelectOpenAIRealtimeSpeech()
{
    var values = RequiredGraphConfig();
    values["Bridge:SpeechMode"] = "OpenAIRealtime";
    values["OpenAIRealtime:ApiKey"] = "sk-test";
    var config = new ConfigurationBuilder()
        .AddInMemoryCollection(values)
        .Build();

    var options = BridgeOptions.FromConfiguration(config);

    AssertEqual(BridgeAdapterMode.Graph, options.AdapterMode);
    AssertEqual(BridgeSpeechMode.OpenAIRealtime, options.SpeechMode);
    AssertEqual("gpt-realtime-2", options.OpenAIRealtime?.Model);
    AssertEqual("marin", options.DefaultVoice);
    AssertEqual(null, options.AzureSpeech);
    return Task.CompletedTask;
}

static Task JoinRequestAcceptsProjectionAndRealtimeMetadata()
{
    var request = JsonSerializer.Deserialize<JoinMeetingRequest>(
        """
        {
          "join_url": "https://teams.example/join",
          "external_meeting_id": "meeting-a",
          "projection": {"origin_session_id": "origin-a", "prompt": "Represent the agent."},
          "realtime": {"model": "gpt-realtime-2", "voice": "marin"}
        }
        """);

    if (request is null)
    {
        throw new InvalidOperationException("Join request did not deserialize.");
    }

    AssertEqual("origin-a", JsonObjectString(request.Projection, "origin_session_id"));
    AssertEqual("gpt-realtime-2", JsonObjectString(request.Realtime, "model"));
    AssertEqual("marin", JsonObjectString(request.Realtime, "voice"));
    return Task.CompletedTask;
}

static async Task JoinRecordsActiveMeetingState()
{
    var media = new RecordingMediaAdapter();
    var coordinator = new MeetingCoordinator(media, new RecordingSpeechAdapter(), "en-US-Test");

    var response = await coordinator.JoinAsync(
        new JoinMeetingRequest(
            "https://teams.example/join",
            "meeting-a",
            "en-US-AvaNeural",
            new Dictionary<string, object>()),
        CancellationToken.None);

    AssertEqual("meeting-a", response.MeetingId);
    AssertEqual("active", coordinator.GetState("meeting-a").Status);
    AssertEqual("meeting-a", media.JoinedMeetingIds.Single());
    AssertEqual("meeting_status", coordinator.GetEvents("meeting-a", 0).Events.Single().EventType);
}

static async Task TranscriptEventsAreEmittedAfterCursor()
{
    var coordinator = new MeetingCoordinator(
        new RecordingMediaAdapter(),
        new RecordingSpeechAdapter(),
        "en-US-Test");
    await coordinator.JoinAsync(
        new JoinMeetingRequest("https://teams.example/join", "meeting-a", null, null),
        CancellationToken.None);
    var transcript = coordinator.RecordTranscript("meeting-a", "hello room");

    var afterJoin = coordinator.GetEvents("meeting-a", 1).Events.Single();

    AssertEqual(transcript.Sequence, afterJoin.Sequence);
    AssertEqual("transcript", afterJoin.EventType);
    AssertEqual("hello room", afterJoin.Text);
}

static async Task SpeakSynthesizesAndInjectsAudio()
{
    var media = new RecordingMediaAdapter();
    var speech = new RecordingSpeechAdapter();
    var coordinator = new MeetingCoordinator(media, speech, "en-US-Test");
    await coordinator.JoinAsync(
        new JoinMeetingRequest("https://teams.example/join", "meeting-a", null, null),
        CancellationToken.None);

    var response = await coordinator.SpeakAsync(
        "meeting-a",
        new SpeakRequest("I can help with that.", "en-US-GuyNeural", false),
        CancellationToken.None);

    AssertEqual("queued", response.Status);
    AssertEqual("I can help with that.", speech.Requests.Single().Text);
    AssertEqual("en-US-GuyNeural", speech.Requests.Single().Voice);
    AssertEqual("meeting-a", media.PlayedAudio.Single().MeetingId);
    AssertEqual("speech_status", coordinator.GetEvents("meeting-a", 1).Events.Single().EventType);
}

static async Task InterruptClearsPendingSpeechQueue()
{
    var graph = new FakeGraphMeetingClient();
    var mediaClient = new FakeApplicationHostedMediaClient();
    var adapter = new GraphTeamsMediaAdapter(graph, mediaClient);
    var coordinator = new MeetingCoordinator(adapter, new FixedPcmSpeechAdapter(1280), "en-US-Test");
    await coordinator.JoinAsync(
        new JoinMeetingRequest("https://teams.example/join", "meeting-a", null, null),
        CancellationToken.None);

    await coordinator.SpeakAsync(
        "meeting-a",
        new SpeakRequest("replace queue", null, true),
        CancellationToken.None);

    AssertEqual(1, mediaClient.AudioSessions.Single().ClearCount);
    AssertEqual(2, mediaClient.AudioSessions.Single().QueuedFrames.Single().Frames.Count);
}

static async Task GraphJoinResolvesUrlAndChunksSynthesizedAudio()
{
    var graph = new FakeGraphMeetingClient();
    var mediaClient = new FakeApplicationHostedMediaClient();
    var adapter = new GraphTeamsMediaAdapter(graph, mediaClient);
    var coordinator = new MeetingCoordinator(adapter, new FixedPcmSpeechAdapter(960), "en-US-Test");

    await coordinator.JoinAsync(
        new JoinMeetingRequest("https://teams.example/join", "meeting-a", null, null),
        CancellationToken.None);
    var response = await coordinator.SpeakAsync(
        "meeting-a",
        new SpeakRequest("speak now", null, false),
        CancellationToken.None);

    AssertEqual("https://teams.example/join", graph.ResolvedJoinUrls.Single());
    AssertEqual("queued", response.Status);
    AssertEqual(2, mediaClient.AudioSessions.Single().QueuedFrames.Single().Frames.Count);
    AssertEqual(640, mediaClient.AudioSessions.Single().QueuedFrames.Single().Frames[0].Length);
}

static async Task RealtimeAdapterStreamsAudioAndTranscriptsOverWebSocket()
{
    var socket = new FakeRealtimeWebSocketClient();
    var adapter = new OpenAIRealtimeSpeechAdapter(
        new OpenAIRealtimeOptions(
            "sk-test",
            new Uri("wss://api.openai.test/v1/realtime"),
            "gpt-realtime-2",
            "marin",
            "semantic_vad",
            "gpt-4o-transcribe"),
        new FakeRealtimeWebSocketClientFactory(socket));
    var audioSession = new FakeMeetingAudioSession();
    var transcripts = new List<string>();
    var statuses = new List<(string Status, string? Text)>();

    await using var session = await adapter.StartAsync(
        new MeetingSpeechStartRequest(
            "meeting-a",
            null,
            audioSession,
            new Dictionary<string, object>(),
            new Dictionary<string, object>
            {
                ["api_base_url"] = "https://attacker.example/v1/realtime",
                ["model"] = "gpt-realtime-2",
                ["voice"] = "cedar",
            },
            new Dictionary<string, object>
            {
                ["prompt"] = "Represent the originating Evander agent.",
            },
            (text, token) =>
            {
                _ = token;
                transcripts.Add(text);
                return Task.CompletedTask;
            },
            (error, token) =>
            {
                _ = token;
                throw new InvalidOperationException(error);
            },
            (operationId, status, text, metadata, token) =>
            {
                _ = operationId;
                _ = metadata;
                _ = token;
                statuses.Add((status, text));
                return Task.CompletedTask;
            }),
        CancellationToken.None);

    AssertEqual("wss://api.openai.test/v1/realtime?model=gpt-realtime-2", socket.ConnectUri?.ToString());
    AssertEqual("Bearer sk-test", socket.Headers["Authorization"]);
    AssertContains("\"type\":\"session.update\"", socket.SentMessages[0]);
    AssertContains("\"voice\":\"cedar\"", socket.SentMessages[0]);
    AssertContains("Represent the originating Evander agent.", socket.SentMessages[0]);

    await session.PushAudioAsync(new byte[640], CancellationToken.None);
    var appendEvent = socket.SentMessages.Single(item => item.Contains("input_audio_buffer.append"));
    var appendedAudio = Base64JsonProperty(appendEvent, "audio");
    AssertEqual(960, Convert.FromBase64String(appendedAudio).Length);

    var speak = await session.SpeakAsync("hello room", null, false, CancellationToken.None);
    AssertEqual("queued", speak.Status);
    AssertContains("conversation.item.create", socket.SentMessages[^2]);
    AssertContains("response.create", socket.SentMessages[^1]);
    AssertContains("\"output_modalities\":[\"audio\"]", socket.SentMessages[^1]);
    AssertDoesNotContain("\"modalities\"", socket.SentMessages[^1]);

    socket.EnqueueIncoming(
        """
        {"type":"response.created","response":{"id":"resp_1"}}
        """);
    socket.EnqueueIncoming(
        $$"""
        {"type":"response.output_audio.delta","response_id":"resp_1","delta":"{{Convert.ToBase64String(new byte[960])}}"}
        """);
    socket.EnqueueIncoming(
        """
        {"type":"conversation.item.input_audio_transcription.completed","transcript":"hello from meeting"}
        """);
    socket.EnqueueIncoming(
        """
        {"type":"response.output_audio_transcript.done","response_id":"resp_1","transcript":"hello back"}
        """);

    await WaitUntil(() => audioSession.QueuedFrames.Count == 1 && transcripts.Count == 1 && statuses.Count > 0);

    AssertEqual(1, audioSession.QueuedFrames.Single().Frames.Count);
    AssertEqual(640, audioSession.QueuedFrames.Single().Frames[0].Length);
    AssertEqual("hello from meeting", transcripts.Single());
    AssertEqual("completed", statuses.Last().Status);
    AssertEqual("hello back", statuses.Last().Text);
}

static async Task RecordingStatusFailurePreventsTranscriptPersistence()
{
    var graph = new FakeGraphMeetingClient { FailRecordingStatus = true };
    var mediaClient = new FakeApplicationHostedMediaClient();
    var adapter = new GraphTeamsMediaAdapter(graph, mediaClient);
    var coordinator = new MeetingCoordinator(adapter, new RecordingSpeechAdapter(), "en-US-Test");
    await coordinator.JoinAsync(
        new JoinMeetingRequest("https://teams.example/join", "meeting-a", null, null),
        CancellationToken.None);

    adapter.EmitTranscript("meeting-a", "do not persist");

    var events = coordinator.GetEvents("meeting-a", 1).Events;
    AssertEqual("error", events.Single().EventType);
    AssertContains("recording status failed", events.Single().Error);
}

static async Task LeaveMarksMeetingStoppedAndDisposesResources()
{
    var media = new RecordingMediaAdapter();
    var coordinator = new MeetingCoordinator(media, new RecordingSpeechAdapter(), "en-US-Test");
    await coordinator.JoinAsync(
        new JoinMeetingRequest("https://teams.example/join", "meeting-a", null, null),
        CancellationToken.None);

    var response = await coordinator.LeaveAsync("meeting-a", CancellationToken.None);

    AssertEqual("stopped", response.Status);
    AssertEqual("stopped", coordinator.GetState("meeting-a").Status);
    AssertEqual("meeting-a", media.LeftMeetingIds.Single());
    AssertEqual("meeting-a", media.StoppedRecordingMeetings.Single());
}

static void AssertEqual<T>(T expected, T actual)
{
    if (!EqualityComparer<T>.Default.Equals(expected, actual))
    {
        throw new InvalidOperationException($"Expected {expected}, got {actual}");
    }
}

static void AssertContains(string expectedSubstring, string? actual)
{
    if (actual is null || !actual.Contains(expectedSubstring, StringComparison.Ordinal))
    {
        throw new InvalidOperationException($"Expected '{actual}' to contain '{expectedSubstring}'");
    }
}

static void AssertDoesNotContain(string unexpectedSubstring, string? actual)
{
    if (actual is not null && actual.Contains(unexpectedSubstring, StringComparison.Ordinal))
    {
        throw new InvalidOperationException($"Expected '{actual}' not to contain '{unexpectedSubstring}'");
    }
}

static void AssertThrows<TException>(Action action)
    where TException : Exception
{
    try
    {
        action();
    }
    catch (TException)
    {
        return;
    }

    throw new InvalidOperationException($"Expected {typeof(TException).Name}");
}

static async Task WaitUntil(Func<bool> predicate)
{
    for (var attempt = 0; attempt < 50; attempt++)
    {
        if (predicate())
        {
            return;
        }

        await Task.Delay(20);
    }

    throw new InvalidOperationException("Timed out waiting for condition.");
}

static Dictionary<string, string?> RequiredGraphConfig()
{
    return new Dictionary<string, string?>
    {
        ["Bridge:AdapterMode"] = "Graph",
        ["Graph:BotAppId"] = "00000000-0000-0000-0000-000000000001",
        ["Graph:BotAppSecret"] = "secret",
        ["Graph:TenantId"] = "00000000-0000-0000-0000-000000000002",
        ["Graph:PublicCallbackBaseUrl"] = "https://bridge.example",
        ["Graph:ServiceFqdn"] = "bridge.example",
        ["Graph:PublicIp"] = "203.0.113.10",
        ["Graph:PublicPort"] = "8445",
        ["Graph:InternalPort"] = "8445",
        ["Graph:CertificateThumbprint"] = "thumbprint",
        ["Graph:OnlineMeetingLookupUserId"] = "lookup@example.com",
    };
}

static string? JsonObjectString(Dictionary<string, object>? values, string propertyName)
{
    if (values is null || !values.TryGetValue(propertyName, out var value))
    {
        return null;
    }

    return value is JsonElement element && element.ValueKind == JsonValueKind.String
        ? element.GetString()
        : Convert.ToString(value);
}

static string Base64JsonProperty(string json, string propertyName)
{
    using var document = JsonDocument.Parse(json);
    return document.RootElement.GetProperty(propertyName).GetString()
        ?? throw new InvalidOperationException($"JSON property {propertyName} was missing.");
}

internal sealed class RecordingMediaAdapter : ITeamsMediaAdapter
{
    public List<string> JoinedMeetingIds { get; } = [];

    public List<(string MeetingId, byte[] AudioBytes, bool Interrupt)> PlayedAudio { get; } = [];

    public List<string> LeftMeetingIds { get; } = [];

    public List<string> StoppedRecordingMeetings { get; } = [];

    public event EventHandler<MediaTranscriptReceivedEvent>? TranscriptReceived;

    public event EventHandler<MediaErrorReceivedEvent>? ErrorReceived;

    public event EventHandler<MediaSpeechStatusEvent>? SpeechStatusChanged;

    public Task<MediaJoinResult> JoinAsync(
        string joinUrl,
        string externalMeetingId,
        CancellationToken cancellationToken)
    {
        _ = joinUrl;
        cancellationToken.ThrowIfCancellationRequested();
        JoinedMeetingIds.Add(externalMeetingId);
        return Task.FromResult(new MediaJoinResult(externalMeetingId));
    }

    public Task<SpeechPlaybackResult> PlayAudioAsync(
        string meetingId,
        string operationId,
        string text,
        byte[] audioBytes,
        bool interrupt,
        CancellationToken cancellationToken)
    {
        _ = operationId;
        _ = text;
        cancellationToken.ThrowIfCancellationRequested();
        PlayedAudio.Add((meetingId, audioBytes, interrupt));
        return Task.FromResult(new SpeechPlaybackResult("queued", 0));
    }

    public Task LeaveAsync(string meetingId, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        LeftMeetingIds.Add(meetingId);
        return Task.CompletedTask;
    }

    public Task StartRecordingStatusAsync(string meetingId, CancellationToken cancellationToken)
    {
        _ = meetingId;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.CompletedTask;
    }

    public Task StopRecordingStatusAsync(string meetingId, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        StoppedRecordingMeetings.Add(meetingId);
        return Task.CompletedTask;
    }
}

internal sealed class RecordingSpeechAdapter : IAzureSpeechAdapter
{
    public List<(string Text, string Voice)> Requests { get; } = [];

    public Task<SynthesizedSpeech> SynthesizeAsync(
        string text,
        string voice,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        Requests.Add((text, voice));
        return Task.FromResult(
            new SynthesizedSpeech("speech-op-1", voice, System.Text.Encoding.UTF8.GetBytes(text)));
    }

    public Task<ISpeechRecognitionSession> StartRecognitionAsync(
        string meetingId,
        Func<string, CancellationToken, Task> onFinalTranscript,
        Func<string, CancellationToken, Task> onError,
        CancellationToken cancellationToken)
    {
        _ = meetingId;
        _ = onFinalTranscript;
        _ = onError;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult<ISpeechRecognitionSession>(new FakeRecognitionSession());
    }
}

internal sealed class FixedPcmSpeechAdapter : IAzureSpeechAdapter
{
    private readonly int byteCount;

    public FixedPcmSpeechAdapter(int byteCount)
    {
        this.byteCount = byteCount;
    }

    public Task<SynthesizedSpeech> SynthesizeAsync(
        string text,
        string voice,
        CancellationToken cancellationToken)
    {
        _ = text;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(new SynthesizedSpeech("speech-op-1", voice, new byte[byteCount]));
    }

    public Task<ISpeechRecognitionSession> StartRecognitionAsync(
        string meetingId,
        Func<string, CancellationToken, Task> onFinalTranscript,
        Func<string, CancellationToken, Task> onError,
        CancellationToken cancellationToken)
    {
        _ = meetingId;
        _ = onFinalTranscript;
        _ = onError;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult<ISpeechRecognitionSession>(new FakeRecognitionSession());
    }
}

internal sealed class FakeRecognitionSession : ISpeechRecognitionSession
{
    public int PushCount { get; private set; }

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;

    public Task PushAudioAsync(byte[] pcmAudio, CancellationToken cancellationToken)
    {
        _ = pcmAudio;
        cancellationToken.ThrowIfCancellationRequested();
        PushCount++;
        return Task.CompletedTask;
    }
}

internal sealed class FakeGraphMeetingClient : IGraphMeetingClient
{
    public bool FailRecordingStatus { get; set; }

    public List<string> ResolvedJoinUrls { get; } = [];

    public List<(string CallId, bool IsRecording)> RecordingStatusCalls { get; } = [];

    public Task<OnlineMeetingCoordinates> ResolveJoinUrlAsync(
        string joinUrl,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        ResolvedJoinUrls.Add(joinUrl);
        return Task.FromResult(new OnlineMeetingCoordinates("{}", "{}", "tenant-a"));
    }

    public Task SetRecordingStatusAsync(
        string callId,
        bool isRecording,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (FailRecordingStatus)
        {
            throw new InvalidOperationException("Graph updateRecordingStatus failed");
        }

        RecordingStatusCalls.Add((callId, isRecording));
        return Task.CompletedTask;
    }
}

internal sealed class FakeApplicationHostedMediaClient : IApplicationHostedMediaClient
{
    public List<FakeMeetingAudioSession> AudioSessions { get; } = [];

    public Task<ApplicationHostedMediaSession> JoinAsync(
        string externalMeetingId,
        OnlineMeetingCoordinates coordinates,
        CancellationToken cancellationToken)
    {
        _ = coordinates;
        cancellationToken.ThrowIfCancellationRequested();
        var audio = new FakeMeetingAudioSession();
        AudioSessions.Add(audio);
        return Task.FromResult(new ApplicationHostedMediaSession(externalMeetingId, $"call-{externalMeetingId}", audio));
    }
}

internal sealed class FakeMeetingAudioSession : IMeetingAudioSession
{
    public List<(string OperationId, IReadOnlyList<byte[]> Frames)> QueuedFrames { get; } = [];

    public int ClearCount { get; private set; }

    public event EventHandler<byte[]>? AudioReceived;

    public Task ClearPendingAudioAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        ClearCount++;
        return Task.CompletedTask;
    }

    public Task EnqueueAudioAsync(
        string operationId,
        IReadOnlyList<byte[]> frames,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        QueuedFrames.Add((operationId, frames));
        return Task.CompletedTask;
    }

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}

internal sealed class FakeRealtimeWebSocketClientFactory : IRealtimeWebSocketClientFactory
{
    private readonly FakeRealtimeWebSocketClient client;

    public FakeRealtimeWebSocketClientFactory(FakeRealtimeWebSocketClient client)
    {
        this.client = client;
    }

    public IRealtimeWebSocketClient Create() => client;
}

internal sealed class FakeRealtimeWebSocketClient : IRealtimeWebSocketClient
{
    private readonly object sync = new();
    private readonly Queue<string?> incoming = new();
    private readonly SemaphoreSlim incomingSignal = new(0);
    private readonly List<string> sentMessages = [];
    public bool StallClose, Disposed;

    public Uri? ConnectUri { get; private set; }

    public Dictionary<string, string> Headers { get; } = new(StringComparer.Ordinal);

    public IReadOnlyList<string> SentMessages { get { lock (sync) return sentMessages.ToArray(); } }

    public Task ConnectAsync(
        Uri uri,
        IReadOnlyDictionary<string, string> headers,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        ConnectUri = uri;
        foreach (var header in headers)
        {
            Headers[header.Key] = header.Value;
        }

        return Task.CompletedTask;
    }

    public Task SendTextAsync(string text, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        lock (sync) sentMessages.Add(text);
        return Task.CompletedTask;
    }

    public async Task<string?> ReceiveTextAsync(CancellationToken cancellationToken)
    {
        await incomingSignal.WaitAsync(cancellationToken);
        lock (sync)
        {
            return incoming.Dequeue();
        }
    }

    public Task CloseAsync(CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (StallClose) return Task.Delay(Timeout.Infinite, cancellationToken);
        lock (sync)
        {
            incoming.Enqueue(null);
        }

        incomingSignal.Release();
        return Task.CompletedTask;
    }

    public void EnqueueIncoming(string message)
    {
        lock (sync)
        {
            incoming.Enqueue(message);
        }

        incomingSignal.Release();
    }

    public ValueTask DisposeAsync()
    {
        Disposed = true;
        incomingSignal.Dispose();
        return ValueTask.CompletedTask;
    }
}
