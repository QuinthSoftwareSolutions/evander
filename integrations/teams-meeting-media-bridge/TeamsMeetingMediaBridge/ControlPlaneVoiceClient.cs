using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TeamsMeetingMediaBridge;

public sealed record VisualCapability(
    [property: JsonPropertyName("enabled")] bool Enabled,
    [property: JsonPropertyName("reason")] string? Reason);

public sealed record TeamsCallAdmission(
    [property: JsonPropertyName("voice_session_id")] string VoiceSessionId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("projection")] Dictionary<string, object> Projection,
    [property: JsonPropertyName("realtime")] Dictionary<string, object> Realtime,
    [property: JsonPropertyName("visual_context")] VisualCapability? VisualContext = null);

public interface IControlPlaneVoiceClient
{
    Task<TeamsCallAdmission> AdmitAsync(string tenantId, string callerId, string callId, CancellationToken token);
    Task<string> QueryAsync(string voiceSessionId, string question, string reason, string requestId, CancellationToken token);
    Task<string> QueryWithVisualsAsync(string voiceSessionId, string question, string reason, string requestId,
        VisualTurnContext context, CancellationToken token) => QueryAsync(voiceSessionId, question, reason, requestId, token);
    Task CompleteAsync(string voiceSessionId, NativeCallSnapshot snapshot, CancellationToken token);
}

public sealed class ControlPlaneVoiceClient : IControlPlaneVoiceClient
{
    private readonly HttpClient client;

    public ControlPlaneVoiceClient(HttpClient client, Uri baseUrl, string token)
    {
        if (string.IsNullOrWhiteSpace(token) || baseUrl.Scheme is not ("http" or "https"))
            throw new ArgumentException("A control plane HTTP URL and voice controller token are required.");
        this.client = client;
        client.BaseAddress = new Uri(baseUrl.AbsoluteUri.TrimEnd('/') + "/");
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        client.Timeout = TimeSpan.FromSeconds(5);
    }

    public async Task<TeamsCallAdmission> AdmitAsync(string tenantId, string callerId, string callId, CancellationToken token)
    {
        using var response = await HttpClientJsonExtensions.PostAsJsonAsync(client, "voice-sessions/inbound-teams-calls",
            new { tenant_id = tenantId, caller_user_id = callerId, graph_call_id = callId }, token);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<TeamsCallAdmission>(token)
            ?? throw new InvalidOperationException("Empty Teams call admission response.");
    }

    public async Task<string> QueryAsync(string voiceSessionId, string question, string reason, string requestId, CancellationToken token)
        => await QueryWithVisualsAsync(voiceSessionId, question, reason, requestId,
            VisualTurnContext.Unavailable("No visual context attached"), token);

