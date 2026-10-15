from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from importlib import import_module, util
from importlib.metadata import EntryPoint, entry_points
import json
from pathlib import Path, PurePosixPath
import re
import sys
import types
from typing import Any

from evander.outbound_identity import OutboundIdentityResolverFactory
from evander.integration_contracts import AddonIntegration

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.storage import JsonObjectStore

_ADDON_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_ADDON_PACKAGE_MANIFEST = "addon.json"
DEFAULT_ADDON_PACKAGE_DIRS = ("addons", "src", "packages")


class AddonLoaderConfig(BaseModel):
    modules: list[str] = Field(default_factory=list)
    package_dirs: list[str] = Field(default_factory=lambda: list(DEFAULT_ADDON_PACKAGE_DIRS))
    enabled_entry_points: bool = False
    enabled_addon_ids: list[str] | None = None

    @field_validator("modules", "package_dirs", mode="before")
    @classmethod
    def normalize_string_list(cls, value: object) -> object:
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
    directory_path: str = "root"


AddonRouteFactory = Callable[[AddonContext], Sequence[Any]]
AddonMcpToolFactory = Callable[[Any, AddonContext], Sequence[str] | None]


class AddonMcpTool(BaseModel):
    name: str
    label: str
    description: str
    group: str | None = None
    # Read-only discovery can accompany existing grants for this add-on's operations.
    discovery_for: tuple[str, ...] = ()

    @field_validator("name", "label", "description", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text

    @field_validator("group", mode="before")
    @classmethod
    def normalize_optional_group(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None


@dataclass(frozen=True)
class AddonRegistration:
    addon_id: str
    display_name: str
    admin_navigation: AddonAdminNavigation | None = None
    admin_api_routes: Sequence[Any] | AddonRouteFactory = field(default_factory=tuple)
    admin_ui_routes: Sequence[Any] | AddonRouteFactory = field(default_factory=tuple)
    mcp_tools: AddonMcpToolFactory | None = None
    mcp_tool_catalog: Sequence[AddonMcpTool | Mapping[str, Any]] = field(default_factory=tuple)
    workflow_providers: Any | None = None
    connector_drivers: Mapping[str, Any] = field(default_factory=dict)
    outbound_identity_resolver: OutboundIdentityResolverFactory | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    website_session_adapters: Mapping[str, Any] = field(default_factory=dict)
    integration: AddonIntegration | Mapping[str, Any] | None = None
    requires_gateway_restart: bool = False
    required_restart_services: Sequence[str] = field(default_factory=tuple)
    security_scan_providers: Mapping[str, Any] = field(default_factory=dict)
    # Optional owners validate their checkout's current lifecycle without core imports.
    git_submission_validator: Callable[[AddonContext, Mapping[str, Any]], bool] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "addon_id", validate_addon_id(self.addon_id))
        display_name = str(self.display_name or "").strip()
        if not display_name:
            raise ValueError("display_name must be a non-empty string")
        object.__setattr__(self, "display_name", display_name)
        if self.git_submission_validator is not None and not callable(self.git_submission_validator):
            raise ValueError("git_submission_validator must be callable")
        if type(self.requires_gateway_restart) is not bool:
            raise ValueError("requires_gateway_restart must be a boolean")
        if (
            not isinstance(self.required_restart_services, Sequence)
            or isinstance(self.required_restart_services, str)
            or any(
                not isinstance(service, str)
                or not re.fullmatch(r"evander-[a-z0-9]+(?:-[a-z0-9]+)*", service)
                for service in self.required_restart_services
            )
        ):
            raise ValueError(
                "required_restart_services must contain Evander service names without .service"
            )
        object.__setattr__(
            self, "required_restart_services", tuple(dict.fromkeys(self.required_restart_services))
        )
        if self.admin_navigation is not None and not isinstance(
            self.admin_navigation, AddonAdminNavigation
        ):
            object.__setattr__(
                self,
                "admin_navigation",
                AddonAdminNavigation.model_validate(self.admin_navigation),
            )
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "connector_drivers", dict(self.connector_drivers or {}))
        object.__setattr__(self, "website_session_adapters", dict(self.website_session_adapters or {}))
        object.__setattr__(self, "security_scan_providers", dict(self.security_scan_providers or {}))
        if self.integration is not None:
            integration = self.integration
            if not isinstance(integration, AddonIntegration):
                integration = AddonIntegration(**dict(integration))
            if self.addon_id in integration.required_addons:
                raise ValueError("an integration cannot depend on itself")
            object.__setattr__(self, "integration", integration)
        tool_catalog: list[AddonMcpTool] = []
        seen_tool_names: set[str] = set()
        for raw_tool in self.mcp_tool_catalog or ():
            tool = (
                raw_tool
                if isinstance(raw_tool, AddonMcpTool)
                else AddonMcpTool.model_validate(raw_tool)
            )
            if tool.name in seen_tool_names:
                raise ValueError(
                    f"add-on {self.addon_id} declares duplicate MCP tool {tool.name!r}"
                )
            seen_tool_names.add(tool.name)
            tool_catalog.append(tool)
        for tool in tool_catalog:
            if tool.name in tool.discovery_for or set(tool.discovery_for) - seen_tool_names:
                raise ValueError(
                    f"add-on {self.addon_id} discovery tool {tool.name!r} must reference "
                    "other tools in its own catalog"
                )
        object.__setattr__(self, "mcp_tool_catalog", tuple(tool_catalog))


