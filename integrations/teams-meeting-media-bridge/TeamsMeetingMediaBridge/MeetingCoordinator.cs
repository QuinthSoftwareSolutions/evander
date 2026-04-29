using System.Collections.Concurrent;

namespace TeamsMeetingMediaBridge;

public sealed class MeetingCoordinator
{
    private readonly ITeamsMediaAdapter mediaAdapter;
    private readonly IAzureSpeechAdapter speechAdapter;
    private readonly string defaultVoice;
    private readonly ConcurrentDictionary<string, MeetingRuntimeState> meetings = new();

    public MeetingCoordinator(
        ITeamsMediaAdapter mediaAdapter,
        IAzureSpeechAdapter speechAdapter,
        string defaultVoice)
    {
        this.mediaAdapter = mediaAdapter;
        this.speechAdapter = speechAdapter;
        this.defaultVoice = string.IsNullOrWhiteSpace(defaultVoice)
            ? "en-US-JennyNeural"
            : defaultVoice.Trim();
        this.mediaAdapter.TranscriptReceived += (_, item) => RecordTranscriptFromMedia(item.MeetingId, item.Text);
        this.mediaAdapter.ErrorReceived += (_, item) => RecordAdapterError(item.MeetingId, item.Error);
        this.mediaAdapter.SpeechStatusChanged += (_, item) => RecordSpeechStatus(
            item.MeetingId,
            item.OperationId,
            item.Status,
            item.Text,
            metadata: null);
    }

    public async Task<JoinMeetingResponse> JoinAsync(
        JoinMeetingRequest request,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(request.JoinUrl))
        {
            throw new ArgumentException("join_url is required");
        }

        if (string.IsNullOrWhiteSpace(request.ExternalMeetingId))
        {
            throw new ArgumentException("external_meeting_id is required");
        }

        var mediaJoin = await mediaAdapter.JoinAsync(
            request.JoinUrl.Trim(),
            request.ExternalMeetingId.Trim(),
            cancellationToken);
        ISpeechRecognitionSession? recognitionSession = null;
        if (mediaJoin.AudioSession is not null)
        {
            recognitionSession = await speechAdapter.StartRecognitionAsync(
                mediaJoin.MeetingId,
                (text, token) =>
                {
                    RecordTranscriptFromMedia(mediaJoin.MeetingId, text);
                    return Task.CompletedTask;
                },
                (error, token) =>
                {
                    _ = token;
                    RecordAdapterError(mediaJoin.MeetingId, error);
                    return Task.CompletedTask;
                },
                cancellationToken);
            mediaJoin.AudioSession.AudioReceived += (_, audio) =>
            {
                _ = recognitionSession.PushAudioAsync(audio, CancellationToken.None)
                    .ContinueWith(task =>
                    {
                        if (task.Exception is not null)
                        {
                            RecordAdapterError(
                                mediaJoin.MeetingId,
                                task.Exception.GetBaseException().Message);
                        }
                    }, TaskScheduler.Default);
            };
        }

