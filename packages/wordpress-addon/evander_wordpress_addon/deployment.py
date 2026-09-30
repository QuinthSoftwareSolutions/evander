"""External recovery stays usable when WordPress itself cannot boot."""

from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

from evander.credentials.store import protected_read

from .models import Conflict, Pipeline, UnknownOutcome, WordPressError
from .transport import Client


class PipelineAdapter:
    def __init__(self, client, prefix="/evander-releases/v1"):
        self.client, self.prefix = client, prefix

    async def call(self, action, payload):
        result = await self.client.call(
            self.prefix + "/" + action, payload, mutation=action in {"deploy", "restore"}
        )
        if result.get("protocol_version") != 1:
            raise WordPressError("Deployment pipeline protocol is incompatible")
        if result.get("conflict"):
            raise Conflict("Hosting resource or release changed")
        if result.get("error"):
            raise WordPressError("Deployment pipeline could not complete the operation")
        return result


class SSHAdapter:
    def __init__(
        self, profile_id, directory, site_id, origin, *, profile_root="/etc/evander/wordpress-ssh"
    ):
        path = Path(profile_root) / (profile_id + ".json")
        config = json.loads(protected_read(path))
        info = path.stat()
        if info.st_uid not in {0, os.getuid()}:
            raise WordPressError("SSH profile is not operator-owned")
        if (
            config.get("directory") != directory
            or config.get("site_id") != site_id
            or config.get("origin") != origin
        ):
            raise WordPressError("SSH profile scope does not match this website")
        if not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}", config.get("host", "")
        ) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", config.get("user", "")):
            raise WordPressError("Invalid SSH destination")
        if type(config.get("port", 22)) is not int or not 1 <= config.get("port", 22) <= 65535:
            raise WordPressError("Invalid SSH port")
        for key in ("key_file", "known_hosts_file"):
            if not Path(config[key]).is_absolute():
                raise WordPressError("SSH trust files require absolute operator paths")
            protected_read(Path(config[key]))
        self.config = config

    async def call(self, action, payload):
        config = self.config
        args = [
            "ssh",
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + config["known_hosts_file"],
            "-o",
            "ConnectTimeout=15",
            "-p",
            str(config.get("port", 22)),
            "-i",
            config["key_file"],
            "--",
            config["user"] + "@" + config["host"],
            "evander-wordpress-helper",
        ]
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(
                    json.dumps({"protocol_version": 1, "action": action, **payload}).encode()
                ),
                120,
            )
        except BaseException:
            process.kill()
            await process.wait()
            if action in {"deploy", "restore"}:
                raise UnknownOutcome("SSH operation needs status reconciliation") from None
            raise WordPressError("SSH helper is unavailable") from None
        if process.returncode or len(stdout) > 128 * 1024:
            if action in {"deploy", "restore"}:
                raise UnknownOutcome("SSH operation needs status reconciliation")
            raise WordPressError("SSH helper is unavailable")
        value = json.loads(stdout)
        if value.get("conflict"):
            raise Conflict("The hosting release changed")
        if value.get("error") or value.get("protocol_version") != 1:
            raise WordPressError("SSH helper rejected the operation")
        return value


