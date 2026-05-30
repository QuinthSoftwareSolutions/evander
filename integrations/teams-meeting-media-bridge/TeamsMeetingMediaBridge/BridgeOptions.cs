using Microsoft.Extensions.Configuration;

namespace TeamsMeetingMediaBridge;

public enum BridgeAdapterMode
{
    InMemory,
    Graph,
}

public enum BridgeSpeechMode
{
    AzureSpeech,
    OpenAIRealtime,
}

public sealed record BridgeOptions(
    BridgeAdapterMode AdapterMode,
    BridgeSpeechMode SpeechMode,
    string DefaultVoice,
    GraphTeamsMediaOptions? Graph,
    AzureSpeechOptions? AzureSpeech,
    OpenAIRealtimeOptions? OpenAIRealtime)
{
    public static BridgeOptions FromConfiguration(IConfiguration configuration)
    {
        var modeText = Value(configuration, "Bridge:AdapterMode", "BRIDGE_ADAPTER_MODE", "InMemory");
        if (!Enum.TryParse<BridgeAdapterMode>(modeText, ignoreCase: true, out var mode))
        {
            throw new InvalidOperationException("Bridge:AdapterMode must be InMemory or Graph.");
        }

        var speechModeText = Value(configuration, "Bridge:SpeechMode", "BRIDGE_SPEECH_MODE", "AzureSpeech");
        if (!Enum.TryParse<BridgeSpeechMode>(speechModeText, ignoreCase: true, out var speechMode))
        {
            throw new InvalidOperationException("Bridge:SpeechMode must be AzureSpeech or OpenAIRealtime.");
        }

        if (mode == BridgeAdapterMode.InMemory)
        {
            return new BridgeOptions(
                mode,
                BridgeSpeechMode.AzureSpeech,
                Optional(configuration, "AzureSpeech:DefaultVoice", "AZURE_SPEECH_DEFAULT_VOICE")
                    ?? "en-US-JennyNeural",
                null,
                null,
                null);
        }

        var graph = GraphTeamsMediaOptions.FromConfiguration(configuration);
        if (speechMode == BridgeSpeechMode.OpenAIRealtime)
        {
            var realtime = OpenAIRealtimeOptions.FromConfiguration(configuration);
            return new BridgeOptions(mode, speechMode, realtime.DefaultVoice, graph, null, realtime);
        }

        var speech = AzureSpeechOptions.FromConfiguration(configuration);
        return new BridgeOptions(mode, speechMode, speech.DefaultVoice, graph, speech, null);
    }

    private static string Value(
        IConfiguration configuration,
        string configKey,
        string envKey,
        string defaultValue)
    {
        return (configuration[configKey] ?? Environment.GetEnvironmentVariable(envKey) ?? defaultValue).Trim();
    }

    private static string? Optional(IConfiguration configuration, string configKey, string envKey)
    {
        var value = configuration[configKey] ?? Environment.GetEnvironmentVariable(envKey);
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }
}

public sealed record GraphTeamsMediaOptions(
    string BotAppId,
    string BotAppSecret,
    string TenantId,
    Uri PublicCallbackBaseUrl,
    string ServiceFqdn,
    string PublicIp,
    int PublicPort,
    int InternalPort,
    string CertificateThumbprint,
    string OnlineMeetingLookupUserId,
    int? MediaPortRangeStart,
    int? MediaPortRangeEnd)
{
    public static GraphTeamsMediaOptions FromConfiguration(IConfiguration configuration)
    {
        var options = new GraphTeamsMediaOptions(
            Required(configuration, "Graph:BotAppId", "TEAMS_BOT_APP_ID"),
            Required(configuration, "Graph:BotAppSecret", "TEAMS_BOT_APP_SECRET"),
            Required(configuration, "Graph:TenantId", "TEAMS_BOT_TENANT_ID"),
            new Uri(Required(configuration, "Graph:PublicCallbackBaseUrl", "TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL")),
            Required(configuration, "Graph:ServiceFqdn", "TEAMS_BOT_SERVICE_FQDN"),
            Required(configuration, "Graph:PublicIp", "TEAMS_BOT_PUBLIC_IP"),
            RequiredInt(configuration, "Graph:PublicPort", "TEAMS_BOT_PUBLIC_PORT"),
            RequiredInt(configuration, "Graph:InternalPort", "TEAMS_BOT_INTERNAL_PORT"),
            Required(configuration, "Graph:CertificateThumbprint", "TEAMS_BOT_CERT_THUMBPRINT"),
            Required(configuration, "Graph:OnlineMeetingLookupUserId", "TEAMS_ONLINE_MEETING_LOOKUP_USER_ID"),
            OptionalInt(configuration, "Graph:MediaPortRangeStart", "TEAMS_BOT_MEDIA_PORT_RANGE_START"),
            OptionalInt(configuration, "Graph:MediaPortRangeEnd", "TEAMS_BOT_MEDIA_PORT_RANGE_END"));

        if (options.MediaPortRangeStart.HasValue != options.MediaPortRangeEnd.HasValue)
        {
            throw new InvalidOperationException(
                "Both TEAMS_BOT_MEDIA_PORT_RANGE_START and TEAMS_BOT_MEDIA_PORT_RANGE_END are required when either is set.");
        }

        return options;
    }

    private static string Required(IConfiguration configuration, string configKey, string envKey)
    {
        var value = configuration[configKey] ?? Environment.GetEnvironmentVariable(envKey);
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"{envKey} is required for Bridge:AdapterMode=Graph.");
        }

        return value.Trim();
    }

    private static int RequiredInt(IConfiguration configuration, string configKey, string envKey)
    {
        var value = Required(configuration, configKey, envKey);
        if (!int.TryParse(value, out var parsed) || parsed <= 0)
        {
            throw new InvalidOperationException($"{envKey} must be a positive integer.");
        }

        return parsed;
    }

    private static int? OptionalInt(IConfiguration configuration, string configKey, string envKey)
    {
        var value = configuration[configKey] ?? Environment.GetEnvironmentVariable(envKey);
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }

        if (!int.TryParse(value.Trim(), out var parsed) || parsed <= 0)
        {
            throw new InvalidOperationException($"{envKey} must be a positive integer when set.");
        }

        return parsed;
    }
}

