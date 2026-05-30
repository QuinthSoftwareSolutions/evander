namespace TeamsMeetingMediaBridge;

internal static class PcmAudio
{
    public const int TeamsSampleRate = 16000;
    public const int RealtimeSampleRate = 24000;
    public const int Teams20MsFrameBytes = 640;

    public static IReadOnlyList<byte[]> ChunkTeamsFrames(byte[] audioBytes)
    {
        if (audioBytes.Length == 0)
        {
            return [];
        }

        var frames = new List<byte[]>((audioBytes.Length + Teams20MsFrameBytes - 1) / Teams20MsFrameBytes);
        for (var offset = 0; offset < audioBytes.Length; offset += Teams20MsFrameBytes)
        {
            var frame = new byte[Teams20MsFrameBytes];
            var count = Math.Min(Teams20MsFrameBytes, audioBytes.Length - offset);
            Buffer.BlockCopy(audioBytes, offset, frame, 0, count);
            frames.Add(frame);
        }

        return frames;
    }

    public static byte[] ResamplePcm16Mono(byte[] audioBytes, int sourceRate, int targetRate)
    {
        if (audioBytes.Length == 0 || sourceRate == targetRate)
        {
            return audioBytes;
        }

        if (sourceRate <= 0 || targetRate <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(sourceRate), "Sample rates must be positive.");
        }

        var sourceSampleCount = audioBytes.Length / 2;
        if (sourceSampleCount == 0)
        {
            return [];
        }

        var targetSampleCount = Math.Max(
            1,
            (int)Math.Round(sourceSampleCount * (double)targetRate / sourceRate, MidpointRounding.AwayFromZero));
        var output = new byte[targetSampleCount * 2];
        for (var index = 0; index < targetSampleCount; index++)
        {
            var sourcePosition = index * (double)sourceRate / targetRate;
            var lowerIndex = Math.Min(sourceSampleCount - 1, (int)Math.Floor(sourcePosition));
            var upperIndex = Math.Min(sourceSampleCount - 1, lowerIndex + 1);
            var fraction = sourcePosition - lowerIndex;
            var lower = ReadInt16LittleEndian(audioBytes, lowerIndex);
            var upper = ReadInt16LittleEndian(audioBytes, upperIndex);
            var sample = (short)Math.Clamp(
                (int)Math.Round(lower + ((upper - lower) * fraction), MidpointRounding.AwayFromZero),
                short.MinValue,
                short.MaxValue);
            WriteInt16LittleEndian(output, index, sample);
        }

        return output;
    }

    private static short ReadInt16LittleEndian(byte[] buffer, int sampleIndex)
    {
        var offset = sampleIndex * 2;
        return (short)(buffer[offset] | (buffer[offset + 1] << 8));
    }

    private static void WriteInt16LittleEndian(byte[] buffer, int sampleIndex, short sample)
    {
        var offset = sampleIndex * 2;
        buffer[offset] = (byte)(sample & 0xff);
        buffer[offset + 1] = (byte)((sample >> 8) & 0xff);
    }
}
