#!/usr/bin/env python3
"""One-time, preview/apply migration. Runtime loaders never import this script."""

from __future__ import annotations

import argparse
from contextlib import closing, nullcontext
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

from evander.admin.composition import admin_config_mutation_file_lock
from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import DirectoryConfigStore
from evander.host_config import HostConfig
from evander.repositories.directories import ReadOnlyJsonDirectoryRepository


TTS = {"tts-1", "tts-1-hd", "gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-03-20",
       "gpt-4o-mini-tts-2025-12-15"}
TRANSCRIPTION = {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe",
                 "gpt-4o-mini-transcribe-2025-03-20", "gpt-4o-mini-transcribe-2025-12-15",
                 "gpt-4o-transcribe-diarize"}
REALTIME = {
    model: target
    for models, target in (
        (("gpt-realtime", "gpt-realtime-2025-08-28", "gpt-4o-realtime",
          "gpt-4o-realtime-preview", "gpt-4o-realtime-preview-2024-10-01",
          "gpt-4o-realtime-preview-2024-12-17", "gpt-4o-realtime-preview-2025-06-03"),
         "gpt-realtime-2.1"),
        (("gpt-realtime-mini", "gpt-realtime-mini-2025-10-06", "gpt-realtime-mini-2025-12-15",
          "gpt-4o-mini-realtime", "gpt-4o-mini-realtime-preview",
          "gpt-4o-mini-realtime-preview-2024-12-17"), "gpt-realtime-2.1-mini"),
    ) for model in models
}
BASE = {
    "openai_realtime_api_base_url": "https://api.openai.com/v1",
    "openai_realtime_model": "gpt-realtime-2",
    "openai_realtime_transcription_model": "gpt-4o-transcribe",
}
ENV = re.compile(r"^(\s*(?:export\s+)?)(OPENAI_REALTIME_[A-Z_]+)(\s*=\s*)([^\r\n]*)(\r?\n?)$", re.M)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def official(url):
    parts = urlsplit(url)
    return (parts.scheme in {"https", "wss"} and parts.netloc == "api.openai.com"
            and parts.path.rstrip("/") in {"/v1", "/v1/realtime"}
            and not parts.query and not parts.fragment)