    public async Task<string> QueryWithVisualsAsync(string voiceSessionId, string question, string reason, string requestId,
        VisualTurnContext context, CancellationToken token)
    {
        var deadline = DateTimeOffset.UtcNow.AddSeconds(90);
        var path = $"voice-sessions/{Uri.EscapeDataString(voiceSessionId)}/queries";
        JsonElement created = default;
        // Retries carry the same function-call ID so an uncertain POST cannot duplicate work.
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                using var body = CreateQueryContent(question, reason, requestId, context);
                using var response = await client.PostAsync(path, body, token);
                response.EnsureSuccessStatusCode();
                created = await response.Content.ReadFromJsonAsync<JsonElement>(token);
                break;
            }
            catch (Exception ex) when (attempt < 2 && !token.IsCancellationRequested && IsTransient(ex))
            {
                await Task.Delay(TimeSpan.FromMilliseconds(200), token);
            }
        }
        var queryId = created.GetProperty("query_id").GetString()!;
        // The durable query owns its snapshots now; polling retains no image buffers.
        context = VisualTurnContext.Unavailable("Query snapshots were handed to the original assistant");
        var queryPath = $"{path}/{Uri.EscapeDataString(queryId)}";
        if (created.TryGetProperty("deadline_at", out var storedDeadline)
            && storedDeadline.ValueKind == JsonValueKind.String
            && storedDeadline.TryGetDateTimeOffset(out var acceptedDeadline) && acceptedDeadline < deadline)
            deadline = acceptedDeadline;
        if (created.GetProperty("status").GetString() != "pending") return created.GetRawText();
        while (DateTimeOffset.UtcNow < deadline)
        {
            token.ThrowIfCancellationRequested();
            if (created.GetProperty("status").GetString() != "pending")
                return created.GetRawText();
            var remaining = deadline - DateTimeOffset.UtcNow;
            if (remaining <= TimeSpan.Zero) break;
            await Task.Delay(remaining < TimeSpan.FromSeconds(1) ? remaining : TimeSpan.FromSeconds(1), token);
            remaining = deadline - DateTimeOffset.UtcNow;
            if (remaining <= TimeSpan.Zero) break;
            using var pollTimeout = CancellationTokenSource.CreateLinkedTokenSource(token);
            pollTimeout.CancelAfter(remaining);
            try
            {
                created = await client.GetFromJsonAsync<JsonElement>(queryPath, pollTimeout.Token);
            }
            catch (Exception ex) when (!token.IsCancellationRequested && IsTransient(ex))
            {
                // A transient failure must not be presented as a confirmed assistant result.
            }
        }
        using var timeout = await HttpClientJsonExtensions.PostAsJsonAsync(client, queryPath + "/timeout", new { }, token);
        timeout.EnsureSuccessStatusCode();
        var final = await timeout.Content.ReadFromJsonAsync<JsonElement>(token);
        // An answer racing the timeout remains a confirmed result.
        if (final.TryGetProperty("status", out var finalStatus) && finalStatus.GetString() != "timeout")
            return final.GetRawText();
        return "{\"status\":\"timeout\",\"message\":\"No result confirmed. Accepted work may continue in Teams.\"}";
    }

    private static HttpContent CreateQueryContent(string question, string reason, string requestId, VisualTurnContext context)
    {
        if (context.Images.Count == 0)
            return JsonContent.Create(new { question, request_id = requestId, deadline_seconds = 90,
                metadata = new { reason, visual_context_unavailable = context.Availability } });
        if (context.Images.Count > 2 || context.Images.Any(image => image.Jpeg.Length > VisualFrameSampler.MaxImageBytes))
            throw new ArgumentException("Visual query exceeds its image limits.");
        var multipart = new MultipartFormDataContent();
        multipart.Add(JsonContent.Create(new { question, request_id = requestId, deadline_seconds = 90,
            metadata = new { reason }, images = context.Images.Select(image => new {
                source = image.Source, captured_at = image.CapturedAt, width = image.Width, height = image.Height,
            }) }), "payload");
        foreach (var image in context.Images)
        {
            var content = new ByteArrayContent(image.Jpeg);
            content.Headers.ContentType = new MediaTypeHeaderValue("image/jpeg");
            multipart.Add(content, image.Source, $"{image.Source}.jpg");
        }
        return multipart;
    }

    private static bool IsTransient(Exception error) => error switch
    {
        TaskCanceledException => true,
        HttpRequestException request => request.StatusCode is null
            || request.StatusCode is System.Net.HttpStatusCode.RequestTimeout or System.Net.HttpStatusCode.TooManyRequests
            || (int)request.StatusCode >= 500,
        _ => false,
    };

    public async Task CompleteAsync(string voiceSessionId, NativeCallSnapshot snapshot, CancellationToken token)
    {
        using var response = await HttpClientJsonExtensions.PostAsJsonAsync(client,
            $"voice-sessions/{Uri.EscapeDataString(voiceSessionId)}/bridge-result", snapshot, token);
        response.EnsureSuccessStatusCode();
    }
}
