using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Headers;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using Microsoft.Graph.Communications.Calls;
using Microsoft.Graph.Communications.Calls.Media;
using Microsoft.Graph.Communications.Client;
using Microsoft.Graph.Communications.Client.Authentication;
using Microsoft.Graph.Communications.Common.Telemetry;
using Microsoft.Graph.Communications.Common.Telemetry.Obfuscation;
using Microsoft.Graph.Models;
using Microsoft.Identity.Client;
using Microsoft.IdentityModel.JsonWebTokens;
using Microsoft.IdentityModel.Protocols;
using Microsoft.IdentityModel.Protocols.OpenIdConnect;
using Microsoft.IdentityModel.Tokens;
using Microsoft.Skype.Bots.Media;

namespace TeamsMeetingMediaBridge;

public interface IGraphMeetingClient
{
    Task<OnlineMeetingCoordinates> ResolveJoinUrlAsync(
        string joinUrl,
        CancellationToken cancellationToken);

    Task SetRecordingStatusAsync(
        string callId,
        bool isRecording,
        CancellationToken cancellationToken);
}

public interface IApplicationHostedMediaClient
{
    Task<ApplicationHostedMediaSession> JoinAsync(
        string externalMeetingId,
        OnlineMeetingCoordinates coordinates,
        CancellationToken cancellationToken);
}

public interface IGraphCallNotificationProcessor
{
    Task<HttpResponseMessage> ProcessNotificationAsync(HttpRequestMessage request);
}

public sealed record ApplicationHostedMediaSession(
    string MeetingId,
    string CallId,
    IMeetingAudioSession AudioSession);

public sealed class GraphTeamsMediaAdapter : ITeamsMediaAdapter
{
    private const int Pcm16k20MsFrameBytes = 640;

    private readonly IGraphMeetingClient graphClient;
    private readonly IApplicationHostedMediaClient mediaClient;
    private readonly ConcurrentDictionary<string, GraphMeetingRuntime> meetings = new(StringComparer.Ordinal);

    public GraphTeamsMediaAdapter(
        IGraphMeetingClient graphClient,
        IApplicationHostedMediaClient mediaClient)
    {
        this.graphClient = graphClient;
        this.mediaClient = mediaClient;
    }

    public event EventHandler<MediaTranscriptReceivedEvent>? TranscriptReceived;

    public event EventHandler<MediaErrorReceivedEvent>? ErrorReceived;

    public event EventHandler<MediaSpeechStatusEvent>? SpeechStatusChanged;

    public async Task<MediaJoinResult> JoinAsync(
        string joinUrl,
        string externalMeetingId,
        CancellationToken cancellationToken)
    {
        var coordinates = await graphClient.ResolveJoinUrlAsync(joinUrl, cancellationToken);
        var session = await mediaClient.JoinAsync(externalMeetingId, coordinates, cancellationToken);
        meetings[session.MeetingId] = new GraphMeetingRuntime(session.CallId, session.AudioSession);
        return new MediaJoinResult(session.MeetingId, session.AudioSession);
    }

    public async Task<SpeechPlaybackResult> PlayAudioAsync(
        string meetingId,
        string operationId,
        string text,
        byte[] audioBytes,
        bool interrupt,
        CancellationToken cancellationToken)
    {
        if (!meetings.TryGetValue(meetingId, out var runtime))
        {
            throw new KeyNotFoundException($"Unknown meeting: {meetingId}");
        }

        SpeechStatusChanged?.Invoke(
            this,
            new MediaSpeechStatusEvent(meetingId, operationId, "accepted", text));
        if (interrupt)
        {
            await runtime.AudioSession.ClearPendingAudioAsync(cancellationToken);
            SpeechStatusChanged?.Invoke(
                this,
                new MediaSpeechStatusEvent(meetingId, operationId, "interrupted", text));
        }

        var frames = ChunkPcm16kAudio(audioBytes);
        await runtime.AudioSession.EnqueueAudioAsync(operationId, frames, cancellationToken);
        SpeechStatusChanged?.Invoke(
            this,
            new MediaSpeechStatusEvent(meetingId, operationId, "queued", text));
        return new SpeechPlaybackResult("queued", frames.Count);
    }

    public async Task LeaveAsync(string meetingId, CancellationToken cancellationToken)
    {
        if (!meetings.TryRemove(meetingId, out var runtime))
        {
            return;
        }

        await runtime.AudioSession.DisposeAsync();
    }