@dataclass(frozen=True)
class LoadedAddonCatalog:
    registrations: tuple[AddonRegistration, ...]
    loaded_addons: tuple[str, ...]


def addon_mcp_discovery_permissions(catalog: LoadedAddonCatalog) -> dict[str, tuple[str, ...]]:
    return {
        tool.name: tool.discovery_for
        for registration in catalog.registrations
        for tool in registration.mcp_tool_catalog
        if tool.discovery_for
    }


def addon_mcp_tool_catalog_payloads(catalog: LoadedAddonCatalog) -> list[dict[str, str]]:
    payloads: list[dict[str, str]] = []
    seen: dict[str, str] = {}
    for registration in catalog.registrations:
        group = (
            registration.admin_navigation.label
            if registration.admin_navigation is not None
            else registration.display_name
        )
        for tool in registration.mcp_tool_catalog:
            previous_addon_id = seen.get(tool.name)
            if previous_addon_id is not None:
                raise ValueError(
                    f"MCP tool {tool.name!r} is declared by both "
                    f"{previous_addon_id!r} and {registration.addon_id!r}"
                )
            seen[tool.name] = registration.addon_id
            payloads.append(
                {
                    "value": tool.name,
                    "label": tool.label,
                    "description": tool.description,
                    "group": tool.group or group,
                    "provider": registration.addon_id,
                    "addon_id": registration.addon_id,
                }
            )
    return payloads


@dataclass(frozen=True)
class DiscoveredAddon:
    source: str
    source_type: str
    entry_point_name: str | None = None
    registration: AddonRegistration | None = None
    error: str | None = None


@dataclass(frozen=True)
class _AddonRegistrationSpec:
    source: str
    source_type: str
    load: Callable[[], Any]
    entry_point_name: str | None = None
    default_enabled: bool = True
    manifest_path: Path | None = None


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
        raise ValueError(
            "add-on storage path must remain within the add-on runtime directory"
        ) from exc
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


