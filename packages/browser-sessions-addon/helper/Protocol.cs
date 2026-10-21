using System.Buffers.Binary;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Evander.BrowserHelper;

sealed class Denied(string code) : Exception(code);

static class Wire
{
    static readonly SemaphoreSlim Output = new(1);
    public static async Task<JsonObject?> Read(Stream input, CancellationToken token)
    {
        var header = new byte[4];
        int first = await input.ReadAsync(header, token);
        if (first == 0) return null;
        await input.ReadExactlyAsync(header.AsMemory(first), token);
        int length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length is < 2 or > 262144) throw new Denied("invalid_request");
        var body = new byte[length]; await input.ReadExactlyAsync(body, token);
        return JsonNode.Parse(body, documentOptions: new JsonDocumentOptions { MaxDepth = 24 }) as JsonObject
            ?? throw new Denied("invalid_request");
    }
    public static async Task Write(object message, CancellationToken token, object? authorityLock = null, Action? validate = null)
    {
        var body = JsonSerializer.SerializeToUtf8Bytes(message);
        if (body.Length > 1000000) throw new Denied("image_too_large");
        var frame = new byte[body.Length + 4]; BinaryPrimitives.WriteInt32LittleEndian(frame, body.Length);
        body.CopyTo(frame, 4);
        await Output.WaitAsync(token);
        try
        {
            var output = Console.OpenStandardOutput();
            ValueTask write;
            lock (authorityLock ?? Output)
            {
                validate?.Invoke();
                write = output.WriteAsync(frame.AsMemory(), token);
            }
            await write; await output.FlushAsync(token);
        }
        finally { Output.Release(); }
    }
}

sealed class Cdp : IAsyncDisposable
{
    readonly ClientWebSocket socket = new();
    readonly SemaphoreSlim sending = new(1);
    readonly ConcurrentDictionary<int, TaskCompletionSource<JsonObject>> pending = new();
    readonly CancellationTokenSource lifetime = new();
    int sequence;
    Task? reader;
    int disposed;
    public Action<JsonObject>? Event;
    public Action? Disconnected;
    public async Task Connect(Uri endpoint, CancellationToken token)
    {
        // The endpoint always comes from a local Chrome profile's port file.
        if (endpoint.Scheme != "ws" || endpoint.Host != "127.0.0.1") throw new Denied("invalid_request");
        socket.Options.SetRequestHeader("User-Agent", "Evander Browser Helper/0.4.0");
        socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(10);
        socket.Options.KeepAliveTimeout = TimeSpan.FromSeconds(10);
        await socket.ConnectAsync(endpoint, token);
        reader = Receive();
    }
    public async Task<JsonObject> Call(string method, JsonObject? parameters = null, string? session = null,
        object? authorityLock = null, Action? validate = null)
    {
        int id = Interlocked.Increment(ref sequence);
        var result = new TaskCompletionSource<JsonObject>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (pending.Count >= 32) throw new Denied("helper_unavailable");
        pending[id] = result;
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        timeout.CancelAfter(TimeSpan.FromSeconds(12));
        try
        {
            var body = JsonSerializer.SerializeToUtf8Bytes(new { id, method, @params = parameters ?? [], sessionId = session },
                new JsonSerializerOptions { DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull });
            await sending.WaitAsync(timeout.Token);
            try
            {
                Task write;
                lock (authorityLock ?? sending)
                {
                    validate?.Invoke();
                    write = socket.SendAsync(new ArraySegment<byte>(body), WebSocketMessageType.Text, true, timeout.Token);
                }
                await write;
            }
            finally { sending.Release(); }
            return await result.Task.WaitAsync(timeout.Token);
        }
        catch (OperationCanceledException) { throw new Denied("helper_timeout"); }
        finally { pending.TryRemove(id, out _); }
    }
    async Task Receive()
    {
        try
        {
            var buffer = new byte[65536];
            while (!lifetime.IsCancellationRequested)
            {
                // Wide application AX trees are compacted to 600 controls before Native Messaging.
                using var data = new MemoryStream();
                WebSocketReceiveResult frame;
                do
                {
                    frame = await socket.ReceiveAsync(buffer, lifetime.Token);
                    if (frame.MessageType == WebSocketMessageType.Close) return;
                    if (frame.MessageType != WebSocketMessageType.Text || data.Length + frame.Count > 33554432) throw new Denied("invalid_request");
                    data.Write(buffer, 0, frame.Count);
                } while (!frame.EndOfMessage);
                var message = JsonNode.Parse(data.GetBuffer().AsSpan(0, (int)data.Length))!.AsObject();
                if (message["id"] is JsonValue value && value.TryGetValue<int>(out int id))
                {
                    if (pending.TryRemove(id, out var waiter))
                    {
                        if (message["error"] is not null) waiter.TrySetException(new Denied("browser_error"));
                        else waiter.TrySetResult(message["result"]?.AsObject() ?? []);
                    }
                }
                else Event?.Invoke(message);
            }
        }
        catch (Exception error) when (error is WebSocketException or OperationCanceledException or JsonException or Denied or InvalidOperationException) { }
        finally
        {
            foreach (var waiter in pending.Values) waiter.TrySetException(new Denied("disconnected"));
            Disconnected?.Invoke();
        }
    }
    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        lifetime.Cancel(); socket.Abort();
        if (reader is not null) await reader;
        socket.Dispose(); lifetime.Dispose();
    }
}