class Deployments:
    def __init__(self, service):
        self.service = service

    async def pipeline(self, connector_id, prefix="/evander-releases/v1"):
        definition, _ = await self.service.host_auth(connector_id)
        if definition.driver != "rest":
            raise WordPressError("Build/deployment pipelines require a host REST connector")
        client = Client(
            definition.config["base_url"],
            lambda: self.service.headers(connector_id),
            auth_material=lambda: self.service.auth_material(connector_id),
            transport_factory=self.service.transport_factory,
            max_response_bytes=140 * 1024**2,
        )
        return PipelineAdapter(client, prefix)

    async def adapter(self, site_id, site):
        if isinstance(site.deployment, Pipeline):
            return await self.pipeline(site.deployment.connector_id, site.deployment.prefix)
        return SSHAdapter(site.deployment.profile_id, self.service.directory, site_id, site.origin)

    async def prepare(self, site_id, site, change, code):
        managed = self.service.host_service.host_services.get("managed_git")
        if not callable(managed):
            raise WordPressError("Managed Git host service is unavailable")
        managed = managed()
        spec = code.model_dump()
        if change.get("build_pending"):
            pinned = change["build_pending"]["pin"]
        else:
            managed.resolve_snapshot(site.source.managed_repo_id, self.service.directory, **spec)
            pinned = managed.pin_retained_snapshot(
                site.source.managed_repo_id,
                self.service.directory,
                binding_kind="wordpress",
                binding_id=change["change_id"],
                pin_ref="refs/evander/wordpress/" + change["change_id"],
                source_ref=code.source_ref,
                commit_sha=code.commit_sha,
            )
            # Retention must own the ref even when adapter setup or hosting fails.
            pending = {
                "building": True,
                "pin": pinned,
                "adapter": site.deployment.model_dump(),
                "commit_sha": code.commit_sha,
            }
            change.update(self.service.store.update(change, build_pending=pending))
        pending = change.get("build_pending")
        adapter = await self.adapter(site_id, site)
        if pending and pending.get("prepared"):
            prepared = pending["prepared"]
        else:
            prepared = await adapter.call(
                "prepare",
                {
                    "operation_id": change["change_id"],
                    "component": site.source.component,
                    "commit_sha": code.commit_sha,
                    "artifact_sha256": None,
                },
            )
            if not prepared.get("previous_release") or not prepared.get("expected_release"):
                raise WordPressError("Hosting recovery baseline is missing")
            pending = {
                "building": True,
                "pin": pinned,
                "prepared": prepared,
                "adapter": site.deployment.model_dump(),
                "commit_sha": code.commit_sha,
            }
            # Persist the original release before a slow build can race a manual
            # edit. Share the new ledger revision with the calling change engine.
            change.update(self.service.store.update(change, build_pending=pending))
        with tempfile.TemporaryDirectory(prefix="wordpress-source-") as temporary:
            archive = Path(temporary) / "source.zip"
            managed.export_pinned_source_archive(
                site.source.managed_repo_id,
                self.service.directory,
                source_ref=pinned["pin_ref"],
                commit_sha=code.commit_sha,
                tree_hash=code.tree_hash,
                archive_path=archive,
            )
            source = archive.read_bytes()
        if len(source) > 50 * 1024**2:
            raise WordPressError("Source archive exceeds the deployment limit")
        builder_id = site.source.build_connector_id or site.deployment.connector_id
        builder = (
            adapter
            if isinstance(site.deployment, Pipeline) and builder_id == site.deployment.connector_id
            else await self.pipeline(builder_id)
        )
        built = await builder.call(
            "prepare",
            {
                "operation_id": change["change_id"] + ":build",
                "commit_sha": code.commit_sha,
                "tree_hash": code.tree_hash,
                "source_sha256": sha256(source).hexdigest(),
                "source_archive": base64.b64encode(source).decode(),
                "source_path": site.source.source_path,
                "required_checks": site.source.required_checks,
                "component": site.source.component,
            },
        )
        if built.get("state") in {"building", "queued", "running"}:
            return pending
        if (
            built.get("commit_sha") != code.commit_sha
            or built.get("execution_backend") not in {"libvirt_vm", "container_worker_vm"}
            or any(
                built.get("checks", {}).get(key) is not True for key in site.source.required_checks
            )
        ):
            raise WordPressError(
                "The isolated build did not verify the pinned source and required checks"
            )
        archive = base64.b64decode(built["artifact_archive"], validate=True)
        if len(archive) > 100 * 1024**2 or sha256(archive).hexdigest() != built.get(
            "artifact_sha256"
        ):
            raise WordPressError("Build artifact digest does not match")
        artifact = self.service.store.artifact(archive, "zip")
        bound = await adapter.call(
            "prepare",
            {
                "operation_id": change["change_id"],
                "component": site.source.component,
                "artifact_sha256": built["artifact_sha256"],
                "commit_sha": code.commit_sha,
            },
        )
        if (
            bound.get("previous_release") != prepared["previous_release"]
            or bound.get("expected_release") != prepared["expected_release"]
        ):
            raise Conflict("Hosting changed its reserved recovery baseline")
        return {
            "adapter": site.deployment.model_dump(),
            "prepared": prepared,
            "artifact": artifact,
            "artifact_sha256": built["artifact_sha256"],
            "commit_sha": code.commit_sha,
            "pin": pinned,
            "checks": built["checks"],
        }

    async def apply(self, site_id, site, change, fence):
        release = change["deployment"]
        if site.deployment.model_dump() != release["adapter"]:
            raise Conflict("Deployment backend changed; the recorded backend must be reconciled")
        adapter = await self.adapter(site_id, site)
        status = await adapter.call(
            "status", {"operation_id": change["change_id"], "component": site.source.component}
        )
        if status.get("state") in {"deployed", "restored"}:
            return status
        if status.get("state") not in {"prepared", "not_found"}:
            raise UnknownOutcome("Hosting operation is still running or ambiguous")
        return await adapter.call(
            "deploy",
            {
                "operation_id": change["change_id"],
                "fence": fence,
                "component": site.source.component,
                "expected_release": release["prepared"]["expected_release"],
                "previous_release": release["prepared"]["previous_release"],
                "artifact_sha256": release["artifact_sha256"],
                "commit_sha": release["commit_sha"],
                "artifact_archive": base64.b64encode(
                    self.service.store.read_artifact(release["artifact"])
                ).decode(),
            },
        )

    async def restore(self, site_id, site, change, fence):
        release = change["deployment"]
        if site.deployment.model_dump() != release["adapter"]:
            raise Conflict("The recorded recovery backend is no longer configured")
        adapter = await self.adapter(site_id, site)
        status = await adapter.call(
            "status", {"operation_id": change["change_id"], "component": site.source.component}
        )
        if status.get("state") == "restored":
            return status
        if (
            status.get("state") == "prepared"
            and status.get("current_release") == release["prepared"]["previous_release"]
        ):
            return {
                "protocol_version": 1,
                "state": "restored",
                "current_release": status["current_release"],
            }
        expected = change.get("release", {}).get("release_version") or status.get("release_version")
        if not expected:
            raise UnknownOutcome("Hosting restore requires a reconciled candidate release version")
        return await adapter.call(
            "restore",
            {
                "operation_id": change["change_id"],
                "fence": fence,
                "component": site.source.component,
                "previous_release": release["prepared"]["previous_release"],
                "expected_release": expected,
            },
        )
