"""Agentless Azure task callbacks. Workflows own the schedule and retry policy."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict

from evander.addons import addon_runtime_dir
from evander.effects.providers.base import EffectProviderBase, scoped_effect_directory_path
from evander.events.models import ConfigValue
from evander.optional_integrations import require_azure_devops_module
from evander.workflow.catalog import WorkflowEditorFieldDefinition, WorkflowEditorNodeCatalogEntry
from evander.workflow.contracts import register_node_output_schema
from evander.workflow.plugins import ProviderPluginRegistration
from evander.workflow.state_store import WorkflowStateStore

from .builds import positive_id


class PipelineTaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    type: Literal["effect:ado:task-log-sync", "effect:ado:task-complete"]
    task: ConfigValue
    session_id: ConfigValue
    claim_start: ConfigValue = False
    timeout_seconds: ConfigValue = 7200
    result: ConfigValue = "failed"
    summary: ConfigValue = ""
    outputs: dict[str, Any] | None = None


def materialize(node):
    return PipelineTaskDefinition.model_validate(
        {
            "id": node.id,
            "display_name": node.display_name,
            "type": node.type,
            "outputs": node.outputs,
            **node.config,
        }
    )


class TaskReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipeline_id: int
    run_id: int
    project_id: UUID
    plan_id: UUID
    job_id: UUID
    timeline_id: UUID
    task_id: UUID


class TaskRequestError(RuntimeError):
    pass


class PipelineTaskClient:
    def __init__(self, context, directory, task):
        config = context.provider_config_resolver.resolve_for_directory(directory)
        require_azure_devops_module(config=config)
        self.token = context.resolve_secret(config.azure_devops_pat_env)
        if not self.token:
            raise ValueError("Azure DevOps build credential is unavailable")
        organization = str(config.azure_devops_organization_url or "").rstrip("/")
        project = str(config.azure_devops_project or "").strip()
        if not organization.startswith("https://") or not project:
            raise ValueError("Azure DevOps organization and project must be configured")
        self.task = task
        self.client = httpx.Client(
            base_url=f"{organization}/{quote(project, safe='')}/_apis/",
            auth=("", self.token),
            timeout=15.0,
            follow_redirects=False,
        )
        self.plan = f"{organization}/{task.project_id}/_apis/distributedtask/hubs/build/plans/{task.plan_id}"
        self.records = f"{self.plan}/timelines/{task.timeline_id}/records"

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, params={"api-version": "7.1"}, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else {}
        except httpx.HTTPError as exc:
            code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else "unavailable"
            )
            raise TaskRequestError(f"Azure DevOps task request failed ({code})") from None
        except ValueError:
            raise TaskRequestError("Azure DevOps returned invalid task data") from None

    def inspect(self):
        t = self.task
        build = self.request("GET", f"build/builds/{t.run_id}")
        if (
            str((build.get("project") or {}).get("id")) != str(t.project_id)
            or str((build.get("definition") or {}).get("id")) != str(t.pipeline_id)
            or str((build.get("orchestrationPlan") or {}).get("planId")) != str(t.plan_id)
        ):
            raise ValueError("Azure task does not belong to the specified pipeline run")
        timeline = self.request("GET", f"build/builds/{t.run_id}/timeline/{t.timeline_id}")
        records = timeline.get("records", [])
        record = next((r for r in records if str(r.get("id")) == str(t.task_id)), None)
        job = next((r for r in records if str(r.get("id")) == str(t.job_id)), None)
        if (
            not record
            or not job
            or record.get("type") != "Task"
            or job.get("type") != "Job"
            or str(record.get("parentId")) != str(t.job_id)
        ):
            raise ValueError("Azure task is outside the specified job and timeline")
        active = (
            str(build.get("status", "")).lower() not in {"completed", "cancelling"}
            and str(record.get("state", "")).lower() != "completed"
            and str(job.get("state", "")).lower() != "completed"
        )
        return record, active

    def append(self, state, record, lines, *, live=True):
        if not lines:
            return
        log_id = state.get("log_id") or (record.get("log") or {}).get("id")
        if not log_id:
            log = self.request(
                "POST", f"{self.plan}/logs", json={"path": f"logs/{self.task.task_id}"}
            )
            log_id = positive_id(log.get("id"), "task log ID")
            self.request(
                "PATCH",
                self.records,
                json={"value": [{"id": str(self.task.task_id), "log": {"id": log_id}}]},
            )
        state["log_id"] = log_id
        # Saving content and sending the console feed are separate Azure operations.
        self.request(
            "POST",
            f"{self.plan}/logs/{log_id}",
            content=("\n".join(lines) + "\n").encode(),
            headers={"Content-Type": "application/octet-stream"},
        )
        if live:
            self.request(
                "POST",
                f"{self.records}/{self.task.job_id}/feed",
                json={
                    "count": len(lines),
                    "value": lines,
                    "stepId": str(self.task.task_id),
                    "startLine": state.get("sent_lines", 0) + 1,
                },
            )
        state["sent_lines"] = state.get("sent_lines", 0) + len(lines)

    def complete(self, result):
        self.request(
            "POST",
            f"{self.plan}/events",
            json={
                "name": "TaskCompleted",
                "taskId": str(self.task.task_id),
                "jobId": str(self.task.job_id),
                "result": result,
            },
        )


def redact(text, context, token):
    secrets = {token}
    for key, value in context.env.items():
        if re.search(r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|API_KEY|PAT)(?:_|$)", key, re.I) and value:
            secrets.add(value)
    for secret in sorted(secrets, key=len, reverse=True):
        for value in (
            secret,
            base64.b64encode(secret.encode()).decode(),
            base64.b64encode((":" + secret).encode()).decode(),
            quote(secret, safe=""),
        ):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s\"',;]+", r"\1 [redacted]", text)
    text = re.sub(
        r"""(?ix)(["']?(?:password|passwd|pwd|token|access_token|api_key|secret|sig)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s&,;]+)""",
        r"\1[redacted]",
        text,
    )
    return text


def scoped_session(context, directory, session_id):
    runtime = getattr(context.admin_service, "runtime", None)
    session = runtime.policy.get_session(session_id) if runtime else None
    if session is not None and session.directory_path != directory:
        raise ValueError("Agent session is outside the workflow directory")
    return session


def rendered_logs(context, directory, session_id):
    """Use the worker's existing rendered transcript, without raw event JSON."""
    admin = getattr(context.admin_service, "vm_agent_admin", context.admin_service)
    session = scoped_session(context, directory, session_id)
    if session is None:
        return {}
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    if (
        session.vm_provider != "container_worker_vm"
        and metadata.get("execution_backend") != "container_worker_vm"
    ):
        return {}
    try:
        backend = admin.container_worker_backend(session_id=session_id, directory_path=directory)
        records = backend.repository.list_by_session(session_id)
        # A thread may span several worker executions. Its rendered session transcript
        # is cumulative, so choose its latest execution instead of repeating the thread.
        if not records:
            return {}
        record = records[-1]
        return {
            session_id: backend.get_log_snapshot(
                record.execution_id, lines=0, mode="session-history"
            )
        }
    except ValueError:
        # Configuration/assignment errors are log failures. The directory ownership
        # check above remains an authorization error and must not be suppressed.
        raise RuntimeError("Agent log source is unavailable") from None


class TaskEffect(EffectProviderBase):
    @classmethod
    def validate_definition(cls, node):
        return materialize(node)

    def execute(self, *, resolved_config=None, execution_context=None):
        context = self.runtime_context
        if context is None:
            raise ValueError("Azure task requires runtime context")
        directory = scoped_effect_directory_path(context)
        graph = str(context.metadata.get("graph_id") or "").strip()
        if not graph:
            raise ValueError("Azure task requires a workflow graph")
        # Scheduled events enter a separate connected graph. Azure run identity,
        # within the authorized directory, owns callbacks across those entries.
        graph = "ado-pipeline-tasks"
        payload = self.effect_definition.model_dump()
        payload.update(dict(resolved_config or {}))
        task = TaskReference.model_validate(payload["task"])
        positive_id(task.pipeline_id, "pipeline ID")
        positive_id(task.run_id, "run ID")
        session_id = str(payload["session_id"]).strip()
        if not session_id:
            raise ValueError("Agent session ID is required")
        scoped_session(context, directory, session_id)
        key = f"{task.project_id}:{task.pipeline_id}:{task.run_id}"
        store = WorkflowStateStore(context.config_path("runtime_dir"))
        lock_dir = (
            addon_runtime_dir(context.config_path("runtime_dir"), "azure-devops") / "task-locks"
        )
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_key = hashlib.sha256(f"{directory}:{graph}:{key}".encode()).hexdigest()
        with (lock_dir / lock_key).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            lookup = store.get(directory, graph, "ado-pipeline-tasks", key)
            run = dict(lookup.record.value) if lookup.record else {}
            client = PipelineTaskClient(context, directory, task)
            try:
                return self._update(
                    context,
                    payload,
                    execution_context or {},
                    task,
                    session_id,
                    directory,
                    graph,
                    key,
                    store,
                    run,
                    client,
                )
            finally:
                client.client.close()

    def _update(
        self,
        context,
        payload,
        execution,
        task,
        session_id,
        directory,
        graph,
        key,
        store,
        run,
        client,
    ):
        now = datetime.now(UTC)
        owner = str(execution.get("idempotency_key") or task.task_id)
        claim = payload.get("claim_start") is True
        completing = payload["type"] == "effect:ado:task-complete"
        if not run:
            if not claim and not completing:
                raise ValueError("Azure test run has not been registered")
            timeout = int(payload.get("timeout_seconds", 7200))
            if not 1 <= timeout <= 7200:
                raise ValueError("Azure task timeout must be between 1 and 7200 seconds")
            run = {
                "session_id": session_id,
                "owner": owner,
                "deadline": (now + timedelta(seconds=timeout)).isoformat(),
                "tasks": {},
            }
        if run["session_id"] != session_id:
            raise ValueError("Azure run is already associated with a different agent")
        state = run["tasks"].setdefault(str(task.task_id), {"cursors": {}, "attempts": 0})
        output = {
            "success": True,
            "done": False,
            "start_required": False,
            "retry_delay_seconds": 5,
            "synced_lines": 0,
            "status": "running",
        }
        if state.get("done"):
            status = run.get("result", "inactive") if state.get("completed") else "inactive"
            if not state.get("completed") and state["attempts"] >= 3:
                status = "callback_exhausted"
            elif state.get("log_attempts", 0) >= 3:
                status = "log_exhausted"
            return {
                **output,
                "done": True,
                "status": status,
            }
        if now >= datetime.fromisoformat(run["deadline"]) and not run.get("result"):
            run.update(result="failed", summary="Test workflow exceeded its 120-minute deadline.")
        if completing and not run.get("result"):
            result = payload.get("result")
            if result not in {"succeeded", "failed"}:
                raise ValueError("Azure task result must be succeeded or failed")
            run["result"] = result
            run["summary"] = redact(str(payload.get("summary") or "")[:2000], context, client.token)
        try:
            record, active = client.inspect()
        except TaskRequestError:
            if not run.get("result"):
                raise
            counter = "log_attempts" if state.get("completed") else "attempts"
            prefix = "log" if state.get("completed") else "callback"
            state[counter] = state.get(counter, 0) + 1
            state["done"] = state[counter] >= 3
            store.set(directory, graph, "ado-pipeline-tasks", key, value=run)
            return {
                **output,
                "success": False,
                "done": state["done"],
                "retry_delay_seconds": 15,
                "status": f"{prefix}_exhausted" if state["done"] else f"{prefix}_retry",
            }
        if (
            not active
            and state.get("callback_started")
            and record.get("state") == "completed"
            and record.get("result") == run.get("result")
        ):
            # The callback may have been accepted before its response was lost or
            # this process stopped. Azure's matching terminal result is the receipt.
            state["completed"] = True
        if not active and not state.get("completed"):
            state["done"] = True
            output.update(done=True, status="completed" if state.get("completed") else "inactive")
        elif not state.get("completed") and state["attempts"] >= 3:
            state["done"] = True
            output.update(success=False, done=True, status="callback_exhausted")
        else:
            output["start_required"] = claim and run["owner"] == owner and not run.get("result")
            log_exhausted = bool(run.get("result")) and state.get("log_attempts", 0) >= 3
            if run.get("result") and not log_exhausted:
                state["log_attempts"] = state.get("log_attempts", 0) + 1
            # Reserve terminal log attempts before I/O. Healthy batches reset this
            # budget; interruption or failure consumes it without blocking callbacks.
            store.set(directory, graph, "ado-pipeline-tasks", key, value=run)
            more_logs = False
            log_failed = False
            try:
                texts = {} if log_exhausted else rendered_logs(context, directory, session_id)
                for source, text in texts.items():
                    lines = text.splitlines()
                    cursor = state["cursors"].get(source, {"line": 0, "hash": ""})
                    offset = cursor["line"]
                    digest = hashlib.sha256("\n".join(lines[:offset]).encode()).hexdigest()
                    reset = offset > len(lines) or (offset and digest != cursor["hash"])
                    if reset:
                        offset = 0
                    end = min(len(lines), offset + 1000)
                    batch = [redact(line, context, client.token) for line in lines[offset:end]]
                    if reset:
                        batch.insert(
                            0,
                            "[Evander] Agent log changed or restarted; resuming available output.",
                        )
                    client.append(state, record, batch, live=not state.get("completed"))
                    more_logs = more_logs or end < len(lines)
                    state["cursors"][source] = {
                        "line": end,
                        "hash": hashlib.sha256("\n".join(lines[:end]).encode()).hexdigest(),
                    }
                    output["synced_lines"] += len(batch)
            except (OSError, RuntimeError) as exc:
                # Never persist provider response bodies or log content in run state.
                state["log_error"] = type(exc).__name__
                log_failed = True
            if (
                run.get("result")
                and not more_logs
                and not log_failed
                and not log_exhausted
                and not state.get("footer")
            ):
                try:
                    client.append(
                        state,
                        record,
                        [f"[Evander] {run['result']}: {run.get('summary', '')}"],
                        live=not state.get("completed"),
                    )
                    state["footer"] = True
                except TaskRequestError:
                    state["log_error"] = "TaskRequestError"
                    log_failed = True
            if run.get("result") and not log_failed and not log_exhausted:
                state["log_attempts"] = 0
            if (
                run.get("result")
                and (not more_logs or log_failed or log_exhausted)
                and not state.get("completed")
            ):
                state["callback_started"] = True
                state["attempts"] += 1
                store.set(directory, graph, "ado-pipeline-tasks", key, value=run)
                try:
                    client.complete(run["result"])
                    state["completed"] = True
                except TaskRequestError:
                    output.update(success=False, retry_delay_seconds=15, status="callback_retry")
                    if state["attempts"] >= 3:
                        state["done"] = True
                        output.update(done=True, status="callback_exhausted")
            if state.get("completed"):
                if log_failed or log_exhausted:
                    state["done"] = state["log_attempts"] >= 3
                    output.update(
                        success=False,
                        done=state["done"],
                        retry_delay_seconds=15,
                        status="log_exhausted" if state["done"] else "log_retry",
                    )
                elif more_logs:
                    output.update(status="log_retry")
                else:
                    state["done"] = True
                    output.update(done=True, status=run["result"])
        store.set(directory, graph, "ado-pipeline-tasks", key, value=run)
        return output


def register_providers():
    outputs = {
        "success": {"type": "boolean"},
        "done": {"type": "boolean"},
        "start_required": {"type": "boolean"},
        "synced_lines": {"type": "integer"},
        "retry_delay_seconds": {"type": "integer"},
        "status": {"type": "string"},
    }
    entries = []
    field_kinds = {
        "task": "json",
        "session_id": "string",
        "claim_start": "boolean",
        "timeout_seconds": "number",
        "result": "enum",
        "summary": "string",
    }
    types = {
        "effect:ado:task-log-sync": "Forward agent logs to Azure task",
        "effect:ado:task-complete": "Complete Azure pipeline task",
    }
    for kind, label in types.items():
        register_node_output_schema(node_type=kind, outputs=outputs)
        entries.append(
            WorkflowEditorNodeCatalogEntry(
                kind="effect",
                type=kind,
                label=label,
                description="Uses directory-scoped host credentials and persisted task state. Schedule retries in the workflow.",
                fields=[
                    WorkflowEditorFieldDefinition(
                        key=key,
                        label=key.replace("_", " ").title(),
                        kind=field_kinds[key],
                        options=[
                            {"value": value, "label": value.title()}
                            for value in ("succeeded", "failed")
                        ]
                        if key == "result"
                        else [],
                        default_value={
                            "claim_start": False,
                            "timeout_seconds": 7200,
                            "result": "failed",
                        }.get(key),
                        required=key in {"task", "session_id"},
                        bindable=True,
                    )
                    for key in (
                        "task",
                        "session_id",
                        "claim_start",
                        "timeout_seconds",
                        "result",
                        "summary",
                    )
                ],
            )
        )
    return ProviderPluginRegistration(
        effect_providers={kind: TaskEffect for kind in types},
        node_definition_resolvers={kind: materialize for kind in types},
        workflow_editor_catalog_entries=tuple(entries),
    )
