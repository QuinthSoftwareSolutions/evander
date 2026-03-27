from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import GatewayConfig, load_config
from .directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from .directories import DirectoryService, DirectoryStore
from .models import SessionManifest
from .storage import JsonStore

logger = logging.getLogger(__name__)

SHADOW_DOMAINS = ("policy", "providers", "workers", "vm-defaults")


@dataclass(frozen=True)
class ShadowDomainContext:
    domain: str
    diff_paths: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkerDirectoryContext:
    directory_path: str
    source: str
    session_id: str | None = None
    domains: tuple[ShadowDomainContext, ...] = ()

    def as_log_dict(self) -> dict[str, Any]:
        return {
            "directory_path": self.directory_path,
            "source": self.source,
            "session_id": self.session_id,
            "domains": [
                {
                    "domain": domain.domain,
                    "diff_paths": list(domain.diff_paths),
                    "validation_errors": list(domain.validation_errors),
                }
                for domain in self.domains
            ],
        }


class WorkerDirectoryContextResolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.sessions = JsonStore(config.session_store_path, SessionManifest)
        directory_store = DirectoryStore(config.directories_store_path)
        self.directories = DirectoryService(directory_store)
        self.config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
        self.resolver = DirectoryConfigResolver(
            self.directories,
            base_gateway_config=config,
            config_store=self.config_store,
        )

    @classmethod
    def from_config_path(cls, path: str | Path) -> "WorkerDirectoryContextResolver":
        return cls(load_config(path))

    def resolve_for_session(self, session_id: str | None, *, fallback_directory_path: str | None = None, source: str) -> WorkerDirectoryContext:
        directory_path = fallback_directory_path or "root"
        if session_id:
            session = self.sessions.get(session_id)
            if session is not None:
                directory_path = session.directory_path
        return self.resolve_for_directory(directory_path, session_id=session_id, source=source)

    def resolve_effective_domain_for_session(
        self,
        session_id: str | None,
        *,
        domain: str,
        fallback_directory_path: str | None = None,
    ):
        directory_path = fallback_directory_path or "root"
        if session_id:
            session = self.sessions.get(session_id)
            if session is not None:
                directory_path = session.directory_path
        return self.resolve_effective_domain_for_directory(directory_path, domain=domain)

    def resolve_for_directory(
        self,
        directory_path: str | None,
        *,
        session_id: str | None = None,
        source: str,
    ) -> WorkerDirectoryContext:
        normalized_path = "root"
        if directory_path:
            try:
                normalized_path = self.directories.show(directory_path).directory.path
            except ValueError:
                normalized_path = "root"
        domains: list[ShadowDomainContext] = []
        for domain in SHADOW_DOMAINS:
            base_config = get_domain_base_config(self.config, domain)
            resolution = self.resolver.resolve_domain(
                normalized_path,
                domain=domain,
                base_config=base_config,
                validator=lambda candidate, domain=domain: validate_effective_domain_payload(domain, candidate),
            )
            diff_paths = tuple(_diff_paths(base_config, resolution.effective_config))
            validation_errors = tuple(resolution.validation_errors)
            domains.append(
                ShadowDomainContext(
                    domain=domain,
                    diff_paths=diff_paths,
                    validation_errors=validation_errors,
                )
            )
        return WorkerDirectoryContext(
            directory_path=normalized_path,
            source=source,
            session_id=session_id,
            domains=tuple(domains),
        )

    def resolve_effective_domain_for_directory(
        self,
        directory_path: str | None,
        *,
        domain: str,
    ):
        normalized_path = "root"
        if directory_path:
            try:
                normalized_path = self.directories.show(directory_path).directory.path
            except ValueError:
                normalized_path = "root"
        return self.resolver.resolve_domain(
            normalized_path,
            domain=domain,
            base_config=None,
            validator=lambda candidate, domain=domain: validate_effective_domain_payload(domain, candidate),
        )


def log_shadow_context(logger_obj: logging.Logger, *, prefix: str, context: WorkerDirectoryContext) -> None:
    changed = [domain for domain in context.domains if domain.diff_paths or domain.validation_errors]
    if not changed:
        logger_obj.debug("%s directory_context=%s", prefix, json.dumps(context.as_log_dict(), sort_keys=True))
        return
    logger_obj.info("%s directory_context=%s", prefix, json.dumps(context.as_log_dict(), sort_keys=True))


def _diff_paths(base: Any, candidate: Any, path: tuple[str, ...] = ()) -> list[str]:
    if isinstance(base, dict) and isinstance(candidate, dict):
        diffs: list[str] = []
        for key in sorted(set(base) | set(candidate)):
            if key not in base or key not in candidate:
                diffs.append(".".join((*path, str(key))))
                continue
            diffs.extend(_diff_paths(base[key], candidate[key], (*path, str(key))))
        return diffs
    if base != candidate:
        return [".".join(path)] if path else ["<root>"]
    return []
