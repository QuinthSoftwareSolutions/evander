"""Retained verifier logs, addressed by worker job ID after worker cleanup."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time


def redact_verifier_text(text: str) -> str:
    # Verifiers have no host credentials. Also redact conventional credential
    # formats and any secret values present in the host process environment.
    for key, value in os.environ.items():
        if len(value) >= 8 and re.search(r"token|secret|password|api_key", key, re.I):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(
        r"""(?i)((?:password|token|api[_-]?key|secret)["']?\s*[=:]\s*["']?)[^\s,;"']+""",
        r"\1[redacted]",
        text,
    )
    return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)


def retain_verification_log(
    runtime_dir: Path, *, job_id: str, text: str, retention_days: int
) -> str:
    root = runtime_dir / "release-verification-logs"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = hashlib.sha256(job_id.encode()).hexdigest()
    # Bound storage without implying that a bounded log is complete.
    text = bound_log_text(text, limit=10 * 1024 * 1024)
    payload = {
        "job_id": job_id,
        "expires_at": time.time() + retention_days * 86400,
        "text": redact_verifier_text(text),
    }
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(gzip.compress(json.dumps(payload).encode()))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / f"{key}.json.gz")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return key


def bound_log_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n[retained log truncated]\n" + text[-limit // 2 :]


def read_verification_log(runtime_dir: Path, job_id: str) -> str | None:
    key = hashlib.sha256(job_id.encode()).hexdigest()
    path = runtime_dir / "release-verification-logs" / f"{key}.json.gz"
    if not path.is_file():
        return None
    try:
        payload = json.loads(gzip.decompress(path.read_bytes()))
        if payload["job_id"] != job_id or payload["expires_at"] < time.time():
            return None
        return payload["text"]
    except (OSError, ValueError, KeyError, EOFError, TypeError):
        return None


def prune_verification_logs(runtime_dir: Path) -> int:
    removed = 0
    for path in (runtime_dir / "release-verification-logs").glob("*.json.gz"):
        try:
            payload = json.loads(gzip.decompress(path.read_bytes()))
            if payload["expires_at"] < time.time():
                path.unlink()
                removed += 1
        except (OSError, ValueError, KeyError):
            continue
    return removed