def load_addons(
    config: AddonLoaderConfig | Mapping[str, Any] | None = None,
    *,
    base_dir: str | Path | None = None,
) -> LoadedAddonCatalog:
    loader_config = (
        config
        if isinstance(config, AddonLoaderConfig)
        else AddonLoaderConfig.model_validate(config or {})
    )
    registration_specs = _addon_registration_specs(loader_config, base_dir=base_dir)

    registrations: list[AddonRegistration] = []
    loaded_sources: list[str] = []
    seen_addon_ids: dict[str, _AddonRegistrationSpec] = {}
    enabled_filter = (
        set(loader_config.enabled_addon_ids)
        if loader_config.enabled_addon_ids is not None
        else None
    )
    for spec in registration_specs:
        if not spec.default_enabled and enabled_filter is None:
            continue
        if (
            spec.source_type == "package"
            and enabled_filter is not None
            and not _addon_package_might_be_enabled(spec, enabled_filter)
        ):
            continue
        try:
            raw_registration = spec.load()
            registration = _coerce_registration(source=spec.source, value=raw_registration)
        except Exception:
            if spec.source_type == "package":
                continue
            raise
        if enabled_filter is not None and registration.addon_id not in enabled_filter:
            continue
        previous_spec = seen_addon_ids.get(registration.addon_id)
        if previous_spec is not None:
            if spec.source_type == "package" and previous_spec.source_type != "package":
                continue
            raise ValueError(
                f"Duplicate add-on id {registration.addon_id!r} from {spec.source}; "
                f"already loaded from {previous_spec.source}"
            )
        seen_addon_ids[registration.addon_id] = spec
        registrations.append(registration)
        loaded_sources.append(spec.source)

    if enabled_filter is not None:
        missing = sorted(enabled_filter - set(seen_addon_ids))
        if missing:
            raise ValueError(f"Configured add-ons were not found: {', '.join(missing)}")

    return LoadedAddonCatalog(
        registrations=tuple(registrations),
        loaded_addons=tuple(loaded_sources),
    )


def discover_addons(
    config: AddonLoaderConfig | Mapping[str, Any] | None = None,
    *,
    base_dir: str | Path | None = None,
) -> tuple[DiscoveredAddon, ...]:
    loader_config = (
        config
        if isinstance(config, AddonLoaderConfig)
        else AddonLoaderConfig.model_validate(config or {})
    )
    records: list[DiscoveredAddon] = []
    for spec in _addon_registration_specs(loader_config, base_dir=base_dir):
        try:
            registration = _coerce_registration(
                source=spec.source,
                value=spec.load(),
            )
        except Exception as exc:
            records.append(
                DiscoveredAddon(
                    source=spec.source,
                    source_type=spec.source_type,
                    entry_point_name=spec.entry_point_name,
                    error=str(exc),
                )
            )
            continue
        records.append(
            DiscoveredAddon(
                source=spec.source,
                source_type=spec.source_type,
                entry_point_name=spec.entry_point_name,
                registration=registration,
            )
        )

    seen_records_by_id: dict[str, DiscoveredAddon] = {}
    normalized: list[DiscoveredAddon] = []
    for record in records:
        if record.registration is None:
            normalized.append(record)
            continue
        previous_record = seen_records_by_id.get(record.registration.addon_id)
        if previous_record is not None:
            if record.source_type == "package" and previous_record.source_type != "package":
                continue
            normalized.append(
                DiscoveredAddon(
                    source=record.source,
                    source_type=record.source_type,
                    entry_point_name=record.entry_point_name,
                    registration=record.registration,
                    error=(
                        f"Duplicate add-on id {record.registration.addon_id!r}; "
                        f"already discovered from {previous_record.source}"
                    ),
                )
            )
            continue
        seen_records_by_id[record.registration.addon_id] = record
        normalized.append(record)
    return tuple(normalized)


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


def register_addon_mcp_tools(
    registration: AddonRegistration, server: Any, context: AddonContext
) -> tuple[str, ...]:
    if registration.mcp_tools is None:
        return ()
    registered = registration.mcp_tools(server, context)
    if registered is None:
        return ()
    if isinstance(registered, (str, bytes)) or not isinstance(registered, Sequence):
        raise TypeError(
            f"add-on {registration.addon_id} MCP tool registrar must return a sequence of names"
        )
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in registered:
        name = str(raw_name or "").strip()
        if not name:
            raise ValueError(f"add-on {registration.addon_id} registered an empty MCP tool name")
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


