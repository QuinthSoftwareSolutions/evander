export type ResetRedemptionAttempt = {
  worker_url: string;
  profile_id: string;
  account_id: string;
  idempotency_key: string;
};

export type ResetRedemptionAttempts = Record<string, ResetRedemptionAttempt>;

const STORAGE_PREFIX = "evander:subscription-reset-attempt:v1:";
export const RESET_REDEMPTION_STORAGE_EVENT = "evander:subscription-reset-attempts-changed";

export function resetRedemptionKey(
  workerUrl: string | null, profileId: string, accountId: string | null,
): string {
  return JSON.stringify([workerUrl?.trim().replace(/\/+$/, "") ?? "", profileId, accountId]);
}

function parseAttempt(key: string, raw: string): ResetRedemptionAttempt {
  const value = JSON.parse(raw) as Partial<ResetRedemptionAttempt> | null;
  if (!value || [value.worker_url, value.profile_id, value.account_id, value.idempotency_key]
    .some((field) => typeof field !== "string" || !field.trim())
    || resetRedemptionKey(value.worker_url!, value.profile_id!, value.account_id!) !== key) {
    throw new Error("Saved reset retry information is invalid; it must be restored before redemption.");
  }
  return {
    worker_url: value.worker_url!, profile_id: value.profile_id!,
    account_id: value.account_id!, idempotency_key: value.idempotency_key!,
  };
}

export function loadResetRedemptionAttempts(): ResetRedemptionAttempts {
  const attempts: ResetRedemptionAttempts = {};
  const storage = window.localStorage;
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key?.startsWith(STORAGE_PREFIX)) continue;
    const raw = storage.getItem(key);
    if (raw !== null) {
      const targetKey = key.slice(STORAGE_PREFIX.length);
      attempts[targetKey] = parseAttempt(targetKey, raw);
    }
  }
  return attempts;
}

export function rememberResetRedemptionAttempt(attempt: ResetRedemptionAttempt): ResetRedemptionAttempt {
  const targetKey = resetRedemptionKey(attempt.worker_url, attempt.profile_id, attempt.account_id);
  const key = STORAGE_PREFIX + targetKey;
  const previous = window.localStorage.getItem(key);
  if (previous !== null) return parseAttempt(targetKey, previous);
  // Persist synchronously before the request. A failed write must prevent sending.
  window.localStorage.setItem(key, JSON.stringify(attempt));
  window.dispatchEvent(new Event(RESET_REDEMPTION_STORAGE_EVENT));
  return attempt;
}

export function forgetResetRedemptionAttempt(attempt: ResetRedemptionAttempt): void {
  const targetKey = resetRedemptionKey(attempt.worker_url, attempt.profile_id, attempt.account_id);
  const key = STORAGE_PREFIX + targetKey;
  const raw = window.localStorage.getItem(key);
  // An older response must not remove a newer attempt for the same target.
  if (raw !== null && parseAttempt(targetKey, raw).idempotency_key === attempt.idempotency_key) {
    window.localStorage.removeItem(key);
    window.dispatchEvent(new Event(RESET_REDEMPTION_STORAGE_EVENT));
  }
}