        var now = DateTimeOffset.UtcNow;
        var state = new MeetingRuntimeState(
            new MeetingState(
                mediaJoin.MeetingId,
                request.JoinUrl.Trim(),
                request.DefaultVoice,
                "active",
                now,
                now),
            mediaJoin.AudioSession,
            recognitionSession);
        meetings[mediaJoin.MeetingId] = state;
        state.Append(
            "meeting_status",
            text: null,
            speechOperationId: null,
            speechStatus: "joined",
            error: null,
            metadata: new Dictionary<string, object> { ["source"] = "media_bridge" });
        return new JoinMeetingResponse(mediaJoin.MeetingId, "active");
    }

    public MeetingEventsResponse GetEvents(string meetingId, long afterSequence)
    {
        var state = RequireMeeting(meetingId);
        return new MeetingEventsResponse(state.EventsAfter(afterSequence));
    }

    public MeetingEvent RecordTranscript(string meetingId, string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            throw new ArgumentException("transcript text is required");
        }

        var state = RequireMeeting(meetingId);
        return state.Append(
            "transcript",
            text.Trim(),
            speechOperationId: null,
            speechStatus: null,
            error: null,
            metadata: new Dictionary<string, object> { ["final"] = true });
    }

    public async Task<SpeakResponse> SpeakAsync(
        string meetingId,
        SpeakRequest request,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(request.Text))
        {
            throw new ArgumentException("text is required");
        }

        var state = RequireMeeting(meetingId);
        var voice = string.IsNullOrWhiteSpace(request.Voice)
            ? state.State.DefaultVoice ?? defaultVoice
            : request.Voice.Trim();
        var speech = await speechAdapter.SynthesizeAsync(request.Text.Trim(), voice, cancellationToken);
        var playback = await mediaAdapter.PlayAudioAsync(
            meetingId,
            speech.OperationId,
            request.Text.Trim(),
            speech.AudioBytes,
            request.Interrupt,
            cancellationToken);
        var status = string.IsNullOrWhiteSpace(playback.Status) ? "queued" : playback.Status.Trim();
        state.Append(
            "speech_status",
            request.Text.Trim(),
            speech.OperationId,
            status,
            error: null,
            metadata: new Dictionary<string, object>
            {
                ["voice"] = speech.Voice,
                ["interrupt"] = request.Interrupt,
                ["frames_queued"] = playback.FramesQueued,
            });
        return new SpeakResponse(speech.OperationId, status, meetingId);
    }

    public async Task<LeaveResponse> LeaveAsync(string meetingId, CancellationToken cancellationToken)
    {
        var state = RequireMeeting(meetingId);
        await mediaAdapter.StopRecordingStatusAsync(meetingId, cancellationToken);
        await mediaAdapter.LeaveAsync(meetingId, cancellationToken);
        await state.DisposeAsync();
        state.MarkStopped();
        state.Append(
            "meeting_status",
            text: null,
            speechOperationId: null,
            speechStatus: "stopped",
            error: null,
            metadata: new Dictionary<string, object> { ["source"] = "media_bridge" });
        return new LeaveResponse(meetingId, "stopped");
    }

    public MeetingState GetState(string meetingId) => RequireMeeting(meetingId).State;

    private MeetingRuntimeState RequireMeeting(string meetingId)
    {
        if (meetings.TryGetValue(meetingId, out var state))
        {
            return state;
        }

        throw new KeyNotFoundException($"Unknown meeting: {meetingId}");
    }

    private void RecordTranscriptFromMedia(string meetingId, string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }

        if (!meetings.TryGetValue(meetingId, out var state))
        {
            return;
        }

        try
        {
            mediaAdapter.StartRecordingStatusAsync(meetingId, CancellationToken.None)
                .GetAwaiter()
                .GetResult();
        }
        catch (Exception ex)
        {
            RecordAdapterError(meetingId, $"recording status failed: {ex.Message}");
            return;
        }

        state.Append(
            "transcript",
            text.Trim(),
            speechOperationId: null,
            speechStatus: null,
            error: null,
            metadata: new Dictionary<string, object>
            {
                ["final"] = true,
                ["source"] = "media",
            });
    }

    private void RecordAdapterError(string meetingId, string error)
    {
        if (!meetings.TryGetValue(meetingId, out var state))
        {
            return;
        }

        state.Append(
            "error",
            text: null,
            speechOperationId: null,
            speechStatus: null,
            error: string.IsNullOrWhiteSpace(error) ? "unknown media adapter error" : error.Trim(),
            metadata: new Dictionary<string, object> { ["source"] = "media_bridge" });
    }

    private void RecordSpeechStatus(
        string meetingId,
        string operationId,
        string status,
        string? text,
        Dictionary<string, object>? metadata)
    {
        if (!meetings.TryGetValue(meetingId, out var state))
        {
            return;
        }

        state.Append(
            "speech_status",
            text,
            operationId,
            status,
            error: null,
            metadata);
    }
}

internal sealed class MeetingRuntimeState : IAsyncDisposable
{
    private readonly object sync = new();
    private readonly List<MeetingEvent> events = new();
    private long nextSequence = 1;
    private readonly IMeetingAudioSession? audioSession;
    private readonly ISpeechRecognitionSession? recognitionSession;

    public MeetingRuntimeState(
        MeetingState state,
        IMeetingAudioSession? audioSession = null,
        ISpeechRecognitionSession? recognitionSession = null)
    {
        State = state;
        this.audioSession = audioSession;
        this.recognitionSession = recognitionSession;
    }

    public MeetingState State { get; private set; }

    public MeetingEvent Append(
        string eventType,
        string? text,
        string? speechOperationId,
        string? speechStatus,
        string? error,
        Dictionary<string, object>? metadata)
    {
        lock (sync)
        {
            var now = DateTimeOffset.UtcNow;
            var item = new MeetingEvent(
                nextSequence++,
                eventType,
                now,
                text,
                speechOperationId,
                speechStatus,
                error,
                metadata);
            events.Add(item);
            State = State with { UpdatedAt = now };
            return item;
        }
    }

    public IReadOnlyList<MeetingEvent> EventsAfter(long afterSequence)
    {
        lock (sync)
        {
            return events.Where(item => item.Sequence > afterSequence).ToArray();
        }
    }

    public void MarkStopped()
    {
        lock (sync)
        {
            State = State with { Status = "stopped", UpdatedAt = DateTimeOffset.UtcNow };
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (recognitionSession is not null)
        {
            await recognitionSession.DisposeAsync();
        }

        if (audioSession is not null)
        {
            await audioSession.DisposeAsync();
        }
    }
}
