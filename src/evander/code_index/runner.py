"""Deterministic index producer. Run only in an isolated indexing checkout."""

from __future__ import annotations

import argparse
import base64
import json
import hashlib
import os
import re
from pathlib import Path
import subprocess
import time

from .models import IndexArtifact, IndexSettings, SourceDocument, TOOLCHAIN, commit_id, source_path
from .scip import read_scip, portable_scip


def run(
    command: list[str],
    *,
    cwd: Path,
    log: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> None:
    with log.open("ab") as stream:
        subprocess.run(
            command,
            cwd=cwd,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=True,
            env=env,
        )


def dotnet_index_environment() -> dict[str, str]:
    """Return MSBuild settings that keep repository transport out of code analysis."""
    env = dict(os.environ)
    # SourceLink metadata is irrelevant to SCIP and rejects Evander's intentionally
    # private managed-Git transport URL in some projects.
    env["EnableSourceLink"] = "false"
    # .NET 10's package-pruning advisory is reported as an MSBuildWorkspace failure
    # by the pinned SCIP indexer even when the project builds successfully.
    no_warn = [value.strip() for value in re.split(r"[;,]", env.get("NoWarn", "")) if value.strip()]
    if "NU1510" not in {value.upper() for value in no_warn}:
        no_warn.append("NU1510")
    env["NoWarn"] = ";".join(no_warn)
    return env


def tracked_files(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repo, check=True, capture_output=True, timeout=30
    )
    return [source_path(p.decode()) for p in result.stdout.split(b"\0") if p]


def npm_workspaces(package_root: Path, log: Path) -> list[Path]:
    # Ask the installed npm after restore: its actual dependency tree applies npm's
    # workspace glob/negation rules and excludes unrelated nested packages.
    with log.open("ab") as stream:
        raw = subprocess.check_output(
            ["npm", "query", ".workspace", "--json"],
            cwd=package_root,
            stderr=stream,
            timeout=30,
        )
    return [Path(package["path"]).resolve() for package in json.loads(raw)]


def build_artifact(
    repo: Path, sha: str, settings: IndexSettings, output: Path, *, lexical_only: bool = False
) -> IndexArtifact:
    repo = repo.resolve()
    sha = commit_id(sha)
    head = (
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, timeout=30).decode().strip()
    )
    if head != sha:
        subprocess.run(["git", "fetch", "origin", sha], cwd=repo, check=True, timeout=120)
        subprocess.run(["git", "checkout", "--detach", sha], cwd=repo, check=True, timeout=60)
    if subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=repo, timeout=30):
        raise ValueError("Indexing requires an unchanged checkout of the requested commit")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = tracked_files(repo)
    documents = []
    diagnostics = []
    for path in files:
        file = repo / path
        if (
            not settings.includes_file(path)
            or file.is_symlink()
            or not file.is_file()
            or not file.resolve().is_relative_to(repo)
        ):
            continue
        if file.stat().st_size > settings.max_file_bytes:
            diagnostics.append(f"Skipped oversized source: {path}")
            continue
        try:
            text = file.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            diagnostics.append(f"Skipped non-UTF-8 source: {path}")
            continue
        if "\x00" not in text:
            documents.append(SourceDocument(path=path, text=text, language=file.suffix.lstrip(".")))
    artifact = IndexArtifact(commit_sha=sha, documents=documents, diagnostics=diagnostics)
    if lexical_only:
        artifact.diagnostics.append("Lexical-only evaluation: compiler navigation was not run")
    else:
        projects = [
            ("dotnet", p)
            for p in (settings.dotnet_projects or [p for p in files if p.endswith(".csproj")])
        ]
        projects += [
            ("typescript", p)
            for p in (
                settings.typescript_projects
                or [p for p in files if Path(p).name.startswith("tsconfig") and p.endswith(".json")]
            )
        ]
        installed = {}
        for tool in ("scip-dotnet", "scip-typescript"):
            try:
                installed[tool] = (
                    subprocess.check_output(
                        [tool, "--version"], stderr=subprocess.STDOUT, timeout=15
                    )
                    .decode()
                    .strip()
                )
            except (OSError, subprocess.SubprocessError):
                installed[tool] = "unavailable"
        restored_npm = set()
        workspace_owners = {}
        seen_occurrences, seen_relationships, exported_paths = set(), set(), set()
        allowed_paths = {document.path for document in documents}
        for number, (language, project) in enumerate(projects):
            log = output.parent / f"index-project-{number}.log"
            log.write_bytes(b"")
            scip_file = output.parent / f"index-project-{number}.scip"
            started = time.monotonic()
            row = {"path": project, "language": language, "status": "failed"}
            try:
                source_path(project)
                if project not in files:
                    raise ValueError("Project is not tracked")
                tool = "scip-dotnet" if language == "dotnet" else "scip-typescript"
                if TOOLCHAIN[tool] not in installed[tool]:
                    raise ValueError("Pinned indexer version is not installed")
                if language == "dotnet":
                    dotnet_env = dotnet_index_environment()
                    # SCIP restores its working directory, not its positional project.
                    # Restore explicitly and check the exit status before navigation.
                    run(
                        ["dotnet", "restore", project, "-p:EnableWindowsTargeting=true"],
                        cwd=repo,
                        log=log,
                        timeout=300,
                        env=dotnet_env,
                    )
                    try:
                        run(
                            [
                                "dotnet",
                                "build",
                                project,
                                "--no-restore",
                                "-p:EnableWindowsTargeting=true",
                                "--verbosity",
                                "quiet",
                            ],
                            cwd=repo,
                            log=log,
                            timeout=settings.timeout_seconds,
                            env=dotnet_env,
                        )
                    except subprocess.CalledProcessError:
                        row["compilation"] = "failed"
                    command = [
                        tool,
                        "index",
                        project,
                        "--output",
                        str(scip_file),
                        "--working-directory",
                        str(repo),
                        "--skip-dotnet-restore",
                        "--allow-global-symbol-definitions",
                    ]
                else:
                    # Restore at the lockfile root, respecting the worker's npm cache/relay.
                    parents = [p for p in (repo / project).parents if p.is_relative_to(repo)]
                    # Monorepos can have dependencies in more than one ancestor.
                    roots = [p for p in reversed(parents) if (p / "package.json").is_file()]
                    for package_root in roots:
                        package_root = workspace_owners.get(package_root, package_root)
                        package = json.loads((package_root / "package.json").read_text())
                        if not any(
                            package.get(key)
                            for key in ("dependencies", "devDependencies", "workspaces")
                        ):
                            continue
                        lock_file = package_root / "package-lock.json"
                        if package_root not in restored_npm:
                            if not lock_file.is_file() and settings.npm_restore == "locked":
                                raise ValueError(
                                    "Tracked package lock is required; select install to resolve dependencies"
                                )
                            verb = "ci" if lock_file.is_file() else "install"
                            run(
                                ["npm", verb, "--ignore-scripts", "--no-audit", "--no-fund"],
                                cwd=package_root,
                                log=log,
                                timeout=300,
                            )
                            if package.get("workspaces"):
                                workspace_owners.update(
                                    (member, package_root)
                                    for member in npm_workspaces(package_root, log)
                                    if member.is_relative_to(package_root)
                                )
                            restored_npm.add(package_root)
                        if lock_file.is_file():
                            row[
                                "dependency_lock_sha256:"
                                + package_root.relative_to(repo).as_posix()
                            ] = hashlib.sha256(lock_file.read_bytes()).hexdigest()
                            if lock_file.relative_to(repo).as_posix() not in files:
                                artifact.diagnostics.append(
                                    f"Resolved untracked npm lock: {lock_file.relative_to(repo)}; retain the worker lock to reproduce dependencies"
                                )
                    command = [tool, "index", project, "--output", str(scip_file)]
                run(
                    command,
                    cwd=repo,
                    log=log,
                    timeout=settings.timeout_seconds,
                    env=dotnet_env if language == "dotnet" else None,
                )
                data = scip_file.read_bytes()
                occurrences, relationships, paths = read_scip(data)
                if not paths:
                    raise ValueError("Indexer returned no source documents")
                for item in occurrences:
                    key = tuple(item.model_dump().values())
                    if item.path in allowed_paths and key not in seen_occurrences:
                        artifact.occurrences.append(item)
                        seen_occurrences.add(key)
                for item in relationships:
                    key = (item.symbol, item.target, item.kind)
                    if key not in seen_relationships:
                        artifact.relationships.append(item)
                        seen_relationships.add(key)
                # Keep one original SCIP document per source path in the portable copy.
                # The merged navigation table above retains all compilation contexts.
                new_paths = (paths & allowed_paths) - exported_paths
                if new_paths:
                    artifact.scip_files[scip_file.name] = base64.b64encode(
                        portable_scip(data, new_paths)
                    ).decode()
                    exported_paths.update(new_paths)
                workspace_failed = language == "dotnet" and bool(
                    re.search(
                        r"(?:fail: Microsoft.CodeAnalysis|Failure:)",
                        log.read_text(errors="replace"),
                    )
                )
                row.update(
                    status="partial"
                    if workspace_failed or row.get("compilation") == "failed"
                    else "ready",
                    documents=len(paths),
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                row["error"] = type(exc).__name__
                artifact.diagnostics.append(
                    f"Navigation failed for {project}: {type(exc).__name__}; artifact {log.name}"
                )
            row["duration_ms"] = round((time.monotonic() - started) * 1000)
            artifact.projects.append(row)
    data = artifact.model_dump_json()
    temporary = output.with_suffix(".tmp")
    temporary.write_text(data)
    os.replace(temporary, output)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args()
    settings = IndexSettings.model_validate_json(args.settings.read_text())
    artifact = build_artifact(
        args.repo, args.commit, settings, args.output, lexical_only=args.lexical_only
    )
    print(
        json.dumps(
            {
                "documents": len(artifact.documents),
                "occurrences": len(artifact.occurrences),
                "projects": artifact.projects,
                "diagnostics": artifact.diagnostics,
            }
        )
    )


if __name__ == "__main__":
    main()
