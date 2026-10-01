param(
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [Parameter(Mandatory=$true)][string]$EnvironmentFile,
    [switch]$Preflight
)
$ErrorActionPreference = "Stop"
if (-not [System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Windows)) {
    throw "The Teams media SDK requires a Windows host."
}
if (-not [Environment]::Is64BitProcess) { throw "Use 64-bit PowerShell." }
$environmentEntries = @(Get-Content -LiteralPath $EnvironmentFile | Where-Object {
    $_.Trim() -ne "" -and -not $_.TrimStart().StartsWith("#")
})
$settings = @{}
foreach ($line in $environmentEntries) {
    if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw "Invalid environment file line." }
    if ($settings.ContainsKey($Matches[1])) { throw "Duplicate environment variable: $($Matches[1])" }
    $settings[$Matches[1]] = $Matches[2]
}
foreach ($required in @("TEAMS_BRIDGE_TOKEN", "TEAMS_BOT_APP_ID", "TEAMS_BOT_APP_SECRET", "TEAMS_BOT_TENANT_ID",
    "TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL", "TEAMS_BOT_SERVICE_FQDN", "TEAMS_BOT_PUBLIC_IP", "TEAMS_BOT_PUBLIC_PORT",
    "TEAMS_BOT_INTERNAL_PORT", "TEAMS_BOT_CERT_THUMBPRINT", "VOICE_MEDIA_CONTROLLER_URL", "VOICE_MEDIA_CONTROLLER_TOKEN", "OPENAI_REALTIME_API_KEY")) {
    if (-not $settings[$required] -or $settings[$required].StartsWith("replace-with-")) { throw "Configure $required." }
}
if ($settings["BRIDGE_ADAPTER_MODE"] -ne "Graph" -or $settings["BRIDGE_SPEECH_MODE"] -ne "OpenAIRealtime") {
    throw "Native calls require Graph and OpenAIRealtime adapters."
}
$callback = [Uri]$settings["TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL"]
if ($callback.Scheme -ne "https") { throw "The public callback URL must use HTTPS." }
$certificate = Get-Item "Cert:\LocalMachine\My\$($settings['TEAMS_BOT_CERT_THUMBPRINT'])"
if (-not $certificate.HasPrivateKey) { throw "The media certificate requires its private key." }
$serviceName = "EvanderTeamsMediaBridge"
$existing = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existing -and $existing.Status -ne "Stopped") { throw "Drain calls and stop $serviceName before updating it." }
if ($Preflight) { Write-Output "Local bridge configuration passed preflight; verify Azure networking and tenant consent separately."; exit 0 }
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw "Installation requires an elevated PowerShell." }
$project = Join-Path $PSScriptRoot "TeamsMeetingMediaBridge\TeamsMeetingMediaBridge.csproj"
New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
& icacls.exe $InstallDirectory /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Could not protect installation directory." }
& dotnet publish $project -c Release -r win-x64 --self-contained false -o $InstallDirectory
if ($LASTEXITCODE -ne 0) { throw "Bridge publish failed." }
$binaryPath = '"' + (Join-Path $InstallDirectory "TeamsMeetingMediaBridge.exe") + '"'
if ($existing) {
    & sc.exe config $serviceName binPath= $binaryPath
    if ($LASTEXITCODE -ne 0) { throw "Service update failed." }
} else {
    New-Service -Name $serviceName -BinaryPathName $binaryPath -DisplayName "Evander Teams Media Bridge" -StartupType Manual | Out-Null
}
# Service-specific environment keeps credentials out of command arguments and application logs.
$serviceKey = "HKLM:\SYSTEM\CurrentControlSet\Services\$serviceName"
$registryAcl = [System.Security.AccessControl.RegistrySecurity]::new()
$registryAcl.SetAccessRuleProtection($true, $false)
foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {
    $rule = [System.Security.AccessControl.RegistryAccessRule]::new(
        [System.Security.Principal.SecurityIdentifier]::new($sid),
        [System.Security.AccessControl.RegistryRights]::FullControl,
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)
    $registryAcl.AddAccessRule($rule)
}
Set-Acl -Path $serviceKey -AclObject $registryAcl
New-ItemProperty -Path $serviceKey -Name Environment -PropertyType MultiString -Value $environmentEntries -Force | Out-Null
Write-Output "Bridge installed locally. Start-Service $serviceName when configuration and networking are ready."
