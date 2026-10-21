using System.Text.Json;
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
    int streamSize;
    const int MaxImageCharacters = 800000; // Base64 plus JSON must fit Chrome's 1 MB native-message limit.
    static readonly (int Width, int Height, int Quality)[] ImageSizes = [(1920, 1080, 65), (1280, 720, 50), (800, 450, 35), (400, 225, 25)];
    static JsonObject StreamParameters(int index)
    {
        var size = ImageSizes[index];
        return new() { ["format"] = "jpeg", ["quality"] = size.Quality, ["maxWidth"] = size.Width, ["maxHeight"] = size.Height };
    }
    readonly Channel<JsonObject> events;
    readonly CancellationTokenSource lifetime = new();
    readonly Task eventPump;
    readonly Task watchdog;
    public Browser(string profile)
    {
        this.profile = profile;
        events = Channel.CreateBounded<JsonObject>(new BoundedChannelOptions(8) { FullMode = BoundedChannelFullMode.DropOldest },
            dropped => { if (dropped["method"]?.GetValue<string>() == "Page.screencastFrame") _ = Acknowledge(dropped); });
        eventPump = Pump(); watchdog = Watch();
    }
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
    static JsonObject Accessibility(JsonObject tree)
    {
        static string? Role(JsonObject node) => node["role"]?["value"]?.GetValue<string>() switch { "LayoutTableRow" => "row", "LayoutTableCell" or "cell" or "rowheader" or "columnheader" => "gridcell", var value => value };
        var roles = new HashSet<string> { "button", "link", "textbox", "combobox", "checkbox", "radio", "tab", "menuitem", "option", "heading", "dialog", "alert", "gridcell", "row" };
        var stateNames = new HashSet<string> { "checked", "selected", "expanded", "disabled", "readonly", "focused", "editable", "required" };
        var all = tree["nodes"]!.AsArray().Select(n => n!.AsObject()).ToArray();
        var byId = all.Where(n => n["nodeId"] is not null).ToDictionary(n => n["nodeId"]!.GetValue<string>());
        static string Text(JsonNode? value, int maximum) { var text = value?.GetValue<string>() ?? ""; return text[..Math.Min(text.Length,maximum)]; }
        var nodes = new JsonArray(); int bytes = 0; bool truncated = false;
        foreach (var node in all)
        {
            string? role = Role(node);
            if (node["ignored"]?.GetValue<bool>() == true || node["backendDOMNodeId"] is null || role is null || !roles.Contains(role)) continue;
            if (nodes.Count == 600) { truncated = true; break; }
            var context = new JsonArray(); var parent = node; int depth = 0;
            while (parent["parentId"] is JsonNode parentId && byId.TryGetValue(parentId.GetValue<string>(), out parent) && depth++<16 && context.Count<3)
            {
                string? parentRole = Role(parent);
                if (parentRole is not ("row" or "dialog" or "form" or "group" or "region")) continue;
                string children = string.Join(" ",(parent["childIds"]?.AsArray() ?? []).Take(24)
                    .Select(id => byId.TryGetValue(id!.GetValue<string>(),out var child) ? Text(child["name"]?["value"],200) : ""));
                context.Add(new JsonObject { ["role"] = parentRole, ["name"] = Text(parent["name"]?["value"],200), ["text"] = children[..Math.Min(children.Length,600)] });
            }
            var properties = new JsonArray();
            foreach (var property in node["properties"]?.AsArray() ?? [])
                if (stateNames.Contains(property?["name"]?.GetValue<string>() ?? "")) properties.Add(property!.DeepClone());
            var compact = new JsonObject { ["backendDOMNodeId"] = node["backendDOMNodeId"]!.DeepClone(),
                ["role"] = new JsonObject { ["value"] = role }, ["name"] = new JsonObject { ["value"] = Text(node["name"]?["value"],400) },
                ["value"] = new JsonObject { ["value"] = Text(node["value"]?["value"],1000) }, ["properties"] = properties, ["evanderContext"] = context };
            // Keep all 600 references within the native envelope even for labels
            // that JSON escapes heavily. Flag semantic truncation for the fast loop.
            while (JsonSerializer.SerializeToUtf8Bytes(compact).Length>1100)
            {
                truncated=true;
                if (context.Count>0) { context.RemoveAt(context.Count-1); continue; }
                string value=compact["value"]!["value"]!.GetValue<string>();
                string name=compact["name"]!["value"]!.GetValue<string>();
                if (value.Length>0) compact["value"]!["value"]=value[..Math.Max(0,value.Length-32)];
                else if (name.Length>0) compact["name"]!["value"]=name[..Math.Max(0,name.Length-32)];
                else break;
            }
            bytes += JsonSerializer.SerializeToUtf8Bytes(compact).Length;
            if (bytes>700000) { truncated = true; break; }
            nodes.Add(compact);
        }
        string text = string.Join(" ",all.Where(n => n["role"]?["value"]?.GetValue<string>()=="StaticText").Select(n => Text(n["name"]?["value"],1000)));
        return new() { ["nodes"] = nodes, ["truncated"] = truncated,
            ["text"] = text[..Math.Min(text.Length,12000)],
            ["title"] = Text(all.FirstOrDefault(n => n["role"]?["value"]?.GetValue<string>()=="RootWebArea")?["name"]?["value"],400) };
    }
    public async Task<JsonObject> Command(string method, JsonObject parameters, long generation, long expectedEpoch, double? commandDeadline = null)
    {
        void Deadline() { if (commandDeadline is double time && (!double.IsFinite(time) || DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000d >= time)) throw new Denied("run_deadline"); }
        Deadline();
        if (!Methods.TryGetValue(method, out var allowed) || parameters.Any(item => !allowed.Contains(item.Key))) throw new Denied("unsupported_action");
        if (parameters.ToJsonString().Length > 65536) throw new Denied("invalid_request");
        // Acknowledgement only releases the selected target's local frame buffer.
        // It must remain possible after origin/control changes, without exposing pixels.
        if (method == "Page.screencastFrameAck") { Guard(); return await cdp!.Call(method, parameters, session); }
        long document = Interlocked.Read(ref epoch); bool input = Inputs.Contains(method);
        if (expectedEpoch != document && method != "Page.screencastFrameAck") throw new Denied("stale_observation");
        await Fence(generation, input, document);
        if (method == "Page.navigate" && Origin(parameters["url"]!.GetValue<string>()) != state!.Origin) throw new Denied("origin_denied");
        double captureWidth = 0, captureHeight = 0;
        if (method == "Page.startScreencast") parameters = StreamParameters(Volatile.Read(ref streamSize));
        if (method == "Page.captureScreenshot")
        {
            var metrics = await cdp!.Call("Page.getLayoutMetrics", session: session);
            var viewport = metrics["cssVisualViewport"]!;
            double width = captureWidth = viewport["clientWidth"]!.GetValue<double>(), height = captureHeight = viewport["clientHeight"]!.GetValue<double>();
            parameters = new() { ["format"] = "jpeg", ["quality"] = 65, ["captureBeyondViewport"] = false,
                ["clip"] = new JsonObject { ["x"] = viewport["pageX"]?.GetValue<double>() ?? 0,
                    ["y"] = viewport["pageY"]?.GetValue<double>() ?? 0, ["width"] = width, ["height"] = height, ["scale"] = Math.Min(1, Math.Min(1920 / width, 1080 / height)) } };
            await Fence(generation, false, document);
        }
        JsonObject result;
        for (int attempt = 0; ; attempt++)
        {
            Deadline();
            Guard(generation, input, document);
            result = await cdp!.Call(method, parameters, session, authorityLock, () =>
            {
                Deadline();
                Guard(generation, input, document);
                if (Origin(currentUrl) != state!.Origin) throw new Denied("origin_denied");
            });
            // Only read-only image captures are retried. Mutations with unknown outcomes never are.
            if (method != "Page.screencastFrameAck") await Fence(generation, input, document);
            if (method != "Page.captureScreenshot" || result["data"]!.GetValue<string>().Length <= MaxImageCharacters) break;
            if (attempt + 1 == ImageSizes.Length) throw new Denied("image_too_large");
            var size = ImageSizes[attempt + 1];
            parameters["quality"] = size.Quality;
            parameters["clip"]!["scale"] = Math.Min(1, Math.Min(size.Width / captureWidth, size.Height / captureHeight));
        }
        if (method == "Accessibility.getFullAXTree") return Accessibility(result);
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
        lock (authorityLock)
        {
            events.Writer.TryWrite(new JsonObject { ["method"] = method, ["params"] = parameters.DeepClone(),
                ["epoch"] = Epoch, ["url"] = currentUrl, ["capture_size"] = Volatile.Read(ref streamSize) });
        }
    }
    async Task Acknowledge(JsonObject message)
    {
        var browser = cdp; var selected = session;
        if (browser is null || selected is null) return;
        try { await browser.Call("Page.screencastFrameAck", new() { ["sessionId"] = message["params"]!["sessionId"]!.DeepClone() }, selected); }
        catch (Exception error) when (error is Denied or ObjectDisposedException or System.Net.WebSockets.WebSocketException) { }
    }
    static (int Width, int Height)? JpegSize(string pixels)
    {
        // Inspect only a bounded JPEG header; never decode page imagery.
        byte[] data;
        try { data = Convert.FromBase64String(pixels[..Math.Min(pixels.Length,16384)]); }
        catch (FormatException) { return null; }
        if (data.Length<4 || data[0]!=255 || data[1]!=216) return null;
        int offset=2;
        while (offset+4<data.Length)
        {
            if (data[offset++]!=255) return null;
            int marker=data[offset++];
            while (marker==255 && offset<data.Length) marker=data[offset++];
            if (marker is 217 or 218) return null;
            if (marker is 216 or 1 || marker is >=208 and <=215) continue;
            if (offset+2>data.Length) return null;
            int length=(data[offset]<<8)|data[offset+1];
            if (length<2 || offset+length>data.Length) return null;
            if (length>=7 && marker is >=192 and <=207 && marker is not (196 or 200 or 204))
                return ((data[offset+5]<<8)|data[offset+6], (data[offset+3]<<8)|data[offset+4]);
            offset+=length;
        }
        return null;
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
                    long document = message["epoch"]!.GetValue<long>();
                    try
                    {
                        // Queued frames retain the setting used when received.
                        // One oversized batch must cause only one downgrade.
                        if (message["capture_size"]!.GetValue<int>() != Volatile.Read(ref streamSize))
                        {
                            await Acknowledge(message); continue;
                        }
                        Guard(document: document);
                        if (Origin(message["url"]!.GetValue<string>()) != state!.Origin || Origin(await Location()) != state!.Origin) throw new Denied("origin_denied");
                        Guard(document: document);
                        if (message["params"]!["data"]!.GetValue<string>().Length > MaxImageCharacters)
                        {
                            await Acknowledge(message);
                            int previous = Volatile.Read(ref streamSize);
                            var dimensions=JpegSize(message["params"]!["data"]!.GetValue<string>());
                            // An in-flight frame can arrive after reconfiguration.
                            // Its old dimensions must not lower the new setting again.
                            if (dimensions is { } size && (size.Width>ImageSizes[previous].Width || size.Height>ImageSizes[previous].Height)) continue;
                            if (previous + 1 < ImageSizes.Length && Interlocked.CompareExchange(ref streamSize, previous + 1, previous) == previous)
                            {
                                await cdp!.Call("Page.stopScreencast", session: session);
                                await cdp.Call("Page.startScreencast", StreamParameters(previous + 1), session);
                            }
                            continue;
                        }
                        await Wire.Write(new { kind = "event", method, epoch = document, @params = message["params"] }, lifetime.Token,
                            authorityLock, () =>
                            {
                                Guard(document: document);
                                if (Origin(message["url"]!.GetValue<string>()) != state!.Origin || Origin(currentUrl) != state!.Origin)
                                    throw new Denied("origin_denied");
                            });
                    }
                    catch (Exception error) when (error is Denied or OperationCanceledException)
                    {
                        await Acknowledge(message);
                    }
                }
                else await Wire.Write(new { kind = "event", method, epoch = message["epoch"]!.GetValue<long>(), @params = message["params"] }, lifetime.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (Denied)
        {
            state = null; lifetime.Cancel();
            await Wire.Write(new { kind = "disconnected" }, CancellationToken.None);
        }
    }
    public async ValueTask DisposeAsync()
    {
        state = null; lifetime.Cancel();
        if (cdp is not null) await cdp.DisposeAsync();
        await eventPump; await watchdog; lifetime.Dispose();
    }
}
