from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


TEAM_RUNTIME_COMPONENTS = (
    "gateway",
    "admin-web",
    "trigger-orchestrator",
    "effect-orchestrator",
)
_GENERATION_ENV = "EVANDER_RUNTIME_GENERATION"
_SYSTEMD_INVOCATION_ENV = "INVOCATION_ID"
_IDENTITY_PATHS = ("src", "frontend/admin-web/dist", "pyproject.toml")
_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


@dataclass(frozen=True, slots=True)
class RuntimeGenerationIdentity:
    generation: str
    version: str
    commit_sha: str | None
    dirty_fingerprint: str | None
    source: str


@dataclass(frozen=True, slots=True)
class RuntimeGenerationRegistration:
    component: str
    generation: str
    version: str
    commit_sha: str | None
    dirty_fingerprint: str | None
    source: str
    boot_id: str | None
    invocation_id: str | None
    registered_at: str


def _run_git(project_dir: Path, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(project_dir), *args],
        capture_output=True,
        check=False,
    )


def _package_version() -> str:
    try:
        return importlib.metadata.version("evander").strip() or "unknown"
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _current_boot_id() -> str | None:
    try:
        return _BOOT_ID_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _dirty_fingerprint(project_dir: Path) -> str | None:
    diff = _run_git(
        project_dir,
        ["diff", "--no-ext-diff", "--binary", "HEAD", "--", *_IDENTITY_PATHS],
    )
    if diff.returncode != 0:
        error = diff.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "cannot identify the runtime source generation because git diff failed"
            + (f": {error}" if error else "")
        )
    untracked = _run_git(
        project_dir,
        ["ls-files", "--others", "--exclude-standard", "--", *_IDENTITY_PATHS],
    )
    if untracked.returncode != 0:
        error = untracked.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            "cannot identify the runtime source generation because git ls-files failed"
            + (f": {error}" if error else "")
        )

    digest = hashlib.sha256()
    digest.update(diff.stdout)
    untracked_paths = sorted(
        path.strip()
        for path in untracked.stdout.decode("utf-8", errors="surrogateescape").splitlines()
        if path.strip()
    )
    for relative_path in untracked_paths:
        digest.update(b"\0untracked\0")
        digest.update(relative_path.encode("utf-8", errors="surrogateescape"))
        try:
            digest.update((project_dir / relative_path).read_bytes())
        except OSError:
            digest.update(b"\0unreadable\0")
    if not diff.stdout and not untracked_paths:
        return None
    return digest.hexdigest()


def resolve_runtime_generation(
    project_dir: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> RuntimeGenerationIdentity:
    runtime_env = os.environ if env is None else env
    version = _package_version()
    configured = str(runtime_env.get(_GENERATION_ENV) or "").strip()
    commit = _run_git(project_dir, ["rev-parse", "HEAD"])
    commit_sha = commit.stdout.decode("ascii", errors="ignore").strip() if commit.returncode == 0 else ""
    normalized_commit = commit_sha or None
    dirty_fingerprint = _dirty_fingerprint(project_dir) if normalized_commit is not None else None
    material = "\0".join((version, normalized_commit or "no-git", dirty_fingerprint or "clean"))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    generation = f"{configured}:{digest}" if configured else digest
    identity_source = "git-and-source-tree" if normalized_commit is not None else "package-version"
    return RuntimeGenerationIdentity(
        generation=generation,
        version=version,
        commit_sha=normalized_commit,
        dirty_fingerprint=dirty_fingerprint,
        source=(
            f"env-label:{_GENERATION_ENV}+{identity_source}"
            if configured
            else identity_source
        ),
    )


def _registration_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "runtime-generations"


def register_runtime_generation(
    *,
    project_dir: Path,
    runtime_dir: Path,
    component: str,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> RuntimeGenerationRegistration:
    normalized_component = component.strip()
    if normalized_component not in TEAM_RUNTIME_COMPONENTS:
        raise ValueError(
            f"component must be one of: {', '.join(TEAM_RUNTIME_COMPONENTS)}"
        )
    identity = resolve_runtime_generation(project_dir, env=env)
    record = RuntimeGenerationRegistration(
        component=normalized_component,
        generation=identity.generation,
        version=identity.version,
        commit_sha=identity.commit_sha,
        dirty_fingerprint=identity.dirty_fingerprint,
        source=identity.source,
        boot_id=_current_boot_id(),
        invocation_id=str((os.environ if env is None else env).get(_SYSTEMD_INVOCATION_ENV) or "").strip()
        or None,
        registered_at=(now or datetime.now(UTC)).astimezone(UTC).isoformat(),
    )
    target_dir = _registration_dir(runtime_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{normalized_component}.json"
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=target_dir,
        prefix=f".{normalized_component}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(asdict(record), handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return record


def read_runtime_generation_status(
    runtime_dir: Path,
    *,
    components: Sequence[str] = TEAM_RUNTIME_COMPONENTS,
) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    current_boot_id = _current_boot_id()
    for component in components:
        path = _registration_dir(runtime_dir) / f"{component}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors[component] = str(exc)
            continue
        if not isinstance(payload, dict) or not str(payload.get("generation") or "").strip():
            errors[component] = "registration is missing a generation"
            continue
        recorded_boot_id = str(payload.get("boot_id") or "").strip() or None
        if current_boot_id is not None and recorded_boot_id != current_boot_id:
            errors[component] = (
                "registration is from an unknown or previous host boot; restart the component"
            )
            continue
        records[component] = payload

    missing = [component for component in components if component not in records]
    generations = sorted({str(record["generation"]) for record in records.values()})
    if not records and not errors:
        state = "not_registered"
    elif errors or missing:
        state = "incomplete"
    elif len(generations) != 1:
        state = "split"
    else:
        state = "coherent"
    return {
        "state": state,
        "coherent": state == "coherent",
        "components": records,
        "missing_components": missing,
        "errors": errors,
        "generations": generations,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register or inspect Evander runtime generations.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--project-dir", type=Path, required=True)
    register.add_argument("--runtime-dir", type=Path, required=True)
    register.add_argument("--component", choices=TEAM_RUNTIME_COMPONENTS, required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--runtime-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "register":
        payload: object = asdict(
            register_runtime_generation(
                project_dir=args.project_dir,
                runtime_dir=args.runtime_dir,
                component=args.component,
            )
        )
    else:
        payload = read_runtime_generation_status(args.runtime_dir)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
