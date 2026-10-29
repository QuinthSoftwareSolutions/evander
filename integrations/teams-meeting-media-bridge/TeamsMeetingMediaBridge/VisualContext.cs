using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace TeamsMeetingMediaBridge;

public sealed record VisualSnapshot(string Source, DateTimeOffset CapturedAt, int Width, int Height, byte[] Jpeg);
public sealed record VisualTurnContext(DateTimeOffset TurnEndedAt, IReadOnlyList<VisualSnapshot> Images,
    string Availability)
{
    public static VisualTurnContext Unavailable(string reason) => new(DateTimeOffset.UtcNow, [], reason);
}

public interface INativeVisualSession
{
    void StartVisualCapture();
    VisualTurnContext CaptureVisualContext();
}

public sealed record RawVideoFrame(string Source, DateTimeOffset CapturedAt, int Width, int Height,
    int Stride, byte[] Nv12, int DisplayWidth = 0, int DisplayHeight = 0);

public interface IVideoSnapshotEncoder
{
    VisualSnapshot Encode(RawVideoFrame frame);
}

/// <summary>One asynchronous encoder per source; no frame queue or recording files.</summary>
public sealed class VisualFrameSampler : IAsyncDisposable
{
    public const int MaxImageBytes = 512 * 1024;
    private readonly object sync = new();
    private readonly IVideoSnapshotEncoder encoder;
    private readonly Func<DateTimeOffset> clock;
    private readonly Dictionary<string, StreamState> streams = new()
        { ["camera"] = new(), ["screen"] = new() };
    private bool disposed;

    public VisualFrameSampler(IVideoSnapshotEncoder encoder, Func<DateTimeOffset>? clock = null)
    { this.encoder = encoder; this.clock = clock ?? (() => DateTimeOffset.UtcNow); }

    // copyFrame runs synchronously while the SDK still owns its unmanaged buffer.
    public void Offer(string source, Func<RawVideoFrame> copyFrame)
    {
        StreamState state;
        long generation;
        lock (sync)
        {
            if (disposed || !streams.TryGetValue(source, out state!)) return;
            var now = clock();
            if (state.Processing || now - state.LastSample < TimeSpan.FromSeconds(1)) return;
            state.LastSample = now;
            state.Processing = true;
            generation = state.Generation;
        }
        RawVideoFrame frame;
        try { frame = copyFrame(); ValidateFrame(frame); }
        catch
        {
            lock (sync) { state.Processing = false; state.Latest = null; }
            return;
        }
        lock (sync)
        {
            if (disposed || generation != state.Generation) { state.Processing = false; return; }
            state.Work = Task.Run(() =>
            {
                VisualSnapshot? snapshot = null;
                try
                {
                    snapshot = encoder.Encode(frame);
                    if (snapshot.Jpeg.Length == 0 || snapshot.Jpeg.Length > MaxImageBytes) snapshot = null;
                }
                catch { /* A failed visual frame does not stop voice. */ }
                lock (sync)
                {
                    if (!disposed && generation == state.Generation) state.Latest = snapshot;
                    state.Processing = false;
                }
            });
        }
    }

    public void Clear(string source)
    {
        lock (sync)
            if (streams.TryGetValue(source, out var state)) { state.Generation++; state.Latest = null; }
    }

    public VisualTurnContext Capture()
    {
        lock (sync)
        {
            var now = clock();
            var images = disposed ? [] : streams.Values.Select(state => state.Latest)
                .Where(image => image is not null && now - image.CapturedAt <= TimeSpan.FromSeconds(5)
                    && image.CapturedAt <= now).Cast<VisualSnapshot>().ToArray();
            var sources = images.Select(image => image.Source).ToHashSet();
            return new VisualTurnContext(now, images,
                $"Camera: {(sources.Contains("camera") ? "available" : "unavailable or stopped")}; " +
                $"Screen: {(sources.Contains("screen") ? "available" : "unavailable or stopped")}");
        }
    }

    public async ValueTask DisposeAsync()
    {
        Task[] work;
        lock (sync)
        {
            disposed = true;
            foreach (var state in streams.Values) { state.Generation++; state.Latest = null; }
            work = streams.Values.Select(state => state.Work).OfType<Task>().ToArray();
        }
        try { await Task.WhenAll(work).WaitAsync(TimeSpan.FromSeconds(2)); }
        catch { /* Encoders cannot publish after disposal. */ }
    }

