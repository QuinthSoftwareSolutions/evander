using System.Collections.Concurrent;
using System.Text.Json.Serialization;

namespace TeamsMeetingMediaBridge;

public interface IIncomingTeamsCall
{
    string CallId { get; }
    string TenantId { get; }
    string CallerUserId { get; }
    bool IsPersonalAudioCall { get; }
    Task Ended { get; }
    Task<IMeetingAudioSession> AnswerAsync(CancellationToken token);
    Task SetRecordingAsync(bool recording, CancellationToken token);
    Task RejectAsync(CancellationToken token);
}

public sealed record NativeTranscript(
    [property: JsonPropertyName("sequence")] long Sequence,
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("speaker")] string Speaker,
    [property: JsonPropertyName("occurred_at")] DateTimeOffset OccurredAt);

public sealed record NativeCallSnapshot(
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("transcript_events")] IReadOnlyList<NativeTranscript> TranscriptEvents,
    [property: JsonPropertyName("error")] string? Error);

public sealed class NativeCallCoordinator
{
    private readonly IControlPlaneVoiceClient controlPlane;
    private readonly IMeetingSpeechAdapter speech;
    private readonly ILogger<NativeCallCoordinator> logger;
    private readonly ConcurrentDictionary<string, Lazy<Task>> admissions = new();
    private readonly ConcurrentDictionary<string, State> calls = new();

    public NativeCallCoordinator(IControlPlaneVoiceClient controlPlane, IMeetingSpeechAdapter speech,
        ILogger<NativeCallCoordinator> logger)
    {
        this.controlPlane = controlPlane;
        this.speech = speech;
        this.logger = logger;
    }

    public Task HandleAsync(IIncomingTeamsCall call) => admissions.GetOrAdd(
        call.CallId, _ => new Lazy<Task>(() => RunCallAsync(call))).Value;

    public NativeCallSnapshot? GetSnapshot(string voiceSessionId) =>
        calls.TryGetValue(voiceSessionId, out var state) ? state.Snapshot() : null;

    private async Task RunCallAsync(IIncomingTeamsCall call)
    {
        TeamsCallAdmission? admission = null;
        State? state = null;
        IMeetingAudioSession? audio = null;
        IMeetingSpeechSession? session = null;
        EventHandler<byte[]>? audioHandler = null;
        var terminalStatus = "failed";
        var failed = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var lifetime = new CancellationTokenSource();
        try
        {
            if (!call.IsPersonalAudioCall || !Guid.TryParse(call.TenantId, out _)
                || !Guid.TryParse(call.CallerUserId, out _))
                throw new InvalidOperationException("A verified one-to-one Teams user audio call is required.");
            using var admitTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            admission = await controlPlane.AdmitAsync(call.TenantId, call.CallerUserId, call.CallId, admitTimeout.Token);
            if (admission.Status is "ended" or "failed")
                throw new InvalidOperationException("This call has already ended.");
            state = new State();
            calls[admission.VoiceSessionId] = state;
            using var answerTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(9));
            audio = await call.AnswerAsync(answerTimeout.Token);
            await call.SetRecordingAsync(true, answerTimeout.Token);
            session = await speech.StartAsync(new MeetingSpeechStartRequest(
                admission.VoiceSessionId, null, audio, null, admission.Realtime, admission.Projection,
                (text, _) => { state.Append(text, "caller"); return Task.CompletedTask; },
                (error, _) => { failed.TrySetResult(error); return Task.CompletedTask; },
                (_, status, text, _, _) =>
                {
                    if (status == "completed" && !string.IsNullOrWhiteSpace(text)) state.Append(text, "assistant");
                    return Task.CompletedTask;
                },
                (question, reason, requestId, token) => controlPlane.QueryAsync(
                    admission.VoiceSessionId, question, reason, requestId, token)), answerTimeout.Token);
            audioHandler = (_, bytes) => _ = FeedAudioAsync(bytes);
            audio.AudioReceived += audioHandler;
            state.Status = "active";
            var playback = audio is IPlaybackMonitoredAudioSession monitored
                ? monitored.PlaybackCompletion : Task.Delay(Timeout.Infinite, lifetime.Token);
            await Task.WhenAny(call.Ended, failed.Task, playback);
            if (failed.Task.IsCompleted) throw new InvalidOperationException(await failed.Task);
            if (playback.IsCompleted && !call.Ended.IsCompleted)
            {
                await playback;
                throw new InvalidOperationException("Teams audio playback stopped.");
            }
            terminalStatus = "ended";
        }
        catch (Exception ex)
        {
            if (state is not null) state.Error = ex.Message;
            logger.LogWarning("Teams call failed call_id={CallId} error={Error}", call.CallId, ex.GetType().Name);
            try
            {
                using var rejectTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                await call.RejectAsync(rejectTimeout.Token);
            }
            catch (Exception rejectError) { logger.LogWarning("Teams call cleanup failed: {Error}", rejectError.GetType().Name); }
        }
        finally
        {
            lifetime.Cancel();
            if (audio is not null && audioHandler is not null) audio.AudioReceived -= audioHandler;
            foreach (var resource in new IAsyncDisposable?[] { session, audio })
            {
                try { if (resource is not null) await resource.DisposeAsync(); }
                catch (Exception ex) { logger.LogWarning("Teams media disposal failed: {Error}", ex.GetType().Name); }
            }
            if (admission is not null && state is not null)
            {
                state.Status = terminalStatus;
                try
                {
                    using var completeTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                    await controlPlane.CompleteAsync(admission.VoiceSessionId, state.Snapshot(), completeTimeout.Token);
                }
                catch (Exception ex) { logger.LogWarning("Teams call completion awaits supervision: {Error}", ex.GetType().Name); }
            }
            // Terminal snapshots remain pollable for recovery; bound their retention in memory.
            _ = ExpireAsync(call.CallId, admission?.VoiceSessionId);
        }

        async Task FeedAudioAsync(byte[] bytes)
        {
            try { if (session is not null) await session.PushAudioAsync(bytes, lifetime.Token); }
            catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
            catch (Exception ex) { failed.TrySetResult(ex.Message); }
        }
    }

    private async Task ExpireAsync(string callId, string? voiceSessionId)
    {
        await Task.Delay(TimeSpan.FromHours(1));
        admissions.TryRemove(callId, out _);
        if (voiceSessionId is not null) calls.TryRemove(voiceSessionId, out _);
    }

    private sealed class State
    {
        private readonly object sync = new();
        private readonly List<NativeTranscript> transcripts = new();
        public string Status = "connecting";
        public string? Error;
        public void Append(string text, string speaker)
        {
            lock (sync) transcripts.Add(new NativeTranscript(transcripts.Count + 1, text, speaker, DateTimeOffset.UtcNow));
        }
        public NativeCallSnapshot Snapshot()
        {
            lock (sync) return new NativeCallSnapshot(Status, transcripts.ToArray(), Error);
        }
    }
}
