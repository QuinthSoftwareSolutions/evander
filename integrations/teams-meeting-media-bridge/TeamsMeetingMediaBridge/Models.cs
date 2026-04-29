using System.Text.Json.Serialization;

namespace TeamsMeetingMediaBridge;

public sealed record JoinMeetingRequest(
    [property: JsonPropertyName("join_url")] string JoinUrl,
    [property: JsonPropertyName("external_meeting_id")] string ExternalMeetingId,
    [property: JsonPropertyName("default_voice")] string? DefaultVoice,
    [property: JsonPropertyName("metadata")] Dictionary<string, object>? Metadata);

public sealed record JoinMeetingResponse(
    [property: JsonPropertyName("meeting_id")] string MeetingId,
    [property: JsonPropertyName("status")] string Status);

public sealed record SpeakRequest(
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("voice")] string? Voice,
    [property: JsonPropertyName("interrupt")] bool Interrupt);

public sealed record SpeakResponse(
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("meeting_id")] string MeetingId);

public sealed record LeaveResponse(
    [property: JsonPropertyName("meeting_id")] string MeetingId,
    [property: JsonPropertyName("status")] string Status);

public sealed record MeetingEventsResponse(
    [property: JsonPropertyName("events")] IReadOnlyList<MeetingEvent> Events);

public sealed record MeetingEvent(
    [property: JsonPropertyName("sequence")] long Sequence,
    [property: JsonPropertyName("event_type")] string EventType,
    [property: JsonPropertyName("occurred_at")] DateTimeOffset OccurredAt,
    [property: JsonPropertyName("text")] string? Text = null,
    [property: JsonPropertyName("speech_operation_id")] string? SpeechOperationId = null,
    [property: JsonPropertyName("speech_status")] string? SpeechStatus = null,
    [property: JsonPropertyName("error")] string? Error = null,
    [property: JsonPropertyName("metadata")] Dictionary<string, object>? Metadata = null);

public sealed record SynthesizedSpeech(
    string OperationId,
    string Voice,
    byte[] AudioBytes);

public sealed record MediaJoinResult(
    string MeetingId,
    IMeetingAudioSession? AudioSession = null);

public sealed record SpeechPlaybackResult(
    string Status,
    int FramesQueued);

public sealed record MediaTranscriptReceivedEvent(
    string MeetingId,
    string Text);

public sealed record MediaErrorReceivedEvent(
    string MeetingId,
    string Error);

public sealed record MediaSpeechStatusEvent(
    string MeetingId,
    string OperationId,
    string Status,
    string? Text = null);

public sealed record OnlineMeetingCoordinates(
    string ChatInfo,
    string MeetingInfo,
    string? TenantId);

public sealed record MeetingState(
    string MeetingId,
    string JoinUrl,
    string? DefaultVoice,
    string Status,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt);