    public async Task StartRecordingStatusAsync(string meetingId, CancellationToken cancellationToken)
    {
        if (!meetings.TryGetValue(meetingId, out var runtime))
        {
            throw new KeyNotFoundException($"Unknown meeting: {meetingId}");
        }

        await graphClient.SetRecordingStatusAsync(runtime.CallId, isRecording: true, cancellationToken);
    }

    public async Task StopRecordingStatusAsync(string meetingId, CancellationToken cancellationToken)
    {
        if (!meetings.TryGetValue(meetingId, out var runtime))
        {
            return;
        }

        await graphClient.SetRecordingStatusAsync(runtime.CallId, isRecording: false, cancellationToken);
    }

    public void EmitTranscript(string meetingId, string text)
    {
        TranscriptReceived?.Invoke(this, new MediaTranscriptReceivedEvent(meetingId, text));
    }

    public void EmitError(string meetingId, string error)
    {
        ErrorReceived?.Invoke(this, new MediaErrorReceivedEvent(meetingId, error));
    }

    private static IReadOnlyList<byte[]> ChunkPcm16kAudio(byte[] audioBytes)
    {
        if (audioBytes.Length == 0)
        {
            return [];
        }

        var frames = new List<byte[]>((audioBytes.Length + Pcm16k20MsFrameBytes - 1) / Pcm16k20MsFrameBytes);
        for (var offset = 0; offset < audioBytes.Length; offset += Pcm16k20MsFrameBytes)
        {
            var frame = new byte[Pcm16k20MsFrameBytes];
            var count = Math.Min(Pcm16k20MsFrameBytes, audioBytes.Length - offset);
            Buffer.BlockCopy(audioBytes, offset, frame, 0, count);
            frames.Add(frame);
        }

        return frames;
    }

    private sealed record GraphMeetingRuntime(
        string CallId,
        IMeetingAudioSession AudioSession);
}

public sealed class GraphRestMeetingClient : IGraphMeetingClient
{
    private readonly HttpClient httpClient;
    private readonly GraphTeamsMediaOptions options;

    public GraphRestMeetingClient(HttpClient httpClient, GraphTeamsMediaOptions options)
    {
        this.httpClient = httpClient;
        this.options = options;
    }

    public async Task<OnlineMeetingCoordinates> ResolveJoinUrlAsync(
        string joinUrl,
        CancellationToken cancellationToken)
    {
        var token = await AcquireTokenAsync(cancellationToken);
        var escapedJoinUrl = Uri.EscapeDataString(joinUrl);
        using var request = new HttpRequestMessage(
            HttpMethod.Get,
            $"https://graph.microsoft.com/v1.0/users/{Uri.EscapeDataString(options.OnlineMeetingLookupUserId)}/onlineMeetings?$filter=JoinWebUrl%20eq%20'{escapedJoinUrl}'");
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);

        using var response = await httpClient.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        var values = document.RootElement.GetProperty("value");
        if (values.GetArrayLength() == 0)
        {
            throw new InvalidOperationException("Teams join_url was not resolved by Graph onlineMeetings lookup.");
        }

        var meeting = values[0];
        return new OnlineMeetingCoordinates(
            meeting.GetProperty("chatInfo").GetRawText(),
            meeting.GetProperty("meetingInfo").GetRawText(),
            meeting.TryGetProperty("tenantId", out var tenantId) ? tenantId.GetString() : options.TenantId);
    }

    public async Task SetRecordingStatusAsync(
        string callId,
        bool isRecording,
        CancellationToken cancellationToken)
    {
        var token = await AcquireTokenAsync(cancellationToken);
        using var request = new HttpRequestMessage(
            HttpMethod.Post,
            $"https://graph.microsoft.com/v1.0/communications/calls/{Uri.EscapeDataString(callId)}/updateRecordingStatus");
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        request.Content = new StringContent(
            JsonSerializer.Serialize(new
            {
                clientContext = Guid.NewGuid().ToString("N"),
                status = isRecording ? "recording" : "notRecording",
            }),
            Encoding.UTF8,
            "application/json");

        using var response = await httpClient.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    private async Task<string> AcquireTokenAsync(CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(
            HttpMethod.Post,
            $"https://login.microsoftonline.com/{Uri.EscapeDataString(options.TenantId)}/oauth2/v2.0/token");
        request.Content = new FormUrlEncodedContent(new Dictionary<string, string>
        {
            ["client_id"] = options.BotAppId,
            ["client_secret"] = options.BotAppSecret,
            ["scope"] = "https://graph.microsoft.com/.default",
            ["grant_type"] = "client_credentials",
        });
        using var response = await httpClient.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken);
        return document.RootElement.GetProperty("access_token").GetString()
            ?? throw new InvalidOperationException("Graph token response did not include access_token.");
    }
}

