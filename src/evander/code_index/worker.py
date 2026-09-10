from __future__ import annotations

import json
import shlex

from evander.effects.providers.agent_session import container_shell_preamble
from evander.effects.providers.schemas import (
    AgentCloneRepoEffectDefinition,
    AgentCreateEffectDefinition,
)
from evander.effects.registry import EffectProviderFactory
from evander.execution.container_worker_client import ContainerWorkerClient
from evander.worker.models import (
    WorkerFileWrite,
    WorkerFileWriteRequest,
    WorkerJobRequest,
    WorkerResourceLimits,
)
from .models import IndexSettings, MAX_ARTIFACT_BYTES


class IndexWorker:
    """Use normal scoped session provisioning and deterministic worker jobs."""

    def __init__(self, components):
        self.components = components

    def client(self, build: dict) -> ContainerWorkerClient:
        config = self.components.runtime.provider_config.resolve_for_session_id(build["session_id"])
        return ContainerWorkerClient(
            base_url=config.container_worker_base_url,
            token_env=config.container_worker_token_env,
            timeout_seconds=config.container_worker_request_timeout_seconds,
        )

    def start(self, build: dict) -> str:
        factory = EffectProviderFactory(
            provider_config_resolver=self.components.runtime.provider_config,
            admin_service=self.components,
        )
        settings = IndexSettings.model_validate(build["config"])
        config = settings.model_dump()
        create = AgentCreateEffectDefinition(
            id=build["id"] + "-session",
            display_name="Prepare repository index worker",
            type="effect:agent:create",
            session_id=build["session_id"],
            execution_backend="container_worker_vm",
            worker_id=config["worker_id"],
            container_image=config["image"],
            existing_agent_behavior="upsert",
            allowed_tools=[],
            ttl_hours=24,
            metadata={"code_index_build_id": build["id"]},
        )
        factory.build(create, directory_path=build["directory"]).execute()
        # The deterministic ID lets a restarted reconciler adopt an already submitted job.
        client = self.client(build)
        try:
            existing = client.get(build["id"])
            return existing.job_id
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
        pin = self.components.managed_git_admin.pin_retained_snapshot(
            build["repo"],
            build["directory"],
            binding_kind="code_indexes",
            binding_id=build["id"],
            pin_ref="refs/evander/code-index/" + build["id"],
            commit_sha=build["sha"],
        )
        clone = AgentCloneRepoEffectDefinition(
            id=build["id"] + "-clone",
            display_name="Clone repository for indexing",
            type="effect:agent:clone-repo",
            session_id=build["session_id"],
            managed_repo_id=build["repo"],
            clone_branch=config["tracked_ref"],
            pinned_commit_sha=build["sha"],
            pinned_ref=pin["pin_ref"],
            git_access_mode="read-only",
        )
        result = factory.build(clone, directory_path=build["directory"]).execute()
        repo_path = result["target_dir"]
        client.write_files(
            build["session_id"],
            WorkerFileWriteRequest(
                files=[
                    WorkerFileWrite(
                        destination_path="/home/agent/code-index-settings.json",
                        # Scheduling and capacity belong to the control plane; keep
                        # the compiler payload compatible with the pinned image.
                        content=json.dumps(
                            {**settings.index_policy(), "timeout_seconds": settings.timeout_seconds}
                        ),
                    )
                ]
            ),
        )
        command = [
            "timeout",
            "--signal=TERM",
            "--kill-after=10s",
            str(config["timeout_seconds"]),
            "python3",
            "-m",
            "evander.code_index.runner",
            "--repo",
            repo_path,
            "--commit",
            build["sha"],
            "--settings",
            "/home/agent/code-index-settings.json",
            "--output",
            "/artifacts/code-index.json",
        ]
        # Profile seeds package/Git relay configuration; keep the pinned compiler first.
        shell = (
            container_shell_preamble()
            + "export DOTNET_ROOT=/opt/code-index/dotnet\nexport PATH=/opt/code-index/node/bin:/opt/code-index/typescript/node_modules/.bin:/opt/code-index/dotnet:$PATH\nexec "
            + shlex.join(command)
        )
        request = WorkerJobRequest(
            session_id=build["session_id"],
            job_id=build["id"],
            image=config["image"],
            command=["bash", "-lc", shell],
            working_directory=repo_path,
            resource_limits=WorkerResourceLimits(
                memory="8g", cpus=config["worker_cpus"], pids_limit=1024
            ),
            metadata={
                "code_index_build_id": build["id"],
                "directory_path": build["directory"],
                "execution_kind": "code_index",
            },
        )
        return client.create(request).job_id

    def status(self, build: dict) -> str:
        record = self.client(build).get(build["worker_job_id"] or build["id"])
        status = str(getattr(record.status, "value", record.status))
        if status in {"exited", "completed", "succeeded"}:
            return (
                "completed"
                if record.metadata.get("exit_code") == 0 and not record.error
                else "failed"
            )
        if status == "removed":
            return "failed"
        return status

    def artifact(self, build: dict) -> str:
        return self.client(build).read_session_artifact_text_bounded(
            build["session_id"], "code-index.json", max_bytes=MAX_ARTIFACT_BYTES
        )

    def stop(self, build: dict) -> None:
        try:
            self.client(build).stop(build["worker_job_id"] or build["id"])
        except Exception as exc:
            if getattr(exc, "status_code", None) != 404:
                raise

    def cleanup(self, build: dict) -> None:
        self.components.vm_agent_admin.cleanup_container_session(build["session_id"])
        self.components.managed_git_admin.release_retained_snapshot(
            build["repo"],
            build["directory"],
            binding_kind="code_indexes",
            binding_id=build["id"],
            pin_ref="refs/evander/code-index/" + build["id"],
            expected_commit_sha=build["sha"],
        )
