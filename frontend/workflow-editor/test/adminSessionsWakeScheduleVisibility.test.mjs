import assert from "node:assert/strict";
import { test } from "node:test";

import {
  isPastOneTimeWakeSchedule,
  visibleWakeSchedules,
} from "../src/features/sessions/wakeScheduleVisibility.ts";

const nowMs = Date.parse("2026-06-10T20:10:00Z");

test("past one-time wake schedules are hidden by default", () => {
  const schedules = [
    {
      request_id: "past-once",
      schedule_kind: "resume_at",
      resume_at: "2026-06-10T20:08:45Z",
    },
    {
      request_id: "future-once",
      schedule_kind: "resume_at",
      resume_at: "2026-06-10T20:12:00Z",
    },
    {
      request_id: "recurring",
      schedule_kind: "cron",
      resume_at: null,
    },
  ];

  assert.equal(isPastOneTimeWakeSchedule(schedules[0], nowMs), true);
  assert.equal(isPastOneTimeWakeSchedule(schedules[1], nowMs), false);
  assert.equal(isPastOneTimeWakeSchedule(schedules[2], nowMs), false);
  assert.deepEqual(
    visibleWakeSchedules(schedules, { showPastOneTime: false, nowMs }).map((item) => item.request_id),
    ["future-once", "recurring"],
  );
});

test("past one-time wake schedules can still be shown", () => {
  const schedules = [
    {
      request_id: "past-once",
      schedule_kind: "resume_at",
      resume_at: "2026-06-10T20:08:45Z",
    },
  ];

  assert.deepEqual(
    visibleWakeSchedules(schedules, { showPastOneTime: true, nowMs }).map((item) => item.request_id),
    ["past-once"],
  );
});
