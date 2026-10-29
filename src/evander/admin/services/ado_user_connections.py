"""Compatibility boundary for optional personal Azure DevOps credentials."""
import re
from evander.optional_integrations import require_azure_devops_module

class AdoConnectionError(ValueError):
    pass


def normalize_organization(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("https://dev.azure.com/"):
        value = value.removeprefix("https://dev.azure.com/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", value):
        raise AdoConnectionError(
            "Enter your Azure DevOps organization name, for example contoso from "
            "dev.azure.com/contoso. An email address cannot be used here."
        )
    return value.lower()


def normalize_scope(value: str) -> str:
    value = value.strip().strip("/")
    if (
        not value
        or value.split("/")[0] != "root"
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise AdoConnectionError("Invalid directory path")
    return value


class AdoUserConnectionService:
    def __init__(self, runtime_dir, *, config=None):
        self.runtime_dir = runtime_dir
        self.config = config
        self._service = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        module = require_azure_devops_module("evander_azure_devops_addon.user_connections", self.config)
        if self._service is None:
            self._service = module.AdoUserConnectionService(self.runtime_dir)
        return getattr(self._service, name)
