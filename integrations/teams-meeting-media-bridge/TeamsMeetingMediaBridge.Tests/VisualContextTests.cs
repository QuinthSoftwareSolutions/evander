using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using TeamsMeetingMediaBridge;

internal static class VisualContextTests
{
    public static async Task BoundedSamplingAndStop()
    {
        var now = DateTimeOffset.UtcNow;
        var encoder = new BlockingEncoder();
        await using var sampler = new VisualFrameSampler(encoder, () => now);
        var copies = 0;
        RawVideoFrame Copy(string source)
        { copies++; return new RawVideoFrame(source, now, 2, 2, 2, [16, 16, 16, 16, 128, 128]); }
        sampler.Offer("camera", () => Copy("camera"));
        await encoder.Started.Task.WaitAsync(TimeSpan.FromSeconds(3));
        for (var i = 0; i < 100; i++) sampler.Offer("camera", () => Copy("camera"));
        Check(copies == 1, "Unbounded frame copying while the encoder is busy");
        sampler.Clear("camera");
        encoder.Release.SetResult();
        await Until(() => encoder.Completed);
        await Task.Delay(20);
        Check(sampler.Capture().Images.Count == 0, "Stopped stream was revived by an in-flight encoder");
        now = now.AddSeconds(2);
        sampler.Offer("camera", () => Copy("camera"));
        sampler.Offer("screen", () => Copy("screen"));
        await Until(() => sampler.Capture().Images.Count == 2);
        var frozen = sampler.Capture();
        sampler.Clear("screen");
        Check(sampler.Capture().Images.Single().Source == "camera", "Stopping screen sharing cleared camera context");
        Check(frozen.Images.Count == 2, "Frozen caller turn followed a later stream update");
        now = now.AddSeconds(6);
        Check(sampler.Capture().Images.Count == 0, "Stale frame was treated as a current view");
        await sampler.DisposeAsync();
        sampler.Offer("camera", () => throw new Exception("Copy after disposal"));
    }

    public static Task Nv12StrideConversion()
    {
        var frame = new RawVideoFrame("screen", DateTimeOffset.UtcNow, 2, 2, 4,
            [16, 235, 99, 99, 16, 235, 99, 99, 128, 128, 99, 99]);
        var pixels = WindowsJpegSnapshotEncoder.ConvertNv12ToBgr(frame);
        Check(pixels.SequenceEqual(new byte[] { 0, 0, 0, 255, 255, 255, 0, 0, 0, 255, 255, 255 }),
            "NV12 row padding or UV stride changed the decoded colors");
        try { WindowsJpegSnapshotEncoder.ConvertNv12ToBgr(frame with { Stride = 1 }); throw new Exception("Invalid stride accepted"); }
        catch (ArgumentException) { }
        return Task.CompletedTask;
    }

