export type WakeScheduleVisibilityItem = {
  schedule_kind: "cron" | "resume_at";
  resume_at?: string | null;
  status?: string | null;
};

export function isPastOneTimeWakeSchedule(
  item: WakeScheduleVisibilityItem,
  nowMs = Date.now(),
): boolean {
  if (item.schedule_kind !== "resume_at" || !item.resume_at) {
    return false;
  }
  const resumeAtMs = Date.parse(item.resume_at);
  if (!Number.isFinite(resumeAtMs)) {
    return false;
  }
  return resumeAtMs <= nowMs;
}

export function visibleWakeSchedules<T extends WakeScheduleVisibilityItem>(
  items: readonly T[],
  options: {
    showPastOneTime: boolean;
    nowMs?: number;
  },
): T[] {
  const nowMs = options.nowMs ?? Date.now();
  if (options.showPastOneTime) {
    return [...items];
  }
  // Cancelled rows stay visible as history even when their one-time timestamp is in the past.
  return items.filter((item) => item.status === "cancelled" || !isPastOneTimeWakeSchedule(item, nowMs));
}
