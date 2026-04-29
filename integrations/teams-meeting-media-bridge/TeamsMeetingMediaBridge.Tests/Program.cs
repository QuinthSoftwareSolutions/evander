using Microsoft.Extensions.Configuration;
using TeamsMeetingMediaBridge;

var tests = new (string Name, Func<Task> Run)[]
{
    ("adapter selection defaults to in-memory", AdapterSelectionDefaultsToInMemory),
    ("graph mode validates required config", GraphModeValidatesRequiredConfig),
    ("join records active meeting state", JoinRecordsActiveMeetingState),
    ("transcript events are emitted after cursor", TranscriptEventsAreEmittedAfterCursor),
    ("speak synthesizes and injects audio", SpeakSynthesizesAndInjectsAudio),
    ("interrupt clears pending speech queue", InterruptClearsPendingSpeechQueue),
    ("graph join resolves url and chunks synthesized audio", GraphJoinResolvesUrlAndChunksSynthesizedAudio),
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
    var options = BridgeOptions.FromConfiguration(new ConfigurationBuilder().Build());

    AssertEqual(BridgeAdapterMode.InMemory, options.AdapterMode);
    AssertEqual("en-US-JennyNeural", options.DefaultVoice);
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
