using System.Text.Json.Nodes;
using System.Collections.Concurrent;
using System.Text.RegularExpressions;
using Evander.BrowserHelper;

using var lifetime = new CancellationTokenSource();
try
{
    if (args.Length == 0 || args[0] is "--install" or "--uninstall") return await Installer.Run(args);
    // Chrome verifies allowed_origins too. This second check also protects direct
    // launches and prevents accidental installation with wildcard access.
    var settingsPath = Path.Combine(AppContext.BaseDirectory, "helper.json");
    if (new FileInfo(settingsPath).Length > 16384) return 1;
    var settings = JsonNode.Parse(await File.ReadAllTextAsync(settingsPath))!;
    string? caller = args.FirstOrDefault();
    if (caller is null || !Regex.IsMatch(caller, "^chrome-extension://[a-p]{32}/$") ||
        !settings["allowed_origins"]!.AsArray().Any(value => value?.GetValue<string>() == caller)) return 1;
    string profile = Path.GetFullPath(settings["browser_data_dir"]!.GetValue<string>());
    await using var browser = new Browser(profile);
    using var serial = new SemaphoreSlim(1);
    int active = 0;
    async Task Dispatch(JsonObject message)
    {
        int id = message["id"]!.GetValue<int>();
        bool counted = false;
        try
        {
            var kind = message["kind"]!.GetValue<string>();
            if (kind == "state")
            {
                browser.Update(message["state"]!.AsObject());
                await Wire.Write(new { id, ok = true, epoch = browser.Epoch, result = new { } }, lifetime.Token); return;
            }
            counted = true;
            if (Interlocked.Increment(ref active) > 16) throw new Denied("helper_unavailable");
            await serial.WaitAsync(lifetime.Token);
            try
            {
                object result = new { };
                if (kind == "ping") result = new { version = "0.4.0", features = new { fast_execution = 1 } };
                else if (kind == "open") await browser.Open(message["target_id"]!.GetValue<string>(), message["state"]!.AsObject(), lifetime.Token);
                else if (kind == "command") result = await browser.Command(message["method"]!.GetValue<string>(), message["params"]?.AsObject() ?? [], message["generation"]!.GetValue<long>(), message["epoch"]!.GetValue<long>(), message["deadline_at"]?.GetValue<double>());
                else throw new Denied("unsupported_action");
                await Wire.Write(new { id, ok = true, epoch = browser.Epoch, result }, lifetime.Token);
            }
            finally { serial.Release(); }
        }
        catch (Exception error) when (error is not OutOfMemoryException)
        {
            string code = error is Denied known ? known.Message : "helper_unavailable";
            if (!lifetime.IsCancellationRequested)
            {
                try { await Wire.Write(new { id, ok = false, epoch = browser.Epoch, error = code }, lifetime.Token); }
                catch (Exception output) when (output is IOException or OperationCanceledException) { lifetime.Cancel(); }
            }
        }
        finally { if (counted) Interlocked.Decrement(ref active); }
    }
    var input = Console.OpenStandardInput();
    var requests = new ConcurrentDictionary<int, Task>();
    while (await Wire.Read(input, lifetime.Token) is { } message)
    {
        if (message["id"] is not JsonValue value || !value.TryGetValue<int>(out int id) || id <= 0 ||
            requests.ContainsKey(id) || requests.Count >= 64) throw new Denied("invalid_request");
        var operation = Dispatch(message); requests[id] = operation;
        _ = operation.ContinueWith(_ => requests.TryRemove(id, out var removed), TaskScheduler.Default);
    }
    lifetime.Cancel();
    try { await Task.WhenAll(requests.Values).WaitAsync(TimeSpan.FromSeconds(3)); }
    catch (Exception error) when (error is OperationCanceledException or TimeoutException or IOException) { }
    return 0;
}
catch (Exception error) when (error is not OutOfMemoryException)
{
    // stdout is exclusively the framed Chrome protocol. Do not print paths,
    // URLs, page content or debugging connection errors to the browser/user.
    Console.Error.WriteLine("Evander Browser Helper could not initialize. Check installation and Chrome remote debugging.");
    return 1;
}