public sealed class GraphApplicationHostedMediaClient :
    IApplicationHostedMediaClient,
    IGraphCallNotificationProcessor
{
    private readonly GraphTeamsMediaOptions options;
    private readonly ICommunicationsClient communicationsClient;

    public GraphApplicationHostedMediaClient(GraphTeamsMediaOptions options)
    {
        this.options = options;
        var logger = new GraphLogger(
            "TeamsMeetingMediaBridge",
            Array.Empty<object>(),
            redirectToTrace: true,
            new HashingObfuscationConfiguration(
                Array.Empty<Microsoft.Graph.Communications.Common.Telemetry.HttpLogging.Filters.BaseFilter>(),
                addOdataType: true,
                Array.Empty<ObfuscationMember>()));
        var mediaSettings = CreateMediaPlatformSettings(options);
        communicationsClient = new CommunicationsClientBuilder(
                "AgentSandboxTeamsMeetingBridge",
                options.BotAppId,
                logger,
                Array.Empty<ObfuscationMember>(),
                [typeof(Call).Assembly])
            .SetAuthenticationProvider(new ClientCredentialRequestAuthenticationProvider(options))
            .SetNotificationUrl(AppendPath(options.PublicCallbackBaseUrl, "api/calls"))
            .SetServiceBaseUrl(new Uri("https://graph.microsoft.com/v1.0"))
            .SetMediaPlatformSettings(mediaSettings)
            .Build();
    }

    public async Task<ApplicationHostedMediaSession> JoinAsync(
        string externalMeetingId,
        OnlineMeetingCoordinates coordinates,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var localMediaSession = communicationsClient.CreateMediaSession(
            new AudioSocketSettings
            {
                StreamDirections = StreamDirection.Sendrecv,
                SupportedAudioFormat = AudioFormat.Pcm16K,
            },
            Enumerable.Empty<VideoSocketSettings>(),
            vbssSocketSettings: null!,
            dataSocketSettings: null!,
            mediaSessionId: Guid.NewGuid());
        var scenarioId = Guid.NewGuid();
        var parameters = new JoinMeetingParameters(
            ParseChatInfo(coordinates.ChatInfo),
            ParseMeetingInfo(coordinates.MeetingInfo),
            (IMediaSession)localMediaSession,
            isInteractiveRosterEnabled: null,
            optIntoDeltaRoster: null,
            isParticipantInfoUpdatesEnabled: true)
        {
            TenantId = string.IsNullOrWhiteSpace(coordinates.TenantId)
                ? options.TenantId
                : coordinates.TenantId.Trim(),
            Subject = $"agent-sandbox-{externalMeetingId}",
        };
        var call = await communicationsClient.Calls(maintainState: true)
            .AddAsync(parameters, scenarioId, cancellationToken);
        var callId = call.Resource.Id;
        if (string.IsNullOrWhiteSpace(callId))
        {
            throw new InvalidOperationException("Graph media join did not return a call id.");
        }

        return new ApplicationHostedMediaSession(
            externalMeetingId,
            callId,
            new GraphMeetingAudioSession(call, localMediaSession));
    }

    public Task<HttpResponseMessage> ProcessNotificationAsync(HttpRequestMessage request)
    {
        return communicationsClient.ProcessNotificationAsync(request);
    }

    private static MediaPlatformSettings CreateMediaPlatformSettings(GraphTeamsMediaOptions options)
    {
        var instanceSettings = new MediaPlatformInstanceSettings
        {
            ServiceFqdn = options.ServiceFqdn,
            InstancePublicIPAddress = IPAddress.Parse(options.PublicIp),
            InstancePublicPort = options.PublicPort,
            InstanceInternalPort = options.InternalPort,
            CertificateThumbprint = options.CertificateThumbprint,
        };
        if (options.MediaPortRangeStart.HasValue && options.MediaPortRangeEnd.HasValue)
        {
            instanceSettings.MediaPortRange = new PortRange(
                (uint)options.MediaPortRangeStart.Value,
                (uint)options.MediaPortRangeEnd.Value);
        }

        return new MediaPlatformSettings
        {
            ApplicationId = options.BotAppId,
            MediaPlatformInstanceSettings = instanceSettings,
        };
    }

    private static ChatInfo ParseChatInfo(string json)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        var chatInfo = new ChatInfo
        {
            ThreadId = OptionalString(root, "threadId"),
            MessageId = OptionalString(root, "messageId"),
            ReplyChainMessageId = OptionalString(root, "replyChainMessageId"),
        };
        CopyAdditionalData(root, chatInfo.AdditionalData, "threadId", "messageId", "replyChainMessageId");
        return chatInfo;
    }

    private static MeetingInfo ParseMeetingInfo(string json)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        var odataType = OptionalString(root, "@odata.type");
        MeetingInfo meetingInfo = string.Equals(
            odataType,
            "#microsoft.graph.organizerMeetingInfo",
            StringComparison.OrdinalIgnoreCase)
            ? new OrganizerMeetingInfo
            {
                Organizer = root.TryGetProperty("organizer", out var organizer)
                    ? ParseIdentitySet(organizer)
                    : null,
            }
            : string.Equals(
                odataType,
                "#microsoft.graph.tokenMeetingInfo",
                StringComparison.OrdinalIgnoreCase)
                ? new TokenMeetingInfo { Token = OptionalString(root, "token") }
                : new MeetingInfo();
        meetingInfo.OdataType = odataType;
        CopyAdditionalData(root, meetingInfo.AdditionalData, "@odata.type", "organizer", "token");
        return meetingInfo;
    }

    private static IdentitySet ParseIdentitySet(JsonElement element)
    {
        var identitySet = new IdentitySet
        {
            Application = ParseIdentity(element, "application"),
            Device = ParseIdentity(element, "device"),
            User = ParseIdentity(element, "user"),
        };
        CopyAdditionalData(element, identitySet.AdditionalData, "application", "device", "user");
        return identitySet;
    }

    private static Identity? ParseIdentity(JsonElement element, string propertyName)
    {
        if (!element.TryGetProperty(propertyName, out var child) || child.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        var identity = new Identity
        {
            DisplayName = OptionalString(child, "displayName"),
            Id = OptionalString(child, "id"),
            OdataType = OptionalString(child, "@odata.type"),
        };
        CopyAdditionalData(child, identity.AdditionalData, "displayName", "id", "@odata.type");
        return identity;
    }

    private static void CopyAdditionalData(
        JsonElement element,
        IDictionary<string, object> target,
        params string[] handledProperties)
    {
        var handled = handledProperties.ToHashSet(StringComparer.Ordinal);
        foreach (var property in element.EnumerateObject())
        {
            if (!handled.Contains(property.Name))
            {
                target[property.Name] = JsonSerializer.Deserialize<object>(property.Value.GetRawText())!;
            }
        }
    }

    private static string? OptionalString(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }

    private static Uri AppendPath(Uri baseUri, string path)
    {
        var builder = new UriBuilder(baseUri);
        builder.Path = string.Join(
            "/",
            new[] { builder.Path.TrimEnd('/'), path.TrimStart('/') }
                .Where(value => !string.IsNullOrWhiteSpace(value)));
        return builder.Uri;
    }

    private sealed class ClientCredentialRequestAuthenticationProvider : IRequestAuthenticationProvider
    {
        private static readonly string[] Scopes = ["https://graph.microsoft.com/.default"];

        private readonly GraphTeamsMediaOptions options;
        private readonly IConfidentialClientApplication app;
        private readonly ConfigurationManager<OpenIdConnectConfiguration> configurationManager;
        private readonly JsonWebTokenHandler tokenHandler = new();

        public ClientCredentialRequestAuthenticationProvider(GraphTeamsMediaOptions options)
        {
            this.options = options;
            app = ConfidentialClientApplicationBuilder
                .Create(options.BotAppId)
                .WithClientSecret(options.BotAppSecret)
                .WithAuthority(AzureCloudInstance.AzurePublic, options.TenantId)
                .Build();
            configurationManager = new ConfigurationManager<OpenIdConnectConfiguration>(
                $"https://login.microsoftonline.com/{Uri.EscapeDataString(options.TenantId)}/v2.0/.well-known/openid-configuration",
                new OpenIdConnectConfigurationRetriever());
        }

        public async Task AuthenticateOutboundRequestAsync(HttpRequestMessage request, string tenant)
        {
            _ = tenant;
            var token = await app.AcquireTokenForClient(Scopes).ExecuteAsync();
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.AccessToken);
        }

        public async Task<RequestValidationResult> ValidateInboundRequestAsync(HttpRequestMessage request)
        {
            var authorization = request.Headers.Authorization;
            if (authorization is null
                || !string.Equals(authorization.Scheme, "Bearer", StringComparison.OrdinalIgnoreCase)
                || string.IsNullOrWhiteSpace(authorization.Parameter))
            {
                return InvalidRequest();
            }

            var token = authorization.Parameter;
            var configuration = await configurationManager.GetConfigurationAsync(CancellationToken.None);
            var result = await tokenHandler.ValidateTokenAsync(
                token,
                new TokenValidationParameters
                {
                    RequireSignedTokens = true,
                    ValidateIssuerSigningKey = true,
                    IssuerSigningKeys = configuration.SigningKeys,
                    ValidateIssuer = true,
                    ValidIssuers =
                    [
                        $"https://login.microsoftonline.com/{options.TenantId}/v2.0",
                        $"https://sts.windows.net/{options.TenantId}/",
                    ],
                    ValidateAudience = true,
                    ValidAudiences = [options.BotAppId, $"api://{options.BotAppId}"],
                    ValidateLifetime = true,
                    ClockSkew = TimeSpan.FromMinutes(5),
                });
            if (!result.IsValid)
            {
                configurationManager.RequestRefresh();
                return InvalidRequest();
            }

            var tenantId = tokenHandler.ReadJsonWebToken(token).Claims
                .FirstOrDefault(claim => string.Equals(claim.Type, "tid", StringComparison.Ordinal))
                ?.Value;
            return new RequestValidationResult
            {
                IsValid = true,
                TenantId = string.IsNullOrWhiteSpace(tenantId) ? options.TenantId : tenantId,
            };
        }

        private static RequestValidationResult InvalidRequest()
        {
            return new RequestValidationResult { IsValid = false, TenantId = string.Empty };
        }
    }

    private sealed class GraphMeetingAudioSession : IMeetingAudioSession
    {
        private readonly ICall call;
        private readonly ILocalMediaSession mediaSession;
        private readonly IAudioSocket audioSocket;
        private bool disposed;

        public GraphMeetingAudioSession(ICall call, ILocalMediaSession mediaSession)
        {
            this.call = call;
            this.mediaSession = mediaSession;
            audioSocket = mediaSession.AudioSocket;
            audioSocket.AudioMediaReceived += OnAudioMediaReceived;
        }

        public event EventHandler<byte[]>? AudioReceived;

        public Task EnqueueAudioAsync(
            string operationId,
            IReadOnlyList<byte[]> frames,
            CancellationToken cancellationToken)
        {
            _ = operationId;
            cancellationToken.ThrowIfCancellationRequested();
            foreach (var frame in frames)
            {
                cancellationToken.ThrowIfCancellationRequested();
                SendFrame(frame);
            }

            return Task.CompletedTask;
        }

        public Task ClearPendingAudioAsync(CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            return Task.CompletedTask;
        }

        public async ValueTask DisposeAsync()
        {
            if (disposed)
            {
                return;
            }

            disposed = true;
            audioSocket.AudioMediaReceived -= OnAudioMediaReceived;
            await call.DeleteAsync(handleHttpNotFoundInternally: true, CancellationToken.None);
            call.Dispose();
            if (mediaSession is IDisposable disposableMediaSession)
            {
                disposableMediaSession.Dispose();
            }
        }

        private void OnAudioMediaReceived(object? sender, AudioMediaReceivedEventArgs args)
        {
            try
            {
                var buffer = args.Buffer;
                var length = checked((int)buffer.Length);
                var audio = new byte[length];
                if (length > 0)
                {
                    Marshal.Copy(buffer.Data, audio, 0, length);
                    AudioReceived?.Invoke(this, audio);
                }
            }
            finally
            {
                args.Buffer.Dispose();
            }
        }

        private void SendFrame(byte[] frame)
        {
            if (frame.Length == 0)
            {
                return;
            }

            var pointer = Marshal.AllocHGlobal(frame.Length);
            AudioSendBuffer? buffer = null;
            try
            {
                Marshal.Copy(frame, 0, pointer, frame.Length);
                buffer = new AudioSendBuffer(
                    pointer,
                    frame.Length,
                    AudioFormat.Pcm16K,
                    MediaPlatform.GetCurrentTimestamp());
                pointer = IntPtr.Zero;
                audioSocket.Send(buffer);
            }
            catch
            {
                buffer?.Dispose();
                throw;
            }
            finally
            {
                if (pointer != IntPtr.Zero)
                {
                    Marshal.FreeHGlobal(pointer);
                }
            }
        }
    }
}
