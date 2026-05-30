namespace TeamsMeetingMediaBridge;

public sealed class AzureMeetingSpeechAdapter : IMeetingSpeechAdapter
{
    private readonly IAzureSpeechAdapter speechAdapter;
    private readonly ITeamsMediaAdapter mediaAdapter;

    public AzureMeetingSpeechAdapter(
        IAzureSpeechAdapter speechAdapter,
        ITeamsMediaAdapter mediaAdapter)
    {
        this.speechAdapter = speechAdapter;
        this.mediaAdapter = mediaAdapter;
    }

    public async Task<IMeetingSpeechSession> StartAsync(
        MeetingSpeechStartRequest request,
        CancellationToken cancellationToken)
    {
        ISpeechRecognitionSession? recognitionSession = null;
        if (request.AudioSession is not null)
        {
            recognitionSession = await speechAdapter.StartRecognitionAsync(
                request.MeetingId,
                request.OnFinalTranscript,
                request.OnError,
                cancellationToken);
        }

        return new AzureMeetingSpeechSession(
            request.MeetingId,
            string.IsNullOrWhiteSpace(request.DefaultVoice)
                ? "en-US-JennyNeural"
                : request.DefaultVoice.Trim(),
            speechAdapter,
            mediaAdapter,
            recognitionSession);
    }

    private sealed class AzureMeetingSpeechSession : IMeetingSpeechSession
    {
        private readonly string meetingId;
        private readonly string defaultVoice;
        private readonly IAzureSpeechAdapter speechAdapter;
        private readonly ITeamsMediaAdapter mediaAdapter;
        private readonly ISpeechRecognitionSession? recognitionSession;

        public AzureMeetingSpeechSession(
            string meetingId,
            string defaultVoice,
            IAzureSpeechAdapter speechAdapter,
            ITeamsMediaAdapter mediaAdapter,
            ISpeechRecognitionSession? recognitionSession)
        {
            this.meetingId = meetingId;
            this.defaultVoice = defaultVoice;
            this.speechAdapter = speechAdapter;
            this.mediaAdapter = mediaAdapter;
            this.recognitionSession = recognitionSession;
        }

        public Task PushAudioAsync(byte[] pcmAudio, CancellationToken cancellationToken)
        {
            return recognitionSession is null
                ? Task.CompletedTask
                : recognitionSession.PushAudioAsync(pcmAudio, cancellationToken);
        }

        public async Task<SpeechPlaybackResult> SpeakAsync(
            string text,
            string? voice,
            bool interrupt,
            CancellationToken cancellationToken)
        {
            var resolvedVoice = string.IsNullOrWhiteSpace(voice) ? defaultVoice : voice.Trim();
            var speech = await speechAdapter.SynthesizeAsync(text, resolvedVoice, cancellationToken);
            var playback = await mediaAdapter.PlayAudioAsync(
                meetingId,
                speech.OperationId,
                text,
                speech.AudioBytes,
                interrupt,
                cancellationToken);
            var metadata = new Dictionary<string, object>(playback.Metadata ?? [])
            {
                ["voice"] = speech.Voice,
            };
            return playback with
            {
                OperationId = speech.OperationId,
                Metadata = metadata,
            };
        }

        public ValueTask DisposeAsync()
        {
            return recognitionSession is null ? ValueTask.CompletedTask : recognitionSession.DisposeAsync();
        }
    }
}