def _iter_addon_entry_points() -> tuple[EntryPoint, ...]:
    discovered = entry_points(group="evander.addons")
    return tuple(sorted(discovered, key=lambda item: item.name))


def _load_registration_from_entry_point(addon_entry_point: EntryPoint) -> Any:
    return addon_entry_point.load()


def _load_registration_from_module(module_name: str) -> Any:
    module = import_module(module_name)
    return _load_registration_from_module_object(module)


def _load_registration_from_module_object(module: types.ModuleType) -> Any:
    register = getattr(module, "register_addon", None)
    if callable(register):
        return register()
    return module


def _addon_registration_specs(
    loader_config: AddonLoaderConfig,
    *,
    base_dir: str | Path | None = None,
) -> list[_AddonRegistrationSpec]:
    specs: list[_AddonRegistrationSpec] = []
    for module_name in loader_config.modules:
        source = str(module_name).strip()
        if not source:
            continue
        specs.append(
            _AddonRegistrationSpec(
                source=source,
                source_type="module",
                load=lambda source=source: _load_registration_from_module(source),
            )
        )
    if loader_config.enabled_entry_points:
        for discovered in _iter_addon_entry_points():
            specs.append(
                _AddonRegistrationSpec(
                    source=f"entry_point:{discovered.value}",
                    source_type="entry_point",
                    entry_point_name=discovered.name,
                    load=lambda discovered=discovered: _load_registration_from_entry_point(
                        discovered
                    ),
                )
            )
    for package_dir, manifest_path, source in _iter_addon_package_manifests(
        loader_config.package_dirs, base_dir=base_dir
    ):
        specs.append(
            _AddonRegistrationSpec(
                source=source,
                source_type="package",
                default_enabled=False,
                manifest_path=manifest_path,
                load=lambda package_dir=package_dir, manifest_path=manifest_path: (
                    _load_registration_from_package_manifest(
                        package_dir=package_dir,
                        manifest_path=manifest_path,
                    )
                ),
            )
        )
    return specs


def _iter_addon_package_manifests(
    package_dirs: Sequence[str],
    *,
    base_dir: str | Path | None = None,
) -> tuple[tuple[Path, Path, str], ...]:
    base_path = Path(base_dir).expanduser() if base_dir is not None else None
    records: list[tuple[Path, Path, str]] = []
    for raw_package_dir in package_dirs:
        root = Path(raw_package_dir).expanduser()
        if not root.is_absolute() and base_path is not None:
            root = base_path / root
        if not root.exists() or not root.is_dir():
            continue
        package_children = (child for child in root.iterdir() if child.is_dir())
        for package_dir in sorted(package_children, key=_path_sort_key):
            manifest_path = package_dir / _ADDON_PACKAGE_MANIFEST
            if not manifest_path.is_file():
                continue
            records.append(
                (
                    package_dir,
                    manifest_path,
                    f"package:{_display_package_dir(package_dir, base_path=base_path)}",
                )
            )
    return tuple(records)


def _path_sort_key(path: Path) -> str:
    return path.name.casefold()


def _display_package_dir(package_dir: Path, *, base_path: Path | None) -> str:
    if base_path is not None:
        try:
            return package_dir.relative_to(base_path).as_posix()
        except ValueError:
            pass
    return package_dir.as_posix()


def _addon_package_might_be_enabled(spec: _AddonRegistrationSpec, enabled_filter: set[str]) -> bool:
    if spec.manifest_path is None:
        return True
    try:
        addon_id = _addon_package_manifest_addon_id(spec.manifest_path)
    except Exception:
        return True
    return addon_id is None or addon_id in enabled_filter


def _addon_package_manifest_addon_id(manifest_path: Path) -> str | None:
    manifest = _load_addon_package_manifest(manifest_path)
    addon_id = str(manifest.get("addon_id") or "").strip()
    return validate_addon_id(addon_id) if addon_id else None


