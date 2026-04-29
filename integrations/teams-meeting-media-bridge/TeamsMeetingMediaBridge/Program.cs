using TeamsMeetingMediaBridge;

var builder = WebApplication.CreateBuilder(args);
var bridgeOptions = BridgeOptions.FromConfiguration(builder.Configuration);
builder.Services.AddSingleton(bridgeOptions);

if (bridgeOptions.AdapterMode == BridgeAdapterMode.Graph)
{
    builder.Services.AddHttpClient<GraphRestMeetingClient>();
    builder.Services.AddSingleton(bridgeOptions.Graph!);
    builder.Services.AddSingleton(bridgeOptions.AzureSpeech!);
    builder.Services.AddSingleton<IGraphMeetingClient>(sp => sp.GetRequiredService<GraphRestMeetingClient>());
    builder.Services.AddSingleton<GraphApplicationHostedMediaClient>();
    builder.Services.AddSingleton<IApplicationHostedMediaClient>(sp =>
        sp.GetRequiredService<GraphApplicationHostedMediaClient>());
    builder.Services.AddSingleton<IGraphCallNotificationProcessor>(sp =>
        sp.GetRequiredService<GraphApplicationHostedMediaClient>());
    builder.Services.AddSingleton<IAzureSpeechAdapter>(sp =>
        new AzureSpeechAdapter(sp.GetRequiredService<AzureSpeechOptions>()));
    builder.Services.AddSingleton<ITeamsMediaAdapter>(sp =>
        new GraphTeamsMediaAdapter(
            sp.GetRequiredService<IGraphMeetingClient>(),
            sp.GetRequiredService<IApplicationHostedMediaClient>()));
}
else
{
    builder.Services.AddSingleton<ITeamsMediaAdapter, InMemoryTeamsMediaAdapter>();
    builder.Services.AddSingleton<IAzureSpeechAdapter, DeterministicSpeechAdapter>();
}

builder.Services.AddSingleton(sp =>
{
    return new MeetingCoordinator(
        sp.GetRequiredService<ITeamsMediaAdapter>(),
        sp.GetRequiredService<IAzureSpeechAdapter>(),
        sp.GetRequiredService<BridgeOptions>().DefaultVoice);
});

var app = builder.Build();
var bearerToken = Environment.GetEnvironmentVariable("TEAMS_BRIDGE_TOKEN")
    ?? Environment.GetEnvironmentVariable("TEAMS_MEETING_BRIDGE_TOKEN");
if (!string.IsNullOrWhiteSpace(bearerToken))
{
    app.Use(async (context, next) =>
    {
        if (bridgeOptions.AdapterMode == BridgeAdapterMode.Graph
            && context.Request.Path.StartsWithSegments("/api/calls"))
        {
            await next();
            return;
        }

        var header = context.Request.Headers.Authorization.ToString();
        if (!string.Equals(header, $"Bearer {bearerToken}", StringComparison.Ordinal))
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            await context.Response.WriteAsJsonAsync(new { error = "unauthorized" });
            return;
        }

        await next();
    });
}

app.MapGet("/healthz", () => Results.Json(new { status = "ok" }));

if (bridgeOptions.AdapterMode == BridgeAdapterMode.Graph)
{
    app.MapPost("/api/calls", async (
        HttpContext context,
        IGraphCallNotificationProcessor notificationProcessor,
        CancellationToken cancellationToken) =>
    {
        using var request = await ToHttpRequestMessageAsync(context.Request, cancellationToken);
        using var response = await notificationProcessor.ProcessNotificationAsync(request);
        await CopyHttpResponseMessageAsync(response, context.Response, cancellationToken);
    });
}

app.MapPost("/meetings/join", async (
    JoinMeetingRequest request,
    MeetingCoordinator coordinator,
    CancellationToken cancellationToken) =>
{
    var response = await coordinator.JoinAsync(request, cancellationToken);
    return Results.Json(response);
});

app.MapGet("/meetings/{meetingId}/events", (
    string meetingId,
    long? after,
    MeetingCoordinator coordinator) =>
{
    var response = coordinator.GetEvents(meetingId, after ?? 0);
    return Results.Json(response);
});

app.MapPost("/meetings/{meetingId}/speak", async (
    string meetingId,
    SpeakRequest request,
    MeetingCoordinator coordinator,
    CancellationToken cancellationToken) =>
{
    var response = await coordinator.SpeakAsync(meetingId, request, cancellationToken);
    return Results.Json(response);
});

app.MapPost("/meetings/{meetingId}/leave", async (
    string meetingId,
    MeetingCoordinator coordinator,
    CancellationToken cancellationToken) =>
{
    var response = await coordinator.LeaveAsync(meetingId, cancellationToken);
    return Results.Json(response);
});

app.Run();

static async Task<HttpRequestMessage> ToHttpRequestMessageAsync(
    HttpRequest request,
    CancellationToken cancellationToken)
{
    var requestMessage = new HttpRequestMessage(
        new HttpMethod(request.Method),
        new Uri($"{request.Scheme}://{request.Host}{request.PathBase}{request.Path}{request.QueryString}"));
    var content = new MemoryStream();
    await request.Body.CopyToAsync(content, cancellationToken);
    content.Position = 0;
    requestMessage.Content = new StreamContent(content);
    foreach (var header in request.Headers)
    {
        if (!requestMessage.Headers.TryAddWithoutValidation(header.Key, header.Value.ToArray()))
        {
            requestMessage.Content.Headers.TryAddWithoutValidation(header.Key, header.Value.ToArray());
        }
    }

    return requestMessage;
}

static async Task CopyHttpResponseMessageAsync(
    HttpResponseMessage source,
    HttpResponse target,
    CancellationToken cancellationToken)
{
    target.StatusCode = (int)source.StatusCode;
    foreach (var header in source.Headers)
    {
        target.Headers[header.Key] = header.Value.ToArray();
    }

    if (source.Content is not null)
    {
        foreach (var header in source.Content.Headers)
        {
            target.Headers[header.Key] = header.Value.ToArray();
        }

        await source.Content.CopyToAsync(target.Body, cancellationToken);
    }

    target.Headers.Remove("transfer-encoding");
}
