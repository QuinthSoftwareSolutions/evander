namespace TeamsMeetingMediaBridge;

public interface ITeamsMediaAdapter
{
    event EventHandler<MediaTranscriptReceivedEvent>? TranscriptReceived;

    event EventHandler<MediaErrorReceivedEvent>? ErrorReceived;

    event EventHandler<MediaSpeechStatusEvent>? SpeechStatusChanged;

    Task<MediaJoinResult> JoinAsync(
        string joinUrl,
        string externalMeetingId,
        CancellationToken cancellationToken);

    Task<SpeechPlaybackResult> PlayAudioAsync(
        string meetingId,
        string operationId,
        string text,
        byte[] audioBytes,
        bool interrupt,
        CancellationToken cancellationToken);

    Task LeaveAsync(string meetingId, CancellationToken cancellationToken);

    Task StartRecordingStatusAsync(string meetingId, CancellationToken cancellationToken);

    Task StopRecordingStatusAsync(string meetingId, CancellationToken cancellationToken);
}

public interface IAzureSpeechAdapter
{
    Task<SynthesizedSpeech> SynthesizeAsync(
        string text,
        string voice,
        CancellationToken cancellationToken);

    Task<ISpeechRecognitionSession> StartRecognitionAsync(
        string meetingId,
        Func<string, CancellationToken, Task> onFinalTranscript,
        Func<string, CancellationToken, Task> onError,
        CancellationToken cancellationToken);
}

public interface ISpeechRecognitionSession : IAsyncDisposable
{
    Task PushAudioAsync(byte[] pcmAudio, CancellationToken cancellationToken);
}

public interface IMeetingAudioSession : IAsyncDisposable
{
    event EventHandler<byte[]>? AudioReceived;

    Task EnqueueAudioAsync(string operationId, IReadOnlyList<byte[]> frames, CancellationToken cancellationToken);

    Task ClearPendingAudioAsync(CancellationToken cancellationToken);
}

public sealed class InMemoryTeamsMediaAdapter : ITeamsMediaAdapter
{
    private readonly object sync = new();
    private readonly HashSet<string> joinedMeetings = new(StringComparer.Ordinal);

    public IReadOnlyCollection<string> JoinedMeetings
    {
        get
        {
            lock (sync)
            {
                return joinedMeetings.ToArray();
            }
        }
    }

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
        lock (sync)
        {
            joinedMeetings.Add(externalMeetingId);
        }
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
        _ = meetingId;
        _ = operationId;
        _ = text;
        _ = audioBytes;
        _ = interrupt;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(new SpeechPlaybackResult("queued", 0));
    }

    public Task LeaveAsync(string meetingId, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        lock (sync)
        {
            joinedMeetings.Remove(meetingId);
        }
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
        _ = meetingId;
        cancellationToken.ThrowIfCancellationRequested();
        return Task.CompletedTask;
    }

    public void EmitTranscript(string meetingId, string text)
    {
        TranscriptReceived?.Invoke(this, new MediaTranscriptReceivedEvent(meetingId, text));
    }

    public void EmitError(string meetingId, string error)
    {
        ErrorReceived?.Invoke(this, new MediaErrorReceivedEvent(meetingId, error));
    }

    public void EmitSpeechStatus(string meetingId, string operationId, string status, string? text = null)
    {
        SpeechStatusChanged?.Invoke(this, new MediaSpeechStatusEvent(meetingId, operationId, status, text));
    }
}

public sealed class DeterministicSpeechAdapter : IAzureSpeechAdapter
{
    public Task<SynthesizedSpeech> SynthesizeAsync(
        string text,
        string voice,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var operationId = $"speech-{Guid.NewGuid():N}";
        var bytes = System.Text.Encoding.UTF8.GetBytes($"{voice}:{text}");
        return Task.FromResult(new SynthesizedSpeech(operationId, voice, bytes));
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
        return Task.FromResult<ISpeechRecognitionSession>(new NoopSpeechRecognitionSession());
    }

    private sealed class NoopSpeechRecognitionSession : ISpeechRecognitionSession
    {
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;

        public Task PushAudioAsync(byte[] pcmAudio, CancellationToken cancellationToken)
        {
            _ = pcmAudio;
            cancellationToken.ThrowIfCancellationRequested();
            return Task.CompletedTask;
        }
    }
}