def merge(left, right):
    result = deepcopy(left)
    for key, value in right.items():
        result[key] = merge(result.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return result


def effective(records, directory, domain, *, new_defaults=False):
    result = dict(BASE) if domain == "providers" else {}
    if new_defaults and domain == "providers":
        result["openai_realtime_transcription_model"] = "gpt-live-transcribe"
    parts = directory.split("/")
    for size in range(1, len(parts) + 1):
        result = merge(result, records.get(("/".join(parts[:size]), domain)) or {})
    return result


def speech(config, *, browser=False):
    result = deepcopy(config)
    url_key, model_key = ("api_base_url", "speech_model") if browser else ("base_url", "model")
    model = result.get(model_key, "gpt-4o-mini-tts")
    if official(result.get(url_key, "https://api.openai.com/v1")) and model in TTS:
        result.update({model_key: "gpt-realtime-2.1-mini", "speech_backend": "realtime"})
        if result.get("voice", "onyx") in {"onyx", "nova", "fable"}:
            result["voice"] = "cedar"
        if result.get("speed", 1.0) > 1.5:
            result["speed"] = 1.5
    else:
        native = official(result.get(url_key, "https://api.openai.com/v1")) and model in {
            "gpt-realtime-2", "gpt-realtime-2.1", "gpt-realtime-2.1-mini"
        }
        result.setdefault("speech_backend", "realtime" if native else "speech_api")
    if not browser and (model in TTS or "speech_backend" not in config):
        result.setdefault("speed", 1.0)
    if browser and official(result.get(url_key, "https://api.openai.com/v1")):
        if result.get("transcription_model") in TRANSCRIPTION:
            result["transcription_model"] = "gpt-transcribe"
    return result


def changes(before, after, path=()):
    result = []
    for key, value in after.items():
        old = before.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            result.extend(changes(old, value, (*path, key)))
        elif old != value or key not in before:
            # All changes are model/backend/voice/speed fields, never credentials.
            if isinstance(value, dict):
                result.extend(changes({}, value, (*path, key)))
            else:
                result.append({"field": list((*path, key)), "before": old, "after": value})
    return result


def environment(content):
    values = {}
    for match in ENV.finditer(content):
        key = match[2]
        if key not in {"OPENAI_REALTIME_MODEL", "OPENAI_REALTIME_TRANSCRIPTION_MODEL",
                       "OPENAI_REALTIME_API_BASE_URL", "OPENAI_REALTIME_BASE_URL"}:
            continue
        if key in values:
            raise ValueError("Duplicate Realtime environment setting")
        tokens = shlex.split(match[4], comments=True)
        if len(tokens) > 1:
            raise ValueError("Realtime environment settings must contain literal values")
        values[key] = tokens[0] if tokens else ""
    urls = [v for k, v in values.items() if k.endswith("BASE_URL") and v]
    edits = {}
    if all(official(url) for url in urls):
        if values.get("OPENAI_REALTIME_MODEL") in REALTIME:
            edits["OPENAI_REALTIME_MODEL"] = REALTIME[values["OPENAI_REALTIME_MODEL"]]
        if values.get("OPENAI_REALTIME_TRANSCRIPTION_MODEL") in TRANSCRIPTION:
            edits["OPENAI_REALTIME_TRANSCRIPTION_MODEL"] = "gpt-live-transcribe"

    def rewrite(match):
        if match[2] not in edits:
            return match[0]
        original = match[4].strip()
        quote = original[0] if original.startswith(("'", '"')) else ""
        comment = re.search(r"\s+#.*$", match[4])
        return (match[1] + match[2] + match[3] + quote + edits[match[2]] + quote
                + (comment[0] if comment else "") + match[5])

    return ENV.sub(rewrite, content), changes(values, {**values, **edits})


def browser_rows(runtime):
    path = runtime / "addons/browser-sessions-addon/state.db"
    if not path.exists():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return [(directory, revision, json.loads(payload)) for directory, revision, payload
                in db.execute("SELECT directory,revision,payload FROM settings ORDER BY directory")]


def inventory(runtime_dir=None, env_files=()):
    """Return a metadata-only public plan and private validated replacement payloads."""
    runtime = Path(runtime_dir).resolve() if runtime_dir is not None else None
    entries, candidates = [], []
    registry_hash = None
    host_hash = None
    if runtime is not None:
        host_bytes = (runtime / "host-config.json").read_bytes()
        host_hash = sha256(host_bytes).hexdigest()
        host = HostConfig.model_validate(json.loads(host_bytes))
        if host.runtime_dir != runtime:
            raise ValueError("Host configuration runtime_dir does not match the selected runtime")
        registry_hash = sha256(host.directories_store_path.read_bytes()).hexdigest()
        directory_store = DirectoryStore(host.directories_store_path, repository=
            ReadOnlyJsonDirectoryRepository(host.directories_store_path))
        directory_service = DirectoryService(directory_store)
        config_store = DirectoryConfigStore(directory_store, runtime_dir=runtime)
        directories = [directory_service.show("root").directory,
                       *directory_service.list_descendants("root")]
        directories.sort(key=lambda item: (item.path.count("/"), item.path))
        original = {(d.path, domain): config_store.read_for_directory(d, domain=domain)
                    for d in directories for domain in ("providers", "connectors")}
        updated = deepcopy(original)
        for directory in directories:
            path = directory.path
            key = (path, "providers")
            before = effective(original, path, "providers")
            after = effective(updated, path, "providers", new_defaults=True)
            local = deepcopy(updated[key] or {})
            if official(before["openai_realtime_api_base_url"]):
                desired_model = REALTIME.get(before["openai_realtime_model"], before["openai_realtime_model"])
                desired_transcription = before["openai_realtime_transcription_model"]
                if desired_transcription in TRANSCRIPTION:
                    desired_transcription = "gpt-live-transcribe"
                for field, desired in (("openai_realtime_model", desired_model),
                                       ("openai_realtime_transcription_model", desired_transcription)):
                    if after.get(field) != desired:
                        local[field] = desired
            else:
                # A custom child endpoint must not inherit a changed public model.
                for field in ("openai_realtime_model", "openai_realtime_transcription_model"):
                    if before.get(field) != after.get(field):
                        local[field] = before.get(field)
            updated[key] = local or original[key]
            key = (path, "connectors")
            before = effective(original, path, "connectors").get("connectors", {})
            after = effective(updated, path, "connectors").get("connectors", {})
            for connector_id, connector in before.items():
                if connector.get("driver") != "demo_video_narration":
                    continue
                desired = speech(connector.get("config", {}))
                current = after.get(connector_id, {}).get("config", {})
                delta = {k: v for k, v in desired.items() if k not in current or current[k] != v}
                if delta:
                    local = deepcopy(updated[key] or {})
                    local.setdefault("connectors", {}).setdefault(connector_id, {}).setdefault(
                        "config", {}).update(delta)
                    updated[key] = local
        for key, raw in original.items():
            candidate = updated[key]
            delta = changes(raw or {}, candidate or {})
            entry = {"kind": "directory", "directory": key[0], "domain": key[1],
                     "revision": digest(raw), "changes": delta}
            entries.append(entry)
            if delta:
                DirectoryConfigStore.validate_domain_payload(key[1], candidate)
                if key[1] == "connectors":
                    from evander_demo_video_addon.narration import SpeechSettings
                    for connector in effective(updated, key[0], "connectors").get("connectors", {}).values():
                        if connector.get("driver") == "demo_video_narration":
                            SpeechSettings.model_validate(connector["config"])
                candidates.append((entry, raw, candidate))
        for directory, revision, raw in browser_rows(runtime):
            candidate = deepcopy(raw)
            if isinstance(raw.get("voice"), dict):
                candidate["voice"] = speech(raw["voice"], browser=True)
            delta = changes(raw, candidate)
            entry = {"kind": "browser", "directory": directory, "revision": revision,
                     "source_hash": digest(raw), "changes": delta}
            entries.append(entry)
            if delta:
                from evander_browser_sessions_addon.models import Settings
                Settings.model_validate(candidate)
                candidates.append((entry, raw, candidate))
    for env_file in sorted({Path(p).resolve() for p in env_files}):
        raw = env_file.read_bytes()
        replacement, delta = environment(raw.decode())
        entry = {"kind": "environment", "path": str(env_file),
                 "revision": sha256(raw).hexdigest(), "changes": delta}
        entries.append(entry)
        if delta:
            candidates.append((entry, raw, replacement.encode()))
    # Preserve custom child selections before changing their ancestors. This
    # order also makes fresh previews safe after a partially completed apply.
    def order(entry):
        return ({"directory": 0, "browser": 1, "environment": 2}[entry["kind"]],
                -entry.get("directory", "").count("/"), entry.get("directory", ""),
                entry.get("domain", ""), entry.get("path", ""))
    entries.sort(key=order)
    candidates.sort(key=lambda item: order(item[0]))
    plan = {"schema": 1, "runtime_dir": str(runtime) if runtime else None,
            "registry_hash": registry_hash, "host_hash": host_hash, "entries": entries}
    return plan, candidates


def private_write(path, content, *, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def apply(plan, *, backup_dir, maintenance=False):
    if not maintenance:
        raise ValueError("Apply requires --maintenance with configuration writers quiesced")
    backup_dir = Path(backup_dir).resolve()
    if any((parent / ".git").is_file() or (parent / ".git/HEAD").is_file()
           for parent in (backup_dir, *backup_dir.parents)):
        raise ValueError("Recovery copies must be outside Git checkouts")
    runtime = Path(plan["runtime_dir"]) if plan["runtime_dir"] else None
    lock = admin_config_mutation_file_lock(runtime / "admin-config-mutations.lock") if runtime else nullcontext()
    completed = []
    with lock:
        current, candidates = inventory(runtime, [e["path"] for e in plan["entries"] if e["kind"] == "environment"])
        if not candidates:
            return {"applied": [], "already_migrated": True}
        if current != plan:
            raise ValueError("Migration sources changed; generate a fresh preview before applying")
        recovery = backup_dir / ("openai-deprecations-" + uuid4().hex)
        recovery.mkdir(parents=True, mode=0o700)
        # Write every recovery copy before the first configuration change.
        for index, (entry, raw, _) in enumerate(candidates):
            content = raw if isinstance(raw, bytes) else json.dumps(raw, indent=2).encode()
            private_write(recovery / f"{index}.backup", content)
        private_write(recovery / "plan.json", json.dumps(plan, indent=2).encode())
        try:
            for entry, _, candidate in candidates:
                if entry["kind"] == "directory":
                    host = HostConfig.model_validate(json.loads((runtime / "host-config.json").read_text()))
                    store = DirectoryConfigStore(DirectoryStore(host.directories_store_path), runtime_dir=runtime)
                    if digest(store.read(entry["directory"], domain=entry["domain"])) != entry["revision"]:
                        raise ValueError("Directory configuration changed before migration")
                    store.write(entry["directory"], domain=entry["domain"], payload=candidate)
                elif entry["kind"] == "browser":
                    from evander_browser_sessions_addon.models import Settings
                    from evander_browser_sessions_addon.state import Store
                    Store(runtime).save_settings(entry["directory"], entry["revision"], Settings.model_validate(candidate))
                else:
                    path = Path(entry["path"])
                    if sha256(path.read_bytes()).hexdigest() != entry["revision"]:
                        raise ValueError("Bridge environment changed before migration")
                    fd, temporary = tempfile.mkstemp(prefix=".openai-migration-", dir=path.parent)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(candidate)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.chmod(temporary, path.stat().st_mode & 0o777)
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                completed.append({k: v for k, v in entry.items() if k in {"kind", "directory", "domain", "path"}})
        except Exception:
            # No blind rollback across independently revisioned stores.
            raise RuntimeError(json.dumps({"message": "Migration stopped; generate a fresh preview", "completed": completed,
                                           "recovery_dir": str(recovery)})) from None
    return {"applied": completed, "recovery_dir": str(recovery)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.apply:
            if args.plan is None or args.backup_dir is None:
                raise ValueError("--apply requires --plan and an external --backup-dir")
            if args.runtime_dir is not None or args.env_file:
                raise ValueError("Apply uses only the targets in --plan")
            result = apply(json.loads(args.plan.read_text()), backup_dir=args.backup_dir, maintenance=args.maintenance)
        else:
            if args.runtime_dir is None and not args.env_file:
                raise ValueError("Select --runtime-dir or --env-file")
            result = inventory(args.runtime_dir, args.env_file)[0]
        print(json.dumps(result, indent=2))
    except RuntimeError as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception:
        parser.exit(1, "Migration failed; check selected sources, installed add-ons and source revisions. No credentials were printed.\n")


if __name__ == "__main__":
    main()
