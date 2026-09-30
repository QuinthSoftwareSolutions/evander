// Timeline requests, facets, pagination, and event presentation.
import { emptyTimelineState } from "./team-ui-state.js";
import { API_BASE, timelinePageSize, timelineTypeOrder } from "./team-ui-config.js";
import {
  escapeAttr,
  escapeHtml,
  humanizeToken,
  optionsFromMap,
  readablePreview,
  responseText,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamTimelineController(peers) {

  let timelineState = emptyTimelineState();

  let timelineLoading = false;

  let timelineRequestId = 0;

  let timelinePage = 1;

  function bindTimelineFilters() {
    document.querySelectorAll("[data-timeline-filter]").forEach((filter) => {
      filter.addEventListener("change", () => {
        syncTimelineFilterControls(filter.dataset.timelineFilter, filter.value);
        timelinePage = 1;
        void loadTimeline();
      });
    });
  }

  function bindTimelinePagination() {
    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-timeline-page-action]");
      if (!button) {
        return;
      }
      const action = button.dataset.timelinePageAction;
      if (action === "next") {
        timelinePage += 1;
      } else if (action === "previous") {
        timelinePage = Math.max(1, timelinePage - 1);
      } else {
        return;
      }
      void loadTimeline();
    });
  }

  function syncTimelineFilterControls(filterKey, value) {
    if (!filterKey) {
      return;
    }
    document.querySelectorAll(`[data-timeline-filter="${filterKey}"]`).forEach((filter) => {
      if (filter.value !== value) {
        filter.value = value;
      }
    });
  }

  async function loadTimeline() {
    if (!document.querySelector("[data-timeline-view]")) {
      return;
    }
    const requestId = ++timelineRequestId;
    const search = timelineSearchParams();

    timelineLoading = true;
    peers.app.setTimelineStatus("Loading activity timeline.", "loading");

    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/timeline${search.toString() ? `?${search.toString()}` : ""}`,
        {
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      if (requestId !== timelineRequestId) {
        return;
      }
      timelineState = normalizeTimeline(payload);
      if (
        peers.app.activeTeamView === "timeline" &&
        Number(timelineState.total_count || 0) > 0 &&
        Number(timelineState.offset || 0) >= Number(timelineState.total_count || 0)
      ) {
        timelinePage = Math.max(1, Math.ceil(Number(timelineState.total_count || 0) / timelinePageSize));
        void loadTimeline();
        return;
      }
      timelineLoading = false;
      peers.selects.refreshSelects();
      renderTimeline();
      peers.app.setTimelineStatus(describeTimelineStatus(timelineState), "ok");
    } catch (error) {
      if (requestId !== timelineRequestId) {
        return;
      }
      timelineLoading = false;
      peers.app.setTimelineStatus(error instanceof Error ? error.message : "Timeline failed to load.", "error");
      renderTimeline();
    }
  }

  function timelineSearchParams() {
    const filters = readTimelineFilters();
    const search = new URLSearchParams();
    if (filters.team_id) {
      search.set("team_id", filters.team_id);
    }
    if (filters.project_id) {
      search.set("project_id", filters.project_id);
    }
    if (filters.agent_id) {
      search.set("agent_id", filters.agent_id);
    }
    if (filters.type) {
      search.set("type", filters.type);
    }
    if (filters.status) {
      search.set("status", filters.status);
    }
    if (filters.attention_needed !== null && filters.attention_needed !== undefined) {
      search.set("attention_needed", filters.attention_needed ? "true" : "false");
    }
    search.set("limit", String(timelinePageSize));
    search.set("offset", String(Math.max(0, (Math.max(1, timelinePage) - 1) * timelinePageSize)));
    return search;
  }

  function normalizeTimeline(payload) {
    const entries = Array.isArray(payload?.entries)
      ? payload.entries.slice().sort(compareTimelineEntriesNewestFirst)
      : [];
    return {
      ...emptyTimelineState(),
      ...(payload || {}),
      entries,
    };
  }

  function compareTimelineEntriesNewestFirst(left, right) {
    const leftTime = timelineEntrySortTime(left);
    const rightTime = timelineEntrySortTime(right);
    if (leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    return (
      String(left.category || "").localeCompare(String(right.category || "")) ||
      String(left.type || "").localeCompare(String(right.type || "")) ||
      String(left.id || "").localeCompare(String(right.id || ""))
    );
  }

  function timelineEntrySortTime(entry) {
    const timestamp = entry?.timestamp;
    if (!timestamp) {
      return Number.NEGATIVE_INFINITY;
    }
    const parsed = Date.parse(normalizeTimelineTimestampForSort(timestamp));
    return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
  }

  function normalizeTimelineTimestampForSort(timestamp) {
    const text = String(timestamp || "").trim();
    if (!text) {
      return text;
    }
    // The API treats legacy offset-naive ISO datetimes as UTC when sorting.
    if (isOffsetNaiveIsoDateTime(text)) {
      return `${text}Z`;
    }
    return text;
  }

  function isOffsetNaiveIsoDateTime(timestamp) {
    return (
      /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(timestamp) &&
      !/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(timestamp)
    );
  }

  function renderTimeline() {
    if (!document.querySelector("[data-timeline-view]")) {
      return;
    }

    const entries = (timelineState.entries || []).slice().sort(compareTimelineEntriesNewestFirst);
    const totalCount = Number.isFinite(Number(timelineState.total_count))
      ? Number(timelineState.total_count)
      : entries.length;
    const limit = Number.isFinite(Number(timelineState.limit)) && Number(timelineState.limit) > 0
      ? Number(timelineState.limit)
      : timelinePageSize;
    let pageWasClamped = false;
    if (peers.app.activeTeamView === "timeline") {
      const pageCount = Math.max(1, Math.ceil(totalCount / limit));
      const nextPage = Math.min(Math.max(1, timelinePage), pageCount);
      pageWasClamped = nextPage !== timelinePage;
      timelinePage = nextPage;
      refetchTimelineAfterPageClamp({ pageWasClamped, totalCount, entries });
    }
    const summaryParts = [`${totalCount} ${totalCount === 1 ? "entry" : "entries"}`];
    if (timelineState.timestampless_count) {
      summaryParts.push(`${timelineState.timestampless_count} timestamp unavailable`);
    }
    if (peers.app.activeTeamView === "timeline" && totalCount > limit) {
      summaryParts.push(`page ${timelinePage} of ${Math.ceil(totalCount / limit)}`);
    }
    document.querySelectorAll("[data-timeline-summary]").forEach((summary) => {
      summary.textContent = peers.app.targetIsActive(summary) ? summaryParts.join(" · ") : "";
    });

    if (!entries.length) {
      peers.app.renderIntoActiveTargets(
        "[data-timeline-view]",
        timelineLoading
          ? `<p class="timeline-empty">Loading timeline entries.</p>`
          : `<p class="timeline-empty">No timeline entries match these filters.</p>`,
      );
      return;
    }

    if (peers.app.activeTeamView === "timeline") {
      renderFocusedTimeline(entries);
      return;
    }

    peers.app.renderIntoActiveTargets("[data-timeline-view]", `
      <ul class="timeline-list">
        ${entries.map((entry) => renderTimelineEntry(entry)).join("")}
      </ul>
    `);
  }

  function refetchTimelineAfterPageClamp({ pageWasClamped, totalCount, entries }) {
    if (!pageWasClamped || timelineLoading || totalCount <= 0 || entries.length) {
      return;
    }
    timelineLoading = true;
    peers.app.setTimelineStatus("Loading activity timeline.", "loading");
    void loadTimeline();
  }

  function renderFocusedTimeline(entries) {
    const totalCount = Number.isFinite(Number(timelineState.total_count))
      ? Number(timelineState.total_count)
      : entries.length;
    const limit = Number.isFinite(Number(timelineState.limit)) && Number(timelineState.limit) > 0
      ? Number(timelineState.limit)
      : timelinePageSize;
    const offset = Number.isFinite(Number(timelineState.offset))
      ? Number(timelineState.offset)
      : (Math.max(1, timelinePage) - 1) * limit;
    const pageCount = Math.max(1, Math.ceil(totalCount / limit));
    timelinePage = Math.min(Math.max(1, timelinePage), pageCount);
    const pageEntries = entries;
    const pagination = totalCount > limit
      ? renderTimelinePagination(pageCount, offset, pageEntries.length, totalCount)
      : "";

    peers.app.renderIntoActiveTargets("[data-timeline-view]", `
      ${pagination}
      <ul class="timeline-list timeline-list-compact">
        ${pageEntries.map((entry) => renderCompactTimelineEntry(entry)).join("")}
      </ul>
    `);
  }

  function renderTimelinePagination(pageCount, start, pageLength, total) {
    const first = start + 1;
    const last = start + pageLength;
    return `
      <div class="timeline-pagination" aria-label="Activity timeline pages">
        <button
          type="button"
          data-timeline-page-action="previous"
          ${timelinePage <= 1 ? "disabled" : ""}
        >Previous</button>
        <span>${first}-${last} of ${total}</span>
        <button
          type="button"
          data-timeline-page-action="next"
          ${timelinePage >= pageCount ? "disabled" : ""}
        >Next</button>
      </div>
    `;
  }

  function renderTimelineEntry(entry) {
    const timestamp = entry.timestamp ? peers.app.formatTimelineTimestamp(entry.timestamp) : "Timestamp unavailable";
    const headline = timelineEntryHeadline(entry);
    return `
      <li class="timeline-entry">
        <article>
          <div class="timeline-entry-top">
            <div>
              <div class="timeline-timestamp">${escapeHtml(timestamp)}</div>
              <div class="timeline-pills">
                <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.type))}</span>
                <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.category))}</span>
              </div>
            </div>
            <span class="state-pill ${escapeAttr(entry.status || "pending")}">${escapeHtml(humanizeToken(entry.status || "pending"))}</span>
          </div>
          <p class="timeline-summary-text">${escapeHtml(headline)}</p>
          ${renderTimelineEntryDetails(entry)}
        </article>
      </li>
    `;
  }

  function renderCompactTimelineEntry(entry) {
    const timestamp = entry.timestamp ? peers.app.formatTimelineTimestamp(entry.timestamp) : "Timestamp unavailable";
    const headline = timelineEntryHeadline(entry);
    return `
      <li class="timeline-entry timeline-entry-compact">
        <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("timeline", entry.id, "detail"))}>
          <summary class="timeline-entry-summary">
            <span class="timeline-summary-main">
              <span class="timeline-timestamp">${escapeHtml(timestamp)}</span>
              <span class="timeline-summary-text">${escapeHtml(headline)}</span>
            </span>
            <span class="timeline-summary-pills">
              <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.type))}</span>
              <span class="state-pill ${escapeAttr(entry.status || "pending")}">${escapeHtml(humanizeToken(entry.status || "pending"))}</span>
            </span>
          </summary>
          <div class="timeline-entry-details">
            <div class="timeline-pills">
              <span class="state-pill timeline-pill">${escapeHtml(humanizeToken(entry.category))}</span>
              ${entry.attention_needed ? `<span class="state-pill blocked">attention needed</span>` : ""}
            </div>
            ${renderTimelineEntryDetails(entry)}
          </div>
        </details>
      </li>
    `;
  }

  function timelineEntryRecord(entry) {
    if (entry.task_id && peers.app.state.tasks?.[entry.task_id]) {
      return peers.app.state.tasks[entry.task_id];
    }
    if (entry.deliverable_id && peers.app.state.deliverables?.[entry.deliverable_id]) {
      return peers.app.state.deliverables[entry.deliverable_id];
    }
    if (entry.review_request_id && peers.app.state.review_requests?.[entry.review_request_id]) {
      return peers.app.state.review_requests[entry.review_request_id];
    }
    if (entry.inbox_item_id && peers.app.state.inbox?.[entry.inbox_item_id]) {
      return peers.app.state.inbox[entry.inbox_item_id];
    }
    if (entry.action_record_id && peers.app.state.action_records?.[entry.action_record_id]) {
      return peers.app.state.action_records[entry.action_record_id];
    }
    if (
      entry.ephemeral_assignment_id &&
      peers.app.state.ephemeral_assignments?.[entry.ephemeral_assignment_id]
    ) {
      return peers.app.state.ephemeral_assignments[entry.ephemeral_assignment_id];
    }
    if (entry.git_promotion_id && peers.app.state.git_promotions?.[entry.git_promotion_id]) {
      return peers.app.state.git_promotions[entry.git_promotion_id];
    }
    if (
      entry.release_package_id &&
      peers.app.state.release_packages?.[entry.release_package_id]
    ) {
      return peers.app.state.release_packages[entry.release_package_id];
    }
    return null;
  }

  function timelineEntrySubject(entry) {
    if (text(entry.subject)) {
      return text(entry.subject);
    }
    const task = entry.task_id ? peers.app.state.tasks?.[entry.task_id] : null;
    const deliverable = entry.deliverable_id
      ? peers.app.state.deliverables?.[entry.deliverable_id]
      : null;
    const review = entry.review_request_id
      ? peers.app.state.review_requests?.[entry.review_request_id]
      : null;
    const inbox = entry.inbox_item_id ? peers.app.state.inbox?.[entry.inbox_item_id] : null;
    const action = entry.action_record_id
      ? peers.app.state.action_records?.[entry.action_record_id]
      : null;
    const assignment = entry.ephemeral_assignment_id
      ? peers.app.state.ephemeral_assignments?.[entry.ephemeral_assignment_id]
      : null;
    const promotion = entry.git_promotion_id
      ? peers.app.state.git_promotions?.[entry.git_promotion_id]
      : null;
    const releaseId =
      entry.release_package_id || review?.release_package_id || promotion?.release_package_id;
    const release = releaseId ? peers.app.state.release_packages?.[releaseId] : null;
    if (entry.type === "task" && task) {
      return task.title;
    }
    if (entry.type === "deliverable" && deliverable) {
      return peers.reviews.deliverableDisplaySubject(deliverable);
    }
    if (entry.type === "review") {
      if (release) {
        return peers.releases.releaseDisplayLabel(release);
      }
      if (deliverable) {
        return peers.reviews.deliverableDisplaySubject(deliverable);
      }
      return task?.title || "Review";
    }
    if (entry.type === "inbox_item" && inbox) {
      return readablePreview(
        peers.agents.inboxItemDetail(inbox) ||
          `Message for ${peers.agents.agentLabel(peers.app.state.agents?.[inbox.recipient_agent_id]) || "an agent"}`,
        120,
      );
    }
    if (entry.type === "action" && action) {
      return (
        text(action.action?.summary) ||
        humanizeToken(action.action?.type || "Agent action")
      );
    }
    if (entry.type === "ephemeral_assignment") {
      const assignmentTask = assignment?.task_id
        ? peers.app.state.tasks?.[assignment.task_id]
        : task;
      return assignmentTask?.title || "Temporary assignment";
    }
    if (entry.type === "git_promotion") {
      return (
        task?.title ||
        (deliverable ? peers.reviews.deliverableDisplaySubject(deliverable) : "") ||
        peers.app.state.projects?.[promotion?.project_id]?.name ||
        "Git publication"
      );
    }
    if (entry.type === "release_package" && release) {
      return peers.releases.releaseDisplayLabel(release);
    }
    return humanizeToken(entry.type || "Activity");
  }

  function timelineEntryHeadline(entry) {
    if (text(entry.event)) {
      return text(entry.event);
    }
    if (text(entry.subject) && text(entry.summary)) {
      return text(entry.summary);
    }
    const subject = timelineEntrySubject(entry);
    const status = humanizeToken(entry.status || "");
    const eventLabels = {
      "task:created": "Created task",
      "task:updated": "Updated task",
      "inbox_item:created": "Sent message",
      "inbox_item:updated": "Updated message",
      "inbox_item:handled": "Marked message handled",
      "deliverable:created": "Submitted deliverable",
      "review:requested": "Requested review",
      "review:decision": status ? `Review decided ${status.toLowerCase()}` : "Recorded review decision",
      "action:created": status ? `Agent action ${status.toLowerCase()}` : "Recorded agent action",
      "ephemeral_assignment:requested": "Requested temporary assignment",
      "ephemeral_assignment:state": status ? `Temporary assignment changed to ${status.toLowerCase()}` : "Updated temporary assignment",
      "git_promotion:requested": "Requested Git publication",
      "git_promotion:result": status ? `Git publication ${status.toLowerCase()}` : "Recorded Git publication result",
      "release_package:created": "Prepared release",
      "release_package:published": "Published release",
      "release_package:blocked": "Blocked release",
    };
    const key = `${text(entry.type)}:${text(entry.category)}`;
    return `${eventLabels[key] || `${humanizeToken(entry.category)} ${humanizeToken(entry.type)}`} — ${subject}`;
  }

  function timelineActorLabel(entry) {
    const actor = entry.actor_agent_id
      ? peers.app.state.agents?.[entry.actor_agent_id]
      : null;
    if (actor) {
      return `${peers.agents.agentLabel(actor)} · ${peers.agents.agentRoleLabel(actor)}`;
    }
    const process = text(entry.process);
    if (process) {
      return `${humanizeToken(process)} process`;
    }
    const actorSubject = text(entry.actor_subject);
    if (actorSubject && actorSubject !== text(entry.actor_agent_id)) {
      return actorSubject;
    }
    return "Initiator not retained";
  }

  function timelineScopeLabel(entry) {
    const team = entry.team_id ? peers.app.state.teams?.[entry.team_id] : null;
    const project = entry.project_id ? peers.app.state.projects?.[entry.project_id] : null;
    return [
      team?.name || "",
      project?.name || "",
    ]
      .filter(Boolean)
      .join(" · ") || "Scope not retained";
  }

  function timelineCurrentValue(entry) {
    const record = timelineEntryRecord(entry);
    if (entry.after && typeof entry.after !== "object") {
      return text(entry.after);
    }
    if (entry.after && typeof entry.after === "object") {
      return Object.entries(entry.after)
        .map(([key, value]) => `${humanizeToken(key)}: ${humanizeToken(value)}`)
        .join(" · ");
    }
    if (record?.state) {
      return humanizeToken(record.state);
    }
    if (record?.status) {
      return humanizeToken(record.status);
    }
    if (typeof record?.success === "boolean") {
      return record.success ? "Succeeded" : "Failed";
    }
    return humanizeToken(entry.status || "Recorded");
  }

  function timelineChangeDescription(entry) {
    if (entry.change && typeof entry.change === "object") {
      const beforeKnown = entry.change.before_known === true;
      return {
        before:
          beforeKnown && text(entry.change.before)
            ? text(entry.change.before)
            : "Earlier value not retained",
        after: text(entry.change.after) || timelineCurrentValue(entry),
        field: text(entry.change.field) || "State",
      };
    }
    if (text(entry.change)) {
      return {
        label: "Change",
        value: text(entry.change),
      };
    }
    const current = timelineCurrentValue(entry);
    if (entry.category === "handled") {
      return { before: "Pending", after: "Handled" };
    }
    if (entry.type === "git_promotion" && entry.category === "result") {
      return { before: "Pending", after: current };
    }
    if (["created", "requested"].includes(text(entry.category))) {
      return {
        label: "Operation",
        value: `${humanizeToken(entry.category)}; no earlier record is implied.`,
      };
    }
    if (
      text(entry.category) === "updated" &&
      !entry.change &&
      !text(entry.before)
    ) {
      return {
        label: "Operation",
        value: "Snapshot updated; no state transition is implied.",
      };
    }
    return {
      before: text(entry.before) || "Earlier value not retained",
      after: current,
    };
  }

  function timelineReason(entry) {
    if (text(entry.reason)) {
      return text(entry.reason);
    }
    const task = entry.task_id ? peers.app.state.tasks?.[entry.task_id] : null;
    const review = entry.review_request_id
      ? peers.app.state.review_requests?.[entry.review_request_id]
      : null;
    const action = entry.action_record_id
      ? peers.app.state.action_records?.[entry.action_record_id]
      : null;
    const assignment = entry.ephemeral_assignment_id
      ? peers.app.state.ephemeral_assignments?.[entry.ephemeral_assignment_id]
      : null;
    const promotion = entry.git_promotion_id
      ? peers.app.state.git_promotions?.[entry.git_promotion_id]
      : null;
    const release = entry.release_package_id
      ? peers.app.state.release_packages?.[entry.release_package_id]
      : null;
    const deliverable = entry.deliverable_id
      ? peers.app.state.deliverables?.[entry.deliverable_id]
      : null;
    return text(
      task?.cancellation?.reason ||
        task?.waiting_reason ||
        review?.finding ||
        review?.block_kind ||
        action?.error ||
        action?.dismiss_reason ||
        assignment?.error ||
        promotion?.error ||
        release?.error ||
        release?.block_reason ||
        deliverable?.supersession_reason,
    );
  }

  function timelineRelatedLabels(entry) {
    const labels = [];
    const actorId = text(entry.actor_agent_id);
    uniqueTextList(entry.related_agent_ids || [])
      .filter((agentId) => text(agentId) !== actorId)
      .forEach((agentId) => {
        const agent = peers.app.state.agents?.[agentId];
        labels.push(agent ? `${peers.agents.agentLabel(agent)} · ${peers.agents.agentRoleLabel(agent)}` : "Unavailable agent");
      });
    const task = entry.task_id ? peers.app.state.tasks?.[entry.task_id] : null;
    const subject = timelineEntrySubject(entry);
    if (task?.title && task.title !== subject) {
      labels.push(`Task: ${task.title}`);
    }
    return uniqueTextList(labels);
  }

  function renderTimelineTechnicalDetails(entry) {
    const relatedAgentIds = uniqueTextList(entry.related_agent_ids || []).filter(
      (agentId) => text(agentId) !== text(entry.actor_agent_id),
    );
    return peers.reviews.renderTechnicalDetails([
      ["Team ID", entry.team_id],
      ["Project ID", entry.project_id],
      ["Actor agent ID", entry.actor_agent_id],
      ["Related agent IDs", relatedAgentIds],
      ["task_id", "Task"],
      ["deliverable_id", "Deliverable"],
      ["review_request_id", "Review"],
      ["inbox_item_id", "Inbox"],
      ["action_record_id", "Action"],
      ["ephemeral_assignment_id", "Ephemeral"],
      ["git_promotion_id", "Git promotion"],
      ["release_package_id", "Release"],
    ].map(([key, label]) => {
      if (key.endsWith("_id")) {
        return [`${label} ID`, entry[key]];
      }
      return [key, label];
    }), {
      disclosureKey: peers.disclosures.teamDisclosureKey("timeline", entry.id, "technical"),
    });
  }

  function renderTimelineEntryDetails(entry) {
    const change = timelineChangeDescription(entry);
    const reason = timelineReason(entry);
    const related = timelineRelatedLabels(entry);
    return `
      <div class="timeline-domain-grid">
        <div><span>Event</span><strong>${escapeHtml(timelineEntryHeadline(entry))}</strong></div>
        <div><span>Initiated by</span><strong>${escapeHtml(timelineActorLabel(entry))}</strong></div>
        <div><span>Subject</span><strong>${escapeHtml(timelineEntrySubject(entry))}</strong></div>
        <div><span>Scope</span><strong>${escapeHtml(timelineScopeLabel(entry))}</strong></div>
      </div>
      <section class="timeline-change-section">
        <h4>Change${change.field ? ` · ${escapeHtml(change.field)}` : ""}</h4>
        ${
          change.before !== undefined
            ? `
              <div class="timeline-change-flow">
                <div><span>Before</span><strong>${renderIdentifierText(change.before)}</strong></div>
                <span aria-hidden="true">→</span>
                <div><span>After</span><strong>${renderIdentifierText(change.after)}</strong></div>
              </div>
            `
            : `<p><strong>${escapeHtml(change.label)}:</strong> ${renderIdentifierText(change.value)}</p>`
        }
        ${reason ? `<p class="timeline-reason"><strong>Reason:</strong> ${renderIdentifierText(reason)}</p>` : ""}
        ${
          !entry.before &&
          !entry.change &&
          !["created", "requested", "handled"].includes(text(entry.category)) &&
          !(entry.type === "git_promotion" && entry.category === "result")
            ? `<p class="timeline-retention-note">This snapshot does not retain the earlier field values or a complete transition diff.</p>`
            : ""
        }
      </section>
      ${
        related.length
          ? `<section class="timeline-related-work"><h4>Related people and work</h4><ul>${related.map((label) => `<li>${escapeHtml(label)}</li>`).join("")}</ul></section>`
          : ""
      }
      ${renderTimelineTechnicalDetails(entry)}
    `;
  }

  function describeTimelineStatus(timeline) {
    const filters = timeline.filters || {};
    const filterParts = [];
    if (filters.team_id) {
      filterParts.push(`team ${peers.app.state.teams?.[filters.team_id]?.name || "unavailable"}`);
    }
    if (filters.project_id) {
      filterParts.push(`project ${peers.app.state.projects?.[filters.project_id]?.name || "unavailable"}`);
    }
    if (filters.agent_id) {
      const agent = peers.app.state.agents?.[filters.agent_id];
      filterParts.push(`agent ${agent ? peers.agents.agentLabel(agent) : "unavailable"}`);
    }
    if (filters.type) {
      filterParts.push(`type ${filters.type}`);
    }
    if (filters.status) {
      filterParts.push(`status ${Array.isArray(filters.status) ? filters.status.join(", ") : filters.status}`);
    }
    if (filters.attention_needed !== undefined && filters.attention_needed !== null) {
      filterParts.push(`attention ${filters.attention_needed ? "yes" : "no"}`);
    }
    const filterSuffix = filterParts.length ? ` for ${filterParts.join(", ")}` : "";
    if (!(timeline.entries || []).length) {
      return `No timeline entries matched the current filters${filterSuffix}.`;
    }
    return `Activity timeline loaded${filterSuffix}.`;
  }

  function readTimelineFilters() {
    const teamId = timelineFilterValue("team_id");
    const projectId = timelineFilterValue("project_id");
    const agentId = timelineFilterValue("agent_id");
    const type = timelineFilterValue("type");
    const status = timelineFilterValue("status");
    const attentionValue = timelineFilterValue("attention_needed");
    return {
      team_id: teamId || null,
      project_id: projectId || null,
      agent_id: agentId || null,
      type: type || null,
      status: status || null,
      attention_needed:
        attentionValue === "true" ? true : attentionValue === "false" ? false : null,
    };
  }

  function timelineFilterValue(filterKey) {
    const controls = Array.from(document.querySelectorAll(`[data-timeline-filter="${filterKey}"]`));
    const activeControl = controls.find(peers.app.targetIsActive);
    return text((activeControl || controls[0])?.value);
  }

  function buildTimelineSelectOptions() {
    const teamLabels = new Map();
    const projectLabels = new Map();
    const agentLabels = new Map();
    const typeLabels = new Map();
    const statusLabels = new Map();
    const filters = readTimelineFilters();

    values(peers.app.state.teams)
      .sort(peers.people.comparePeopleTeams)
      .forEach((team) => {
        teamLabels.set(team.id, team.name || team.id);
      });
    values(peers.app.state.projects)
      .sort(peers.people.compareTeamProjects)
      .forEach((project) => {
        projectLabels.set(project.id, project.name || project.id);
      });
    values(peers.app.state.agents)
      .sort(peers.people.comparePeopleAgents)
      .forEach((agent) => {
        agentLabels.set(
          agent.id,
          `${peers.agents.agentLabel(agent)} · ${peers.agents.agentRoleLabel(agent)}`,
        );
      });

    (Array.isArray(timelineState.facets?.types) ? timelineState.facets.types : [])
      .forEach((type) => {
        const normalizedType = text(type);
        if (normalizedType) {
          typeLabels.set(normalizedType, humanizeToken(normalizedType));
        }
      });
    (Array.isArray(timelineState.facets?.statuses) ? timelineState.facets.statuses : [])
      .forEach((status) => {
        const normalizedStatus = text(status);
        if (normalizedStatus) {
          statusLabels.set(normalizedStatus, humanizeToken(normalizedStatus));
        }
      });

    timelineState.entries.forEach((entry) => {
      if (entry.team_id && !teamLabels.has(entry.team_id)) {
        teamLabels.set(entry.team_id, entry.team_id);
      }
      if (entry.project_id && !projectLabels.has(entry.project_id)) {
        projectLabels.set(entry.project_id, entry.project_id);
      }
      if (entry.actor_agent_id && !agentLabels.has(entry.actor_agent_id)) {
        agentLabels.set(entry.actor_agent_id, entry.actor_agent_id);
      }
      (entry.related_agent_ids || []).forEach((agentId) => {
        if (!agentLabels.has(agentId)) {
          agentLabels.set(agentId, agentId);
        }
      });
      if (entry.type && !typeLabels.has(entry.type)) {
        typeLabels.set(entry.type, humanizeToken(entry.type));
      }
      if (entry.status && !statusLabels.has(entry.status)) {
        statusLabels.set(entry.status, humanizeToken(entry.status));
      }
    });

    if (filters.team_id && !teamLabels.has(filters.team_id)) {
      teamLabels.set(filters.team_id, filters.team_id);
    }
    if (filters.project_id && !projectLabels.has(filters.project_id)) {
      projectLabels.set(filters.project_id, filters.project_id);
    }
    if (filters.agent_id && !agentLabels.has(filters.agent_id)) {
      agentLabels.set(filters.agent_id, filters.agent_id);
    }
    if (filters.type && !typeLabels.has(filters.type)) {
      typeLabels.set(filters.type, humanizeToken(filters.type));
    }
    if (filters.status && !statusLabels.has(filters.status)) {
      statusLabels.set(filters.status, humanizeToken(filters.status));
    }

    return {
      teams: optionsFromMap(teamLabels),
      projects: optionsFromMap(projectLabels),
      agents: optionsFromMap(agentLabels),
      types: optionsFromMap(typeLabels, timelineTypeOrder),
      statuses: optionsFromMap(statusLabels),
    };
  }

  function applyTimeline(payload) {
    timelineState = { ...emptyTimelineState(), ...payload };
    timelineLoading = false;
  }

  return {
    applyTimeline,
    bindTimelineFilters,
    bindTimelinePagination,
    buildTimelineSelectOptions,
    loadTimeline,
    normalizeTimelineTimestampForSort,
    renderTimeline,
    timelineSearchParams,
  };
}
