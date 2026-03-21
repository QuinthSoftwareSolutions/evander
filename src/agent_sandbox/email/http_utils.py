from __future__ import annotations

import random
import time
from typing import Any

import httpx

_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"})


class ProviderRequestError(RuntimeError):
    """Raised when provider HTTP requests fail with contextual metadata."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempt = attempt
        self.max_attempts = max_attempts


def request_with_retries(
    *,
    client: httpx.Client,
    provider: str,
    operation: str,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    max_attempts: int = 4,
    base_backoff_seconds: float = 0.25,
    max_backoff_seconds: float = 2.0,
    retry_non_idempotent_methods: bool = False,
) -> httpx.Response:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    method_normalized = method.upper()
    method_is_retryable = retry_non_idempotent_methods or method_normalized in _IDEMPOTENT_METHODS
    last_error: Exception | None = None
    last_status_code: int | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.request(
                method_normalized,
                url,
                headers=headers,
                params=params,
                json=json,
            )
            last_status_code = response.status_code
            if response.status_code in {401, 403}:
                raise ProviderRequestError(
                    f"{provider}.{operation} failed without retry on auth/permission error: "
                    f"{method_normalized} {url} status={response.status_code} attempt={attempt}/{max_attempts}",
                    status_code=response.status_code,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )

            if response.status_code == 429 or response.status_code >= 500:
                if method_is_retryable and attempt < max_attempts:
                    _sleep_with_jitter(attempt, base_backoff_seconds, max_backoff_seconds)
                    continue
                if not method_is_retryable:
                    raise ProviderRequestError(
                        f"{provider}.{operation} failed without retry on transient HTTP error for non-idempotent "
                        f"method: {method_normalized} {url} status={response.status_code} "
                        f"attempt={attempt}/{max_attempts}",
                        status_code=response.status_code,
                        attempt=attempt,
                        max_attempts=max_attempts,
                    )
                raise ProviderRequestError(
                    f"{provider}.{operation} exhausted retries on transient HTTP error: "
                    f"{method_normalized} {url} status={response.status_code} attempts={attempt}/{max_attempts}",
                    status_code=response.status_code,
                    attempt=attempt,
                    max_attempts=max_attempts,
                )

            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if method_is_retryable and attempt < max_attempts:
                _sleep_with_jitter(attempt, base_backoff_seconds, max_backoff_seconds)
                continue
            if not method_is_retryable:
                raise ProviderRequestError(
                    f"{provider}.{operation} failed without retry on transient network error for "
                    f"non-idempotent method: {method_normalized} {url} attempt={attempt}/{max_attempts} "
                    f"error={type(exc).__name__}: {exc}",
                    status_code=last_status_code,
                    attempt=attempt,
                    max_attempts=max_attempts,
                ) from exc
            break
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response else None
            raise ProviderRequestError(
                f"{provider}.{operation} failed: {method_normalized} {url} "
                f"status={status_code} attempt={attempt}/{max_attempts} error={exc}",
                status_code=status_code,
                attempt=attempt,
                max_attempts=max_attempts,
            ) from exc
        except ProviderRequestError as exc:
            raise ProviderRequestError(
                f"{provider}.{operation} failed: {method_normalized} {url} "
                f"status={last_status_code} attempt={attempt}/{max_attempts} error={exc}",
                status_code=last_status_code,
                attempt=attempt,
                max_attempts=max_attempts,
            ) from exc

    error_detail = f"{type(last_error).__name__}: {last_error}" if last_error else "unknown network error"
    raise ProviderRequestError(
        f"{provider}.{operation} exhausted retries on transient network error: "
        f"{method_normalized} {url} status={last_status_code} attempts={max_attempts}/{max_attempts} "
        f"error={error_detail}",
        status_code=last_status_code,
        attempt=max_attempts,
        max_attempts=max_attempts,
    ) from last_error


def _sleep_with_jitter(attempt: int, base_backoff_seconds: float, max_backoff_seconds: float) -> None:
    exponential = base_backoff_seconds * (2 ** (attempt - 1))
    jittered = exponential * (0.5 + random.random())
    time.sleep(min(jittered, max_backoff_seconds))