    public static async Task RealtimeImagesAndExactDelegation()
    {
        var socket = new FakeRealtimeWebSocketClient();
        var original = Context(1);
        var current = original;
        var delegated = new TaskCompletionSource<VisualTurnContext>(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var session = await Start(socket, () => current,
            (_, _, _, images, _) => { delegated.TrySetResult(images); return Task.FromResult("confirmed"); });
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_stopped\"}");
        await Until(() => VisualMessages(socket).Count == 1);
        Check(ResponseCount(socket) == 0, "Response preceded image acceptance");
        var message = VisualMessages(socket).Single();
        using (var parsed = JsonDocument.Parse(message))
        {
            var content = parsed.RootElement.GetProperty("item").GetProperty("content");
            Check(content.EnumerateArray().Count(part => part.GetProperty("type").GetString() == "input_image") == 2,
                "Camera and screen were not included together");
        }
        Ack(socket, message);
        await Until(() => ResponseCount(socket) == 1);
        current = Context(2);
        socket.EnqueueIncoming("{\"type\":\"response.created\",\"response\":{\"id\":\"visual-response\"}}");
        var function = new { type = "function_call", name = "query_origin_agent", call_id = "visual-query",
            arguments = "{\"question\":\"Explain the screen\",\"reason\":\"visual work\"}" };
        socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "response.done",
            response = new { id = "visual-response", status = "completed", output = new[] { function } } }));
        var received = await delegated.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Check(received.Images.Select(image => image.Jpeg[0]).SequenceEqual(new byte[] { 1, 1 }),
            "Delegation used newer frames instead of the responding turn's snapshots");
        Check(ReferenceEquals(received, original), "Turn snapshot identity was lost");
    }

    public static async Task InterruptionWhileImagesArePending()
    {
        var socket = new FakeRealtimeWebSocketClient();
        var audio = new FakeMeetingAudioSession();
        var current = Context(1);
        await using var session = await Start(socket, () => current, (_, _, _, _, _) => Task.FromResult("confirmed"), audio);
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_stopped\"}");
        await Until(() => VisualMessages(socket).Count == 1);
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_started\"}");
        await Until(() => audio.ClearCount == 1);
        await session.PushAudioAsync(new byte[640], CancellationToken.None);
        Check(socket.SentMessages.Any(message => message.Contains("input_audio_buffer.append")), "Image acknowledgement blocked audio submission");
        Ack(socket, VisualMessages(socket)[0]);
        await Task.Delay(30);
        Check(ResponseCount(socket) == 0, "Image completion started a response over caller speech");
        current = Context(2);
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_stopped\"}");
        await Until(() => VisualMessages(socket).Count == 2);
        Ack(socket, VisualMessages(socket)[1]);
        await Until(() => ResponseCount(socket) == 1);
        Check(VisualMessages(socket)[1].Contains(Convert.ToBase64String(new byte[] { 2 })), "New turn lost its new view");
    }

    public static async Task RejectedImagesPreserveAudio()
    {
        var socket = new FakeRealtimeWebSocketClient();
        var delegated = new TaskCompletionSource<VisualTurnContext>(TaskCreationOptions.RunContinuationsAsynchronously);
        await using var session = await Start(socket, () => Context(1),
            (_, _, _, images, _) => { delegated.SetResult(images); return Task.FromResult("confirmed"); });
        socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_stopped\"}");
        await Until(() => VisualMessages(socket).Count == 1);
        using var message = JsonDocument.Parse(VisualMessages(socket).Single());
        var eventId = message.RootElement.GetProperty("event_id").GetString();
        socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "error", error = new { event_id = eventId, message = "Rejected image" } }));
        await Until(() => ResponseCount(socket) == 1);
        await session.PushAudioAsync(new byte[640], CancellationToken.None);
        socket.EnqueueIncoming("{\"type\":\"response.created\",\"response\":{\"id\":\"audio-fallback\"}}");
        socket.EnqueueIncoming("{\"type\":\"response.function_call_arguments.done\",\"response_id\":\"audio-fallback\",\"name\":\"query_origin_agent\",\"call_id\":\"query-fallback\",\"arguments\":\"{\\\"question\\\":\\\"Read it\\\",\\\"reason\\\":\\\"visual\\\"}\"}");
        var context = await delegated.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Check(context.Images.Count == 0 && context.Availability.Contains("rejected"), "Rejected image context was delegated as visible");
        Check(socket.SentMessages.Any(value => value.Contains("input_audio_buffer.append")), "Image rejection stopped audio");
    }

    public static async Task ConversationImageHistoryIsBounded()
    {
        var socket = new FakeRealtimeWebSocketClient();
        byte turn = 1;
        await using var session = await Start(socket, () => Context(turn), (_, _, _, _, _) => Task.FromResult("confirmed"));
        for (; turn <= 5; turn++)
        {
            var index = turn - 1;
            socket.EnqueueIncoming("{\"type\":\"input_audio_buffer.speech_stopped\"}");
            await Until(() => VisualMessages(socket).Count == index + 1);
            Ack(socket, VisualMessages(socket)[index]);
            await Until(() => ResponseCount(socket) == index + 1);
            var id = $"turn-{turn}";
            socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "response.created", response = new { id } }));
            socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "response.done", response = new { id, status = "completed", output = Array.Empty<object>() } }));
        }
        using var first = JsonDocument.Parse(VisualMessages(socket)[0]);
        var oldestId = first.RootElement.GetProperty("item").GetProperty("id").GetString();
        var deletions = socket.SentMessages.Where(message => message.Contains("conversation.item.delete")).ToList();
        Check(deletions.Count == 1, "Visual history retained more than four turns or deleted a recent turn");
        using var deletion = JsonDocument.Parse(deletions.Single());
        Check(deletion.RootElement.GetProperty("item_id").GetString() == oldestId, "Visual history deleted a recent turn");
    }

    public static async Task MultipartRetryPreservesSnapshots()
    {
        var handler = new MultipartHandler();
        var client = new ControlPlaneVoiceClient(new HttpClient(handler), new Uri("https://control.example.test"), "synthetic-token");
        var context = Context(7);
        await client.QueryWithVisualsAsync("voice-bound", "Read screen", "visual work", "function-visual", context, CancellationToken.None);
        Check(handler.Attempts == 2 && handler.RequestIds.All(id => id == "function-visual"), "Visual retry changed query identity");
        Check(handler.Bodies.Count == 4 && handler.Bodies.All(body => body.SequenceEqual(new byte[] { 7 })), "Visual retry changed snapshot bytes");
    }

    private static Task<IMeetingSpeechSession> Start(FakeRealtimeWebSocketClient socket, Func<VisualTurnContext> capture,
        Func<string, string, string, VisualTurnContext, CancellationToken, Task<string>> query,
        FakeMeetingAudioSession? audio = null) => new OpenAIRealtimeSpeechAdapter(
            new OpenAIRealtimeOptions("synthetic-key", new Uri("wss://realtime.example.test/v1/realtime"),
                "gpt-realtime-2", "marin", "semantic_vad", "gpt-4o-transcribe"), new FakeRealtimeWebSocketClientFactory(socket))
        .StartAsync(new MeetingSpeechStartRequest("voice-bound", null, audio ?? new FakeMeetingAudioSession(), null, null, null,
            (_, _) => Task.CompletedTask, (error, _) => throw new Exception(error), (_, _, _, _, _) => Task.CompletedTask,
            (_, _, _, _) => Task.FromResult("confirmed"), capture, query), CancellationToken.None);

    private static VisualTurnContext Context(byte pixel) => new(DateTimeOffset.UtcNow,
        [new("camera", DateTimeOffset.UtcNow, 2, 2, [pixel]), new("screen", DateTimeOffset.UtcNow, 2, 2, [pixel])],
        "Camera: available; Screen: available");
    private static List<string> VisualMessages(FakeRealtimeWebSocketClient socket) => socket.SentMessages
        .Where(message => message.Contains("input_image")).ToList();
    private static int ResponseCount(FakeRealtimeWebSocketClient socket) => socket.SentMessages.Count(message => message.Contains("response.create"));
    private static void Ack(FakeRealtimeWebSocketClient socket, string message)
    {
        using var parsed = JsonDocument.Parse(message);
        var id = parsed.RootElement.GetProperty("item").GetProperty("id").GetString();
        socket.EnqueueIncoming(JsonSerializer.Serialize(new { type = "conversation.item.added", item = new { id } }));
    }
    private static void Check(bool condition, string message) { if (!condition) throw new Exception(message); }
    private static async Task Until(Func<bool> condition)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        while (!condition()) await Task.Delay(10, timeout.Token);
    }

    private sealed class BlockingEncoder : IVideoSnapshotEncoder
    {
        public TaskCompletionSource Started = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Release = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public bool Completed;
        public VisualSnapshot Encode(RawVideoFrame frame)
        {
            Started.TrySetResult();
            Release.Task.GetAwaiter().GetResult();
            Completed = true;
            return new VisualSnapshot(frame.Source, frame.CapturedAt, frame.Width, frame.Height, [1]);
        }
    }

    private sealed class MultipartHandler : HttpMessageHandler
    {
        public int Attempts;
        public List<string?> RequestIds = [];
        public List<byte[]> Bodies = [];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
        {
            Check(request.Headers.Authorization?.Parameter == "synthetic-token", "Visual upload lost authentication");
            Check(request.Content is MultipartFormDataContent, "Snapshots were put into query JSON");
            foreach (var content in (MultipartFormDataContent)request.Content!)
            {
                var name = content.Headers.ContentDisposition?.Name?.Trim('"');
                if (name == "payload")
                {
                    using var parsed = JsonDocument.Parse(await content.ReadAsStringAsync(token));
                    RequestIds.Add(parsed.RootElement.GetProperty("request_id").GetString());
                    Check(parsed.RootElement.GetProperty("images").GetArrayLength() == 2, "Snapshot metadata missing");
                }
                else
                {
                    Check(name is "camera" or "screen" && content.Headers.ContentType?.MediaType == "image/jpeg", "Incorrect visual part");
                    Bodies.Add(await content.ReadAsByteArrayAsync(token));
                }
            }
            Attempts++;
            return Attempts == 1 ? new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = JsonContent.Create(new { query_id = "query-1", status = "answered" }) };
        }
    }
}
