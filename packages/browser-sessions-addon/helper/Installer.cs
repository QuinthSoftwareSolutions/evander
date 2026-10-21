using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace Evander.BrowserHelper;

static class Installer
{
    const string Host = "com.evander.browser_helper";
    static string LocalData
    {
        get
        {
            string path = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData, Environment.SpecialFolderOption.DoNotVerify);
            if (!Path.IsPathFullyQualified(path)) throw new Denied("installation_failed");
            return path;
        }
    }
    public static string Root => Path.Combine(LocalData, "Evander", "BrowserHelper");
    static string ConfigRoot => Environment.GetEnvironmentVariable("XDG_CONFIG_HOME") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config");
    static IEnumerable<string> Manifests()
    {
        if (OperatingSystem.IsWindows()) { yield return Path.Combine(Root, "native-host.json"); yield break; }
        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        foreach (var browser in new[] { "google-chrome", "google-chrome-for-testing", "chromium" })
        {
            string parent = OperatingSystem.IsMacOS() ? Path.Combine(home, "Library", "Application Support", browser switch
                { "google-chrome" => "Google/Chrome", "google-chrome-for-testing" => "Google/ChromeForTesting", _ => "Chromium" }) : Path.Combine(ConfigRoot, browser);
            yield return Path.Combine(parent, "NativeMessagingHosts", Host + ".json");
        }
    }
    static string DefaultProfile()
    {
        if (OperatingSystem.IsWindows()) return Path.Combine(LocalData, "Google", "Chrome", "User Data");
        if (OperatingSystem.IsMacOS()) return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Application Support", "Google", "Chrome");
        return Path.Combine(ConfigRoot, "google-chrome");
    }
    public static async Task<int> Run(string[] args)
    {
        if (args.FirstOrDefault() == "--uninstall")
        {
            foreach (var file in Manifests()) File.Delete(file);
            if (OperatingSystem.IsWindows())
            {
                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Google\Chrome\NativeMessagingHosts\" + Host, false);
                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\EvanderBrowserHelper", false);
                // Run from outside the installation so the running executable can exit
                // before its files are removed. No system-wide policy is changed.
                string script = Path.Combine(Path.GetTempPath(), "evander-helper-uninstall-" + Guid.NewGuid().ToString("N") + ".ps1");
                await File.WriteAllTextAsync(script, "Start-Sleep -Seconds 2\n$helperRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Evander/BrowserHelper'\nRemove-Item -LiteralPath $helperRoot -Recurse -Force\nRemove-Item -LiteralPath $PSCommandPath -Force\n");
                var start = new ProcessStartInfo("powershell.exe") { UseShellExecute = false, CreateNoWindow = true };
                foreach (var value in new[] { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script }) start.ArgumentList.Add(value);
                Process.Start(start);
            }
            else if (Directory.Exists(Root)) Directory.Delete(Root, recursive: true);
            Console.WriteLine("Evander Browser Helper unregistered. End sharing in Chrome to release any running helper.");
            return 0;
        }
        string? id = args.SkipWhile(value => value != "--install").Skip(1).FirstOrDefault();
        if (id is null) { Console.Write("Extension ID shown in the Evander sidebar: "); id = Console.ReadLine()?.Trim(); }
        if (id is null || !Regex.IsMatch(id, "^[a-p]{32}$")) throw new Denied("invalid_extension_id");
        string executable = Environment.ProcessPath ?? throw new Denied("installation_failed");
        string binary = Path.Combine(Root, OperatingSystem.IsWindows() ? "evander-browser-helper.exe" : "evander-browser-helper");
        Directory.CreateDirectory(Root);
        // Release packages contain one self-contained executable, with no SDK/runtime requirement.
        if (Path.GetFullPath(executable) != Path.GetFullPath(binary)) File.Copy(executable, binary, overwrite: true);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(binary, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        string settingsFile = Path.Combine(Root, "helper.json");
        var settings = File.Exists(settingsFile) ? JsonNode.Parse(await File.ReadAllTextAsync(settingsFile))!.AsObject() : new JsonObject();
        var allowed = settings["allowed_origins"]?.AsArray().Select(value => value!.GetValue<string>()).Where(value => Regex.IsMatch(value, "^chrome-extension://[a-p]{32}/$")).ToHashSet() ?? [];
        allowed.Add("chrome-extension://" + id + "/");
        int profileOption = Array.IndexOf(args, "--browser-data-dir");
        string profile = profileOption >= 0 ? Path.GetFullPath(args[profileOption + 1]) : settings["browser_data_dir"]?.GetValue<string>() ?? DefaultProfile();
        settings["allowed_origins"] = new JsonArray(allowed.Select(value => JsonValue.Create(value)).ToArray());
        settings["browser_data_dir"] = profile;
        await File.WriteAllTextAsync(settingsFile, settings.ToJsonString());
        foreach (string manifest in Manifests())
        {
            Directory.CreateDirectory(Path.GetDirectoryName(manifest)!);
            var value = new JsonObject { ["name"] = Host, ["description"] = "Evander Browser Helper", ["path"] = binary,
                ["type"] = "stdio", ["allowed_origins"] = settings["allowed_origins"]!.DeepClone() };
            await File.WriteAllTextAsync(manifest, value.ToJsonString());
            if (OperatingSystem.IsWindows())
            {
                using var key = Registry.CurrentUser.CreateSubKey(@"Software\Google\Chrome\NativeMessagingHosts\" + Host);
                key.SetValue("", manifest);
            }
        }
        if (OperatingSystem.IsWindows())
        {
            using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\EvanderBrowserHelper");
            key.SetValue("DisplayName", "Evander Browser Helper"); key.SetValue("DisplayVersion", "0.4.0");
            key.SetValue("UninstallString", "\"" + binary + "\" --uninstall"); key.SetValue("NoModify", 1); key.SetValue("NoRepair", 1);
        }
        Console.WriteLine("Installed for your user. Enable chrome://inspect/#remote-debugging, select Installed helper in the Evander sidebar, and approve Chrome's prompt.");
        Console.WriteLine("Browser data directory: " + profile);
        return 0;
    }
}
