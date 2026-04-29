using Microsoft.CognitiveServices.Speech;
using Microsoft.CognitiveServices.Speech.Audio;

namespace TeamsMeetingMediaBridge;

public sealed class AzureSpeechAdapter : IAzureSpeechAdapter
{
    private readonly AzureSpeechOptions options;

    public AzureSpeechAdapter(AzureSpeechOptions options)
    {
        this.options = options;
    }

    public async Task<SynthesizedSpeech> SynthesizeAsync(
        string text,
        string voice,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var operationId = $"speech-{Guid.NewGuid():N}";
        var speechConfig = CreateSpeechConfig();
        speechConfig.SpeechSynthesisVoiceName = string.IsNullOrWhiteSpace(voice)
            ? options.DefaultVoice
            : voice.Trim();
        speechConfig.SetSpeechSynthesisOutputFormat(
            SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm);

        using var synthesizer = new SpeechSynthesizer(speechConfig, audioConfig: null);
        using var result = await synthesizer.SpeakTextAsync(text).WaitAsync(cancellationToken);
        if (result.Reason != ResultReason.SynthesizingAudioCompleted)
        {
            var cancellation = SpeechSynthesisCancellationDetails.FromResult(result);
            throw new InvalidOperationException(
                $"Azure Speech synthesis failed: {cancellation.Reason} {cancellation.ErrorDetails}".Trim());
        }

        return new SynthesizedSpeech(operationId, speechConfig.SpeechSynthesisVoiceName, result.AudioData);
    }

    public Task<ISpeechRecognitionSession> StartRecognitionAsync(
        string meetingId,
        Func<string, CancellationToken, Task> onFinalTranscript,
        Func<string, CancellationToken, Task> onError,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var speechConfig = CreateSpeechConfig();
        speechConfig.SpeechRecognitionLanguage = options.RecognitionLanguage;
        var format = AudioStreamFormat.GetWaveFormatPCM(
            samplesPerSecond: 16000,
            bitsPerSample: 16,
            channels: 1);
        var pushStream = AudioInputStream.CreatePushStream(format);
        var audioConfig = AudioConfig.FromStreamInput(pushStream);
        var recognizer = new SpeechRecognizer(speechConfig, audioConfig);
        var session = new AzureSpeechRecognitionSession(
            meetingId,
            speechConfig,
            pushStream,
            audioConfig,
            recognizer,
            onFinalTranscript,
            onError);
        return session.StartAsync(cancellationToken);
    }

    private SpeechConfig CreateSpeechConfig()
    {
        return options.Endpoint is not null
            ? SpeechConfig.FromEndpoint(options.Endpoint, options.Key)
            : SpeechConfig.FromSubscription(options.Key, options.Region);
    }

    private sealed class AzureSpeechRecognitionSession : ISpeechRecognitionSession
    {
        private readonly string meetingId;
        private readonly PushAudioInputStream pushStream;
        private readonly AudioConfig audioConfig;
        private readonly SpeechRecognizer recognizer;
        private readonly Func<string, CancellationToken, Task> onFinalTranscript;
        private readonly Func<string, CancellationToken, Task> onError;
        private bool disposed;

        public AzureSpeechRecognitionSession(
            string meetingId,
            SpeechConfig speechConfig,
            PushAudioInputStream pushStream,
            AudioConfig audioConfig,
            SpeechRecognizer recognizer,
            Func<string, CancellationToken, Task> onFinalTranscript,
            Func<string, CancellationToken, Task> onError)
        {
            this.meetingId = meetingId;
            _ = speechConfig;
            this.pushStream = pushStream;
            this.audioConfig = audioConfig;
            this.recognizer = recognizer;
            this.onFinalTranscript = onFinalTranscript;
            this.onError = onError;

            this.recognizer.Recognized += (_, args) =>
            {
                if (args.Result.Reason == ResultReason.RecognizedSpeech
                    && !string.IsNullOrWhiteSpace(args.Result.Text))
                {
                    _ = this.onFinalTranscript(args.Result.Text.Trim(), CancellationToken.None);
                }
            };
            this.recognizer.Canceled += (_, args) =>
            {
                var message = $"Azure Speech recognition canceled for {this.meetingId}: {args.Reason} {args.ErrorDetails}".Trim();
                _ = this.onError(message, CancellationToken.None);
            };
        }

        public async Task<ISpeechRecognitionSession> StartAsync(CancellationToken cancellationToken)
        {
            await recognizer.StartContinuousRecognitionAsync().WaitAsync(cancellationToken);
            return this;
        }

        public Task PushAudioAsync(byte[] pcmAudio, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (disposed || pcmAudio.Length == 0)
            {
                return Task.CompletedTask;
            }

            pushStream.Write(pcmAudio);
            return Task.CompletedTask;
        }

        public async ValueTask DisposeAsync()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            pushStream.Close();
            await recognizer.StopContinuousRecognitionAsync();
            recognizer.Dispose();
            audioConfig.Dispose();
            pushStream.Dispose();
        }
    }
}