    public static void ValidateFrame(RawVideoFrame frame)
    {
        var longEdge = frame.Source == "camera" ? 1280 : 1920;
        var shortEdge = frame.Source == "camera" ? 720 : 1080;
        if (frame.Source is not ("camera" or "screen") || frame.Width <= 0 || frame.Height <= 0
            || frame.Width % 2 != 0 || frame.Height % 2 != 0
            || Math.Max(frame.Width, frame.Height) > longEdge || Math.Min(frame.Width, frame.Height) > shortEdge
            || frame.Stride < frame.Width || frame.Stride > 8192
            || frame.Nv12.Length < checked(frame.Stride * frame.Height * 3 / 2))
            throw new ArgumentException("Invalid or oversized NV12 frame.");
    }

    private sealed class StreamState
    {
        public long Generation;
        public bool Processing;
        public DateTimeOffset LastSample = DateTimeOffset.MinValue;
        public VisualSnapshot? Latest;
        public Task? Work;
    }
}

public sealed class WindowsJpegSnapshotEncoder : IVideoSnapshotEncoder
{
    public VisualSnapshot Encode(RawVideoFrame frame)
    {
        if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException("Teams video encoding requires Windows.");
        VisualFrameSampler.ValidateFrame(frame);
        return EncodeWindows(frame);
    }

    // NV12 includes padded rows; use its supplied stride for both Y and UV planes.
    public static byte[] ConvertNv12ToBgr(RawVideoFrame frame)
    {
        VisualFrameSampler.ValidateFrame(frame);
        var output = new byte[frame.Width * frame.Height * 3];
        var uvOffset = frame.Stride * frame.Height;
        for (var y = 0; y < frame.Height; y++)
            for (var x = 0; x < frame.Width; x++)
            {
                var luminance = frame.Nv12[y * frame.Stride + x] - 16;
                var uv = uvOffset + y / 2 * frame.Stride + x / 2 * 2;
                var u = frame.Nv12[uv] - 128;
                var v = frame.Nv12[uv + 1] - 128;
                var index = (y * frame.Width + x) * 3;
                output[index] = (byte)Math.Clamp((298 * luminance + 516 * u + 128) >> 8, 0, 255);
                output[index + 1] = (byte)Math.Clamp((298 * luminance - 100 * u - 208 * v + 128) >> 8, 0, 255);
                output[index + 2] = (byte)Math.Clamp((298 * luminance + 409 * v + 128) >> 8, 0, 255);
            }
        return output;
    }

    [SupportedOSPlatform("windows")]
    private static VisualSnapshot EncodeWindows(RawVideoFrame frame)
    {
        var pixels = ConvertNv12ToBgr(frame);
        using var source = new Bitmap(frame.Width, frame.Height, PixelFormat.Format24bppRgb);
        var data = source.LockBits(new Rectangle(0, 0, source.Width, source.Height), ImageLockMode.WriteOnly,
            PixelFormat.Format24bppRgb);
        try
        {
            for (var y = 0; y < source.Height; y++)
                Marshal.Copy(pixels, y * source.Width * 3, data.Scan0 + y * data.Stride, source.Width * 3);
        }
        finally { source.UnlockBits(data); }
        var displayWidth = frame.DisplayWidth > 0 ? frame.DisplayWidth : frame.Width;
        var displayHeight = frame.DisplayHeight > 0 ? frame.DisplayHeight : frame.Height;
        var scale = Math.Min(1.0, Math.Min((frame.Source == "camera" ? 1280 : 1920) / (double)Math.Max(displayWidth, displayHeight),
            (frame.Source == "camera" ? 720 : 1080) / (double)Math.Min(displayWidth, displayHeight)));
        using var image = new Bitmap(source, Math.Max(1, (int)(displayWidth * scale)), Math.Max(1, (int)(displayHeight * scale)));
        var codec = ImageCodecInfo.GetImageEncoders().Single(codec => codec.MimeType == "image/jpeg");
        foreach (var quality in new long[] { 85, 70, 55, 40 })
        {
            using var output = new MemoryStream();
            using var parameters = new EncoderParameters(1);
            parameters.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, quality);
            image.Save(output, codec, parameters);
            if (output.Length <= VisualFrameSampler.MaxImageBytes)
                return new VisualSnapshot(frame.Source, frame.CapturedAt, image.Width, image.Height, output.ToArray());
        }
        throw new InvalidOperationException("Encoded snapshot exceeds the image limit.");
    }
}
