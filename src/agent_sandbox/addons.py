from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path, PurePosixPath
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.storage import JsonObjectStore

_ADDON_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class AddonLoaderConfig(BaseModel):
    modules: list[str] = Field(default_factory=list)
    enabled_entry_points: bool = False
    enabled_addon_ids: list[str] | None = None

    @field_validator("modules", mode="before")
    @classmethod
    def normalize_modules(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("enabled_addon_ids", mode="before")
    @classmethod
    def normalize_enabled_addon_ids(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            addon_id = validate_addon_id(str(item).strip())
            if addon_id in seen:
                continue
            seen.add(addon_id)
            normalized.append(addon_id)
        return normalized


class AddonAdminNavigation(BaseModel):
    label: str
    short_label: str | None = None
    description: str
    ui_path: str | None = None

    @field_validator("label", "description", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("short_label", "ui_path", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @model_validator(mode="after")
    def apply_defaults(self) -> "AddonAdminNavigation":
        if self.short_label is None:
            self.short_label = self.label
        if self.ui_path is not None and not self.ui_path.startswith("/"):
            raise ValueError("ui_path must start with / when provided")
        return self


@dataclass(frozen=True)
class AddonContext:
    addon_id: str
    runtime_dir: Path
    addon_runtime_dir: Path
    project_dir: Path | None = None
    config: Any | None = None
    service: Any | None = None
    auth: Any | None = None


AddonRouteFactory = Callable[[AddonContext], Sequence[Any]]


@dataclass(frozen=True)
class AddonRegistration:
    addon_id: str
    display_name: str
    admin_navigation: AddonAdminNavigation | None = None
    admin_api_routes: Sequence[Any] | AddonRouteFactory = field(default_factory=tuple)
    admin_ui_routes: Sequence[Any] | AddonRouteFactory = field(default_factory=tuple)
    workflow_providers: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "addon_id", validate_addon_id(self.addon_id))
        display_name = str(self.display_name or "").strip()
        if not display_name:
            raise ValueError("display_name must be a non-empty string")
        object.__setattr__(self, "display_name", display_name)
        if self.admin_navigation is not None and not isinstance(
            self.admin_navigation, AddonAdminNavigation
        ):
            object.__setattr__(
                self,
                "admin_navigation",
                AddonAdminNavigation.model_validate(self.admin_navigation),
            )
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True)
class LoadedAddonCatalog:
    registrations: tuple[AddonRegistration, ...]
    loaded_addons: tuple[str, ...]


def validate_addon_id(value: str) -> str:
    addon_id = str(value or "").strip()
    if not _ADDON_ID_PATTERN.fullmatch(addon_id):
        raise ValueError(
            "addon_id must use lowercase letters, numbers, dots, underscores, or hyphens, "
            "and must start with a lowercase letter or number"
        )
    return addon_id


def addon_runtime_dir(runtime_dir: str | Path, addon_id: str) -> Path:
    return Path(runtime_dir) / "addons" / validate_addon_id(addon_id)


def addon_storage_path(runtime_dir: str | Path, addon_id: str, *parts: str | Path) -> Path:
    root = addon_runtime_dir(runtime_dir, addon_id)
    if not parts:
        return root
    normalized_parts: list[str] = []
    for raw_part in parts:
        text = str(raw_part)
        if not text or "\x00" in text:
            raise ValueError("add-on storage path parts must be non-empty")
        candidate = Path(text)
        if candidate.is_absolute():
            raise ValueError("add-on storage path parts must be relative")
        posix = PurePosixPath(text.replace("\\", "/"))
        if any(part in {"", ".", ".."} for part in posix.parts):
            raise ValueError("add-on storage path parts must not contain traversal segments")
        normalized_parts.extend(posix.parts)
    path = root.joinpath(*normalized_parts)
    resolved_root = root.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("add-on storage path must remain within the add-on runtime directory") from exc
    return path


def addon_json_object_store(
    runtime_dir: str | Path,
    addon_id: str,
    name: str | Path,
    *,
    initial_object: dict[str, Any] | None = None,
    file_mode: int | None = None,
) -> JsonObjectStore[Any]:
    return JsonObjectStore(
        addon_storage_path(runtime_dir, addon_id, name),
        initial_object or {},
        file_mode=file_mode,
    )


def load_addons(config: AddonLoaderConfig | Mapping[str, Any] | None = None) -> LoadedAddonCatalog:
    loader_config = (
        config
        if isinstance(config, AddonLoaderConfig)
        else AddonLoaderConfig.model_validate(config or {})
    )
    registration_specs: list[tuple[str, Any]] = []
    for module_name in loader_config.modules:
        registration_specs.append((module_name, _load_registration_from_module(module_name)))
    if loader_config.enabled_entry_points:
        for discovered in _iter_addon_entry_points():
            registration_specs.append(
                (f"entry_point:{discovered.value}", _load_registration_from_entry_point(discovered))
            )

    registrations: list[AddonRegistration] = []
    loaded_sources: list[str] = []
    seen_addon_ids: dict[str, str] = {}
    enabled_filter = (
        set(loader_config.enabled_addon_ids)
        if loader_config.enabled_addon_ids is not None
        else None
    )
    for source, raw_registration in registration_specs:
        registration = _coerce_registration(source=source, value=raw_registration)
        if enabled_filter is not None and registration.addon_id not in enabled_filter:
            continue
        previous_source = seen_addon_ids.get(registration.addon_id)
        if previous_source is not None:
            raise ValueError(
                f"Duplicate add-on id {registration.addon_id!r} from {source}; "
                f"already loaded from {previous_source}"
            )
        seen_addon_ids[registration.addon_id] = source
        registrations.append(registration)
        loaded_sources.append(source)

    if enabled_filter is not None:
        missing = sorted(enabled_filter - set(seen_addon_ids))
        if missing:
            raise ValueError(f"Configured add-ons were not found: {', '.join(missing)}")

    return LoadedAddonCatalog(
        registrations=tuple(registrations),
        loaded_addons=tuple(loaded_sources),
    )


def addon_admin_navigation_payloads(catalog: LoadedAddonCatalog) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for registration in catalog.registrations:
        navigation = registration.admin_navigation
        if navigation is None:
            continue
        payloads.append(
            {
                "addon_id": registration.addon_id,
                "display_name": registration.display_name,
                "tab_key": f"addon:{registration.addon_id}",
                "label": navigation.label,
                "short_label": navigation.short_label or navigation.label,
                "description": navigation.description,
                "api_base_path": f"/api/addons/{registration.addon_id}",
                "ui_path": navigation.ui_path,
            }
        )
    return payloads


def resolve_addon_admin_api_routes(
    registration: AddonRegistration, context: AddonContext
) -> tuple[Any, ...]:
    return _resolve_addon_routes(
        registration.admin_api_routes,
        context,
        source=f"add-on {registration.addon_id} Admin API routes",
    )


def resolve_addon_admin_ui_routes(
    registration: AddonRegistration, context: AddonContext
) -> tuple[Any, ...]:
    return _resolve_addon_routes(
        registration.admin_ui_routes,
        context,
        source=f"add-on {registration.addon_id} Admin UI routes",
    )


def _iter_addon_entry_points() -> tuple[EntryPoint, ...]:
    discovered = entry_points(group="agent_sandbox.addons")
    return tuple(sorted(discovered, key=lambda item: item.name))


def _load_registration_from_entry_point(addon_entry_point: EntryPoint) -> Any:
    return addon_entry_point.load()


def _load_registration_from_module(module_name: str) -> Any:
    module = import_module(module_name)
    register = getattr(module, "register_addon", None)
    if callable(register):
        return register()
    return module


def _coerce_registration(*, source: str, value: Any) -> AddonRegistration:
    if callable(value):
        value = value()
    if isinstance(value, AddonRegistration):
        return value
    register = getattr(value, "register_addon", None)
    if callable(register):
        candidate = register()
        if isinstance(candidate, AddonRegistration):
            return candidate
        value = candidate

    if isinstance(value, Mapping):
        return AddonRegistration(
            addon_id=str(value.get("addon_id") or ""),
            display_name=str(value.get("display_name") or value.get("addon_id") or ""),
            admin_navigation=_coerce_navigation(value.get("admin_navigation")),
            admin_api_routes=value.get("admin_api_routes") or (),
            admin_ui_routes=value.get("admin_ui_routes") or (),
            workflow_providers=value.get("workflow_providers"),
            metadata=_coerce_metadata(value.get("metadata"), source=source),
        )

    addon_id = getattr(value, "addon_id", "")
    return AddonRegistration(
        addon_id=str(addon_id or ""),
        display_name=str(getattr(value, "display_name", addon_id) or ""),
        admin_navigation=_coerce_navigation(getattr(value, "admin_navigation", None)),
        admin_api_routes=getattr(value, "admin_api_routes", ()),
        admin_ui_routes=getattr(value, "admin_ui_routes", ()),
        workflow_providers=getattr(value, "workflow_providers", None),
        metadata=_coerce_metadata(getattr(value, "metadata", None), source=source),
    )


def _coerce_navigation(value: Any) -> AddonAdminNavigation | None:
    if value is None:
        return None
    if isinstance(value, AddonAdminNavigation):
        return value
    return AddonAdminNavigation.model_validate(value)


def _coerce_metadata(value: Any, *, source: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"Add-on metadata from {source} must be a mapping")
    return dict(value)


def _resolve_addon_routes(
    raw_routes: Sequence[Any] | AddonRouteFactory,
    context: AddonContext,
    *,
    source: str,
) -> tuple[Any, ...]:
    routes = raw_routes(context) if callable(raw_routes) else raw_routes
    if routes is None:
        return ()
    if isinstance(routes, (str, bytes)) or not isinstance(routes, Sequence):
        raise TypeError(f"{source} must be a sequence of Starlette routes")
    normalized = tuple(routes)
    for route in normalized:
        path = str(getattr(route, "path", "") or "")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError(f"{source} must use relative route paths that start with /")
        posix = PurePosixPath(path)
        if any(part in {".."} for part in posix.parts):
            raise ValueError(f"{source} must not contain traversal segments")
    return normalized