def _load_registration_from_package_manifest(
    *,
    package_dir: Path,
    manifest_path: Path,
) -> Any:
    manifest = _load_addon_package_manifest(manifest_path)
    module_name = str(manifest.get("module") or "").strip()
    if not module_name:
        raise ValueError(f"{manifest_path} must declare a non-empty module")
    return _load_registration_from_package_module(
        package_dir=package_dir,
        module_name=module_name,
        manifest_path=manifest_path,
    )


def _load_addon_package_manifest(manifest_path: Path) -> Mapping[str, Any]:
    if not manifest_path.is_file():
        raise ValueError(f"Missing add-on package manifest: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid add-on package manifest {manifest_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"Add-on package manifest {manifest_path} must be a JSON object")
    return payload


def _load_registration_from_package_module(
    *,
    package_dir: Path,
    module_name: str,
    manifest_path: Path,
) -> Any:
    module_path, submodule_search_locations = _addon_package_module_location(
        package_dir=package_dir,
        module_name=module_name,
        manifest_path=manifest_path,
    )
    found_existing, registration = _load_existing_package_module_registration(
        module_name=module_name,
        module_path=module_path,
    )
    if found_existing:
        return registration

    root_module_name = _addon_package_import_root(package_dir)
    _clear_addon_package_import(root_module_name)
    _install_addon_package_parent_modules(root_module_name, package_dir, module_name)
    loaded_module_name = f"{root_module_name}.{module_name}"
    spec = util.spec_from_file_location(
        loaded_module_name,
        module_path,
        submodule_search_locations=submodule_search_locations,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load add-on package module {module_name!r} from {module_path}")
    module = util.module_from_spec(spec)
    alias_root = module_name.split(".", 1)[0]
    alias_snapshot = _snapshot_existing_declared_aliases(alias_root)
    try:
        _clear_addon_package_import(alias_root)
        sys.modules[loaded_module_name] = module
        _install_addon_package_declared_aliases(
            root_module_name=root_module_name,
            module_name=module_name,
            module=module,
        )
        spec.loader.exec_module(module)
        return _load_registration_from_module_object(module)
    finally:
        _restore_existing_declared_aliases(alias_root, alias_snapshot)


def _load_existing_package_module_registration(
    *, module_name: str, module_path: Path
) -> tuple[bool, Any]:
    module = sys.modules.get(module_name)
    if module is None or not isinstance(module, types.ModuleType):
        return False, None
    if not _module_file_matches(module, module_path):
        return False, None
    return True, _load_registration_from_module_object(module)


def _module_file_matches(module: types.ModuleType, expected_path: Path) -> bool:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return False
    try:
        return Path(module_file).resolve(strict=False) == expected_path.resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def _snapshot_existing_declared_aliases(
    alias_root: str,
) -> dict[str, types.ModuleType] | None:
    if alias_root not in sys.modules:
        return None
    prefix = f"{alias_root}."
    return {
        module_name: module
        for module_name, module in sys.modules.items()
        if module_name == alias_root or module_name.startswith(prefix)
        if isinstance(module, types.ModuleType)
    }


def _restore_existing_declared_aliases(
    alias_root: str, snapshot: dict[str, types.ModuleType] | None
) -> None:
    if snapshot is None:
        return
    prefix = f"{alias_root}."
    for module_name in list(sys.modules):
        if module_name == alias_root or module_name.startswith(prefix):
            sys.modules.pop(module_name, None)
    sys.modules.update(snapshot)


def _addon_package_module_location(
    *,
    package_dir: Path,
    module_name: str,
    manifest_path: Path,
) -> tuple[Path, list[str] | None]:
    parts = module_name.split(".")
    if not parts or any(not part.isidentifier() for part in parts):
        raise ValueError(f"{manifest_path} declares invalid module name {module_name!r}")
    if len(parts) == 1 and parts[0] == package_dir.name:
        package_init = package_dir / "__init__.py"
        if package_init.is_file():
            return package_init, [str(package_dir)]
    module_path = package_dir.joinpath(*parts)
    package_init = module_path / "__init__.py"
    if package_init.is_file():
        return package_init, [str(module_path)]
    module_file = module_path.with_suffix(".py")
    if module_file.is_file():
        return module_file, None
    raise ValueError(
        f"{manifest_path} declares module {module_name!r}, but it was not found under {package_dir}"
    )


def _addon_package_import_root(package_dir: Path) -> str:
    digest = hashlib.sha256(str(package_dir.resolve(strict=False)).encode("utf-8")).hexdigest()[:16]
    return f"_evander_addon_package_{digest}"


def _clear_addon_package_import(root_module_name: str) -> None:
    prefix = f"{root_module_name}."
    for module_name in list(sys.modules):
        if module_name == root_module_name or module_name.startswith(prefix):
            sys.modules.pop(module_name, None)


def _install_addon_package_parent_modules(
    root_module_name: str, package_dir: Path, module_name: str
) -> None:
    root_module = types.ModuleType(root_module_name)
    root_module.__path__ = [str(package_dir)]
    root_module.__package__ = root_module_name
    sys.modules[root_module_name] = root_module

    current_path = package_dir
    current_name = root_module_name
    for part in module_name.split(".")[:-1]:
        current_path = current_path / part
        current_name = f"{current_name}.{part}"
        parent_module = types.ModuleType(current_name)
        parent_module.__path__ = [str(current_path)]
        parent_module.__package__ = current_name
        sys.modules[current_name] = parent_module


def _install_addon_package_declared_aliases(
    *, root_module_name: str, module_name: str, module: types.ModuleType
) -> None:
    declared_name = ""
    synthetic_name = root_module_name
    parts = module_name.split(".")
    for part in parts[:-1]:
        declared_name = f"{declared_name}.{part}" if declared_name else part
        synthetic_name = f"{synthetic_name}.{part}"
        synthetic_parent = sys.modules.get(synthetic_name)
        if synthetic_parent is not None:
            sys.modules[declared_name] = synthetic_parent
    sys.modules[module_name] = module


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
            mcp_tools=value.get("mcp_tools"),
            mcp_tool_catalog=value.get("mcp_tool_catalog") or (),
            workflow_providers=value.get("workflow_providers"),
            connector_drivers=value.get("connector_drivers") or {},
            website_session_adapters=value.get("website_session_adapters") or {},
            security_scan_providers=value.get("security_scan_providers") or {},
            integration=value.get("integration"),
            requires_gateway_restart=value.get("requires_gateway_restart", False),
            required_restart_services=value.get("required_restart_services", ()),
            metadata=_coerce_metadata(value.get("metadata"), source=source),
        )

    addon_id = getattr(value, "addon_id", "")
    return AddonRegistration(
        addon_id=str(addon_id or ""),
        display_name=str(getattr(value, "display_name", addon_id) or ""),
        admin_navigation=_coerce_navigation(getattr(value, "admin_navigation", None)),
        admin_api_routes=getattr(value, "admin_api_routes", ()),
        admin_ui_routes=getattr(value, "admin_ui_routes", ()),
        mcp_tools=getattr(value, "mcp_tools", None),
        mcp_tool_catalog=getattr(value, "mcp_tool_catalog", ()),
        workflow_providers=getattr(value, "workflow_providers", None),
        connector_drivers=getattr(value, "connector_drivers", {}),
        website_session_adapters=getattr(value, "website_session_adapters", {}),
        security_scan_providers=getattr(value, "security_scan_providers", {}),
        integration=getattr(value, "integration", None),
        requires_gateway_restart=getattr(value, "requires_gateway_restart", False),
        required_restart_services=getattr(value, "required_restart_services", ()),
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
