"""Stable error contract for optional Teams authentication."""

from typing import Any


class TeamsAuthError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        error_description: str | None = None,
        response_status_code: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.error_description = error_description
        self.response_status_code = response_status_code
        self.payload = payload or {}


class TeamsAuthenticationRequiredError(TeamsAuthError):
    pass


class TeamsAuthProtocolError(TeamsAuthError):
    pass


class TeamsAuthTransportError(TeamsAuthError):
    pass


class AuthorizationPendingError(TeamsAuthError):
    pass


class SlowDownError(TeamsAuthError):
    pass


class ConsentDeniedError(TeamsAuthError):
    pass


class DeviceCodeExpiredError(TeamsAuthError):
    pass


class DeviceCodePollingTimeoutError(TeamsAuthError):
    pass


class InvalidClientConfigurationError(TeamsAuthError):
    pass


class RefreshTokenRevokedError(TeamsAuthError):
    pass


class InteractionRequiredError(TeamsAuthError):
    pass


class TemporaryAuthFailureError(TeamsAuthError):
    pass