public sealed record AzureSpeechOptions(
    string Key,
    string? Region,
    Uri? Endpoint,
    string RecognitionLanguage,
    string DefaultVoice)
{
    public static AzureSpeechOptions FromConfiguration(IConfiguration configuration)
    {
        var key = Required(configuration, "AzureSpeech:Key", "AZURE_SPEECH_KEY");
        var region = Optional(configuration, "AzureSpeech:Region", "AZURE_SPEECH_REGION");
        var endpointText = Optional(configuration, "AzureSpeech:Endpoint", "AZURE_SPEECH_ENDPOINT");
        if (string.IsNullOrWhiteSpace(region) && string.IsNullOrWhiteSpace(endpointText))
        {
            throw new InvalidOperationException(
                "AZURE_SPEECH_REGION or AZURE_SPEECH_ENDPOINT is required for Bridge:AdapterMode=Graph.");
        }

        return new AzureSpeechOptions(
            key,
            region,
            string.IsNullOrWhiteSpace(endpointText) ? null : new Uri(endpointText),
            Optional(configuration, "AzureSpeech:RecognitionLanguage", "AZURE_SPEECH_RECOGNITION_LANGUAGE") ?? "en-US",
            Optional(configuration, "AzureSpeech:DefaultVoice", "AZURE_SPEECH_DEFAULT_VOICE") ?? "en-US-JennyNeural");
    }

    private static string Required(IConfiguration configuration, string configKey, string envKey)
    {
        var value = Optional(configuration, configKey, envKey);
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"{envKey} is required for Bridge:AdapterMode=Graph.");
        }

        return value;
    }

    private static string? Optional(IConfiguration configuration, string configKey, string envKey)
    {
        var value = configuration[configKey] ?? Environment.GetEnvironmentVariable(envKey);
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }
}

public sealed record OpenAIRealtimeOptions(
    string ApiKey,
    Uri BaseUrl,
    string Model,
    string DefaultVoice,
    string TurnDetection,
    string TranscriptionModel)
{
    public static OpenAIRealtimeOptions FromConfiguration(IConfiguration configuration)
    {
        var apiKey = Optional(configuration, "OpenAIRealtime:ApiKey", "OPENAI_REALTIME_API_KEY")
            ?? Environment.GetEnvironmentVariable("OPENAI_API_KEY");
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            throw new InvalidOperationException(
                "OPENAI_REALTIME_API_KEY or OPENAI_API_KEY is required for Bridge:SpeechMode=OpenAIRealtime.");
        }

        var baseUrl = Optional(configuration, "OpenAIRealtime:BaseUrl", "OPENAI_REALTIME_BASE_URL")
            ?? "wss://api.openai.com/v1/realtime";
        var uri = new Uri(baseUrl);
        if (uri.Scheme != Uri.UriSchemeWs && uri.Scheme != Uri.UriSchemeWss)
        {
            throw new InvalidOperationException("OPENAI_REALTIME_BASE_URL must use ws:// or wss://.");
        }

        var turnDetection = Optional(
            configuration,
            "OpenAIRealtime:TurnDetection",
            "OPENAI_REALTIME_TURN_DETECTION") ?? "semantic_vad";
        if (!string.Equals(turnDetection, "semantic_vad", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(turnDetection, "server_vad", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "OPENAI_REALTIME_TURN_DETECTION must be semantic_vad or server_vad.");
        }

        return new OpenAIRealtimeOptions(
            apiKey.Trim(),
            uri,
            Optional(configuration, "OpenAIRealtime:Model", "OPENAI_REALTIME_MODEL") ?? "gpt-realtime-2",
            Optional(configuration, "OpenAIRealtime:Voice", "OPENAI_REALTIME_VOICE") ?? "marin",
            turnDetection.Trim().ToLowerInvariant(),
            Optional(
                configuration,
                "OpenAIRealtime:TranscriptionModel",
                "OPENAI_REALTIME_TRANSCRIPTION_MODEL") ?? "gpt-4o-transcribe");
    }

    private static string? Optional(IConfiguration configuration, string configKey, string envKey)
    {
        var value = configuration[configKey] ?? Environment.GetEnvironmentVariable(envKey);
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }
}
