using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading.Channels;

namespace Evander.BrowserHelper;

sealed record Authority(string Id, string Actor, string? Controller, long Generation, double Expires, string Origin)
{
    public static Authority Parse(JsonObject state)
    {
        if (state["state"]?.GetValue<string>() is not ("starting" or "ready" or "paused")) throw new Denied("session_expired");
        string id = state["id"]!.GetValue<string>(), agent = state["agent_session_id"]!.GetValue<string>();
        var origins = state["allowed_origins"]!.AsArray();
        if (id.Length is < 1 or > 128 || agent.Length is < 1 or > 128 || origins.Count != 1) throw new Denied("invalid_request");
        string origin = Browser.Origin(origins[0]!.GetValue<string>());
        double expires = state["expires_at"]!.GetValue<double>();
        if (!double.IsFinite(expires) || expires > DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 86400) throw new Denied("invalid_request");
        return new(id, "agent:" + agent, state["controller"]?.GetValue<string>(), state["control_generation"]!.GetValue<long>(), expires, origin);
    }
}

sealed class Browser : IAsyncDisposable
{
    readonly string profile;
    public long Epoch => Interlocked.Read(ref epoch);
    Cdp? cdp;
    string? session;
    string? mainFrame;
    string currentUrl = "";
    volatile Authority? state;
    readonly object authorityLock = new();
    string? owner;
    string? agent;
    double deadline = double.MaxValue;
    long generation = -1;
    long epoch;
    readonly Channel<JsonObject> events = Channel.CreateBounded<JsonObject>(new BoundedChannelOptions(8) { FullMode = BoundedChannelFullMode.DropOldest });
    readonly CancellationTokenSource lifetime = new();
    readonly Task eventPump;
    readonly Task watchdog;
    public Browser(string profile) { this.profile = profile; eventPump = Pump(); watchdog = Watch(); }
    async Task Watch()
    {
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                await Task.Delay(1000, lifetime.Token);
                var authority = state;
                if (authority is not null && (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000d >= authority.Expires ||
                    !File.Exists(Path.Combine(AppContext.BaseDirectory, "helper.json"))))
                {
                    state = null; lifetime.Cancel();
                    if (cdp is not null) await cdp.DisposeAsync();
                    await Wire.Write(new { kind = "disconnected" }, CancellationToken.None); return;
                }
            }
        }
        catch (OperationCanceledException) { }
    }

    static readonly Dictionary<string, string[]> Methods = new()
    {
        ["Page.enable"] = [], ["Accessibility.enable"] = [], ["Page.getFrameTree"] = [],
        ["Page.getLayoutMetrics"] = [], ["Accessibility.getFullAXTree"] = [],
        ["Page.captureScreenshot"] = ["format", "quality", "captureBeyondViewport"],
        ["Page.startScreencast"] = ["format", "quality", "maxWidth", "maxHeight"],
        ["Page.screencastFrameAck"] = ["sessionId"], ["Page.stopScreencast"] = [],
        ["DOM.getBoxModel"] = ["backendNodeId"], ["DOM.focus"] = ["backendNodeId"],
        ["DOM.scrollIntoViewIfNeeded"] = ["backendNodeId"],
        ["Input.dispatchMouseEvent"] = ["type", "x", "y", "button", "clickCount", "deltaX", "deltaY"],
        ["Input.dispatchKeyEvent"] = ["type", "key", "code", "modifiers", "windowsVirtualKeyCode", "text", "unmodifiedText"],
        ["Input.insertText"] = ["text"], ["Page.navigate"] = ["url"], ["Page.reload"] = [],
        ["Page.handleJavaScriptDialog"] = ["accept"],
    };
    static readonly HashSet<string> Inputs = ["DOM.focus", "DOM.scrollIntoViewIfNeeded", "Input.dispatchMouseEvent", "Input.dispatchKeyEvent", "Input.insertText", "Page.navigate", "Page.reload", "Page.handleJavaScriptDialog"];
    public static string Origin(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https") || uri.UserInfo.Length != 0) throw new Denied("origin_denied");
        return uri.GetComponents(UriComponents.SchemeAndServer, UriFormat.UriEscaped).TrimEnd('/');
    }
    public void Update(JsonObject value)
    {
        lock (authorityLock)
        {
            state = null; // A malformed/closed update cannot retain old input authority.
            var next = Authority.Parse(value);
            if ((owner is not null && owner != next.Id) || (agent is not null && agent != next.Actor) ||
                next.Expires > deadline || next.Generation < generation) throw new Denied("stale_control");
            owner ??= next.Id; agent ??= next.Actor; deadline = Math.Min(deadline, next.Expires);
            generation = next.Generation; state = next;
        }
    }
    void Guard(long? generation = null, bool input = false, long? document = null)
    {
        var authority = state ?? throw new Denied("disconnected");
        if (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000d >= authority.Expires) throw new Denied("session_expired");
        if (generation is not null && generation != authority.Generation) throw new Denied("stale_control");
        if (input && authority.Controller != authority.Actor) throw new Denied("control_required");
        if (document is not null && document != Interlocked.Read(ref epoch)) throw new Denied("stale_observation");
    }
    async Task<string> Location()
    {
        var result = await cdp!.Call("Page.getFrameTree", session: session);
        var frame = result["frameTree"]!["frame"]!;
        mainFrame = frame["id"]!.GetValue<string>();
        string url = frame["url"]!.GetValue<string>();
        lock (authorityLock)
        {
            if (url != currentUrl) { currentUrl = url; Interlocked.Increment(ref epoch); }
        }
        return url;
    }
    async Task Fence(long generation, bool input, long document)
    {
        Guard(generation, input, document);
        var url = await Location();
        Guard(generation, input, document);
        if (Origin(url) != state!.Origin) throw new Denied("origin_denied");
    }
    public async Task Open(string target, JsonObject authority, CancellationToken cancel)
    {
        if (cdp is not null || !Regex.IsMatch(target, "^[a-fA-F0-9]{16,64}$")) throw new Denied("invalid_request");
        Update(authority); Guard();
        var portFile = Path.Combine(profile, "DevToolsActivePort");
        if (!File.Exists(portFile) || new FileInfo(portFile).Length > 4096) throw new Denied("remote_debugging_unavailable");
        var lines = (await File.ReadAllLinesAsync(portFile, cancel)).Where(line => !string.IsNullOrWhiteSpace(line)).ToArray();
        if (lines.Length != 2 || !int.TryParse(lines[0], out int port) || port is < 1 or > 65535 ||
            !Regex.IsMatch(lines[1], "^/devtools/browser/[a-zA-Z0-9-]{1,128}$")) throw new Denied("remote_debugging_unavailable");
        cdp = new Cdp(); cdp.Event = OnEvent;
        cdp.Disconnected = () => { state = null; lifetime.Cancel(); _ = Wire.Write(new { kind = "disconnected" }, CancellationToken.None); };
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancel, lifetime.Token);
        deadline.CancelAfter(TimeSpan.FromSeconds(75));
        await cdp.Connect(new Uri($"ws://127.0.0.1:{port}{lines[1]}"), deadline.Token);
        var information = await cdp.Call("Target.getTargetInfo", new() { ["targetId"] = target });
        var info = information["targetInfo"]!;
        if (info["type"]!.GetValue<string>() != "page" || Origin(info["url"]!.GetValue<string>()) != state!.Origin) throw new Denied("wrong_browser");
        var attached = await cdp.Call("Target.attachToTarget", new() { ["targetId"] = target, ["flatten"] = true });
        session = attached["sessionId"]!.GetValue<string>();
        currentUrl = await Location();
        await Fence(state!.Generation, false, Interlocked.Read(ref epoch));
    }
    public async Task<JsonObject> Command(string method, JsonObject parameters, long generation, long expectedEpoch)
    {
        if (!Methods.TryGetValue(method, out var allowed) || parameters.Any(item => !allowed.Contains(item.Key))) throw new Denied("unsupported_action");
        if (parameters.ToJsonString().Length > 65536) throw new Denied("invalid_request");
        long document = Interlocked.Read(ref epoch); bool input = Inputs.Contains(method);
        if (expectedEpoch != document && method != "Page.screencastFrameAck") throw new Denied("stale_observation");
        await Fence(generation, input, document);
        if (method == "Page.navigate" && Origin(parameters["url"]!.GetValue<string>()) != state!.Origin) throw new Denied("origin_denied");
        if (method == "Page.startScreencast") parameters = new() { ["format"] = "jpeg", ["quality"] = 65, ["maxWidth"] = 1920, ["maxHeight"] = 1080 };
        if (method == "Page.captureScreenshot")
        {
            var metrics = await cdp!.Call("Page.getLayoutMetrics", session: session);
            var viewport = metrics["cssVisualViewport"]!;
            double width = viewport["clientWidth"]!.GetValue<double>(), height = viewport["clientHeight"]!.GetValue<double>();
            parameters = new() { ["format"] = "jpeg", ["quality"] = 65, ["captureBeyondViewport"] = false,
                ["clip"] = new JsonObject { ["x"] = viewport["pageX"]?.GetValue<double>() ?? 0,
                    ["y"] = viewport["pageY"]?.GetValue<double>() ?? 0, ["width"] = width, ["height"] = height, ["scale"] = Math.Min(1, Math.Min(1920 / width, 1080 / height)) } };
            await Fence(generation, false, document);
        }
        Guard(generation, input, document);
        var result = await cdp!.Call(method, parameters, session, authorityLock, () =>
        {
            Guard(generation, input, document);
            if (Origin(currentUrl) != state!.Origin) throw new Denied("origin_denied");
        });
        // After mutating navigation an unknown outcome is returned, never replayed.
        if (method != "Page.screencastFrameAck") await Fence(generation, input, document);
        if (method == "Page.getFrameTree") return new() { ["frameTree"] = new JsonObject { ["frame"] = result["frameTree"]!["frame"]!.DeepClone() } };
        return result;
    }
    void OnEvent(JsonObject message)
    {
        if (session is not null && message["method"]?.GetValue<string>() == "Target.detachedFromTarget" &&
            message["params"]?["sessionId"]?.GetValue<string>() == session)
        {
            state = null; lifetime.Cancel();
            _ = Wire.Write(new { kind = "disconnected" }, CancellationToken.None);
            _ = Task.Run(async () => { if (cdp is not null) await cdp.DisposeAsync(); });
            return;
        }
        if (session is null || message["sessionId"]?.GetValue<string>() != session) return;
        string? method = message["method"]?.GetValue<string>();
        var parameters = message["params"]?.AsObject() ?? [];
        if (method == "Page.frameNavigated" && parameters["frame"]?["parentId"] is null)
        {
            lock (authorityLock) { currentUrl = parameters["frame"]!["url"]!.GetValue<string>(); Interlocked.Increment(ref epoch); }
        }
        else if (method == "Page.navigatedWithinDocument" && parameters["frameId"]?.GetValue<string>() == mainFrame)
        {
            lock (authorityLock) { currentUrl = parameters["url"]!.GetValue<string>(); Interlocked.Increment(ref epoch); }
        }
        else if (method != "Page.screencastFrame") return;
        events.Writer.TryWrite(new JsonObject { ["method"] = method, ["params"] = parameters.DeepClone() });
    }
    async Task Pump()
    {
        try
        {
            await foreach (var message in events.Reader.ReadAllAsync(lifetime.Token))
            {
                var method = message["method"]!.GetValue<string>();
                if (method == "Page.screencastFrame")
                {
                    long document = Interlocked.Read(ref epoch);
                    try
                    {
                        Guard(document: document);
                        if (Origin(currentUrl) != state!.Origin || Origin(await Location()) != state!.Origin) throw new Denied("origin_denied");
                        Guard(document: document);
                        await Wire.Write(new { kind = "event", method, epoch = Epoch, @params = message["params"] }, lifetime.Token);
                    }
                    catch (Exception error) when (error is Denied or OperationCanceledException)
                    {
                        await cdp!.Call("Page.screencastFrameAck", new() { ["sessionId"] = message["params"]!["sessionId"]!.DeepClone() }, session);
                    }
                }
                else await Wire.Write(new { kind = "event", method, epoch = Epoch, @params = message["params"] }, lifetime.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (Denied) { lifetime.Cancel(); }
    }
    public async ValueTask DisposeAsync()
    {
        state = null; lifetime.Cancel();
        if (cdp is not null) await cdp.DisposeAsync();
        await eventPump; await watchdog; lifetime.Dispose();
    }
}
