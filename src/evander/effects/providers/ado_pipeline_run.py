from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from evander.ado_builds import AdoBuildClient, positive_id

from .base import EffectProviderBase, EffectProviderExecutionError

_ADO_API_VERSION = "7.1-preview.1"
_TERMINAL_STATES = {"completed"}
_SUCCESS_RESULTS = {"succeeded"}


class AdoPipelineRunEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._effect_payload()
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = self._require_non_empty_string(
            payload.get("directory_path"), field_name="directory_path"
        )
        config = self._resolve_directory_config(directory_path)
        organization_url = self._require_non_empty_string(
            getattr(config, "azure_devops_organization_url", None),
            field_name="azure_devops_organization_url",
        )
        project = self._normalize_optional_string(
            payload.get("project")
        ) or self._require_non_empty_string(
            getattr(config, "azure_devops_project", None),
            field_name="azure_devops_project",
        )
        pat_env = self._require_non_empty_string(
            getattr(config, "azure_devops_pat_env", None), field_name="azure_devops_pat_env"
        )
        pat = self._context().resolve_secret(pat_env)
        if not pat:
            raise RuntimeError(
                f"effect:ado:pipeline-run Azure DevOps PAT is not configured in {pat_env}"
            )

        pipeline_id = self._coerce_positive_int(
            payload.get("pipeline_id"),
            field_name="pipeline_id",
        )
        branch = self._normalize_branch(payload.get("branch"))
        variables = self._coerce_variables(payload.get("variables"))
        template_parameters = self._coerce_mapping(
            payload.get("template_parameters"), field_name="template_parameters"
        )
        timeout_seconds = self._coerce_float(
            payload.get("timeout_seconds"), field_name="timeout_seconds", default=3600.0
        )
        if timeout_seconds <= 0:
            raise ValueError("effect:ado:pipeline-run timeout_seconds must be greater than zero")
        poll_interval_seconds = self._coerce_float(
            payload.get("poll_interval_seconds"),
            field_name="poll_interval_seconds",
            default=10.0,
        )
        if poll_interval_seconds < 0:
            raise ValueError("effect:ado:pipeline-run poll_interval_seconds must be non-negative")
        check = self._coerce_bool(payload.get("check"), default=True)

        request_payload = self._build_queue_payload(
            branch=branch,
            variables=variables,
            template_parameters=template_parameters,
        )
        reject_parameters = self._coerce_mapping(
            payload.get("reject_running_parameters"), field_name="reject_running_parameters"
        )
        if reject_parameters:
            self._reject_matching_active_run(
                directory_path, project, pipeline_id, reject_parameters
            )
        project_segment = quote(project, safe="")
        base_url = (
            f"{organization_url.rstrip('/')}/{project_segment}/_apis/pipelines/{pipeline_id}/runs"
        )
        auth = ("", pat)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                base_url,
                params={"api-version": _ADO_API_VERSION},
                headers=headers,
                auth=auth,
                json=request_payload,
            )
            response.raise_for_status()
            run = self._coerce_response_object(response)
            run_id = self._normalize_optional_string(run.get("id"))
            if not run_id:
                raise RuntimeError(
                    "effect:ado:pipeline-run queue response did not include a run id"
                )

            deadline = time.monotonic() + timeout_seconds
            while not self._is_terminal(run):
                if time.monotonic() >= deadline:
                    output = self._output(
                        run,
                        organization_url=organization_url,
                        project=project,
                        pipeline_id=pipeline_id,
                        queued=True,
                        completed=False,
                    )
                    if check:
                        raise EffectProviderExecutionError(
                            "effect:ado:pipeline-run timed out waiting for Azure DevOps run "
                            f"{run_id}",
                            outputs=output,
                        )
                    return output
                if poll_interval_seconds:
                    time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))
                poll_response = client.get(
                    f"{base_url}/{quote(run_id, safe='')}",
                    params={"api-version": _ADO_API_VERSION},
                    headers=headers,
                    auth=auth,
                )
                poll_response.raise_for_status()
                run = self._coerce_response_object(poll_response)

        output = self._output(
            run,
            organization_url=organization_url,
            project=project,
            pipeline_id=pipeline_id,
            queued=True,
            completed=True,
        )
        if check and not output["success"]:
            raise EffectProviderExecutionError(
                "effect:ado:pipeline-run Azure DevOps run "
                f"{output['run_id']} completed with result {output['result'] or output['status']}",
                outputs=output,
            )
        return output

    def _effect_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        effect = self.effect_definition
        for field_name in (
            "directory_path",
            "pipeline_id",
            "project",
            "branch",
            "variables",
            "template_parameters",
            "reject_running_parameters",
            "timeout_seconds",
            "poll_interval_seconds",
            "check",
        ):
            if effect is not None and hasattr(effect, field_name):
                payload[field_name] = getattr(effect, field_name)
        return payload

    def _reject_matching_active_run(self, directory_path, project, pipeline_id, parameters):
        # This is a best-effort busy check, not an exclusive environment lease.
        with AdoBuildClient(
            self._context(), directory_path=directory_path, project=project
        ) as client:
            continuation = None
            seen = set()
            while True:
                params = {
                    "definitions": pipeline_id,
                    "statusFilter": "notStarted,inProgress,postponed,cancelling",
                    "$top": 100,
                }
                if continuation:
                    params["continuationToken"] = continuation
                page, continuation = client.list_builds(**params)
                for build in page:
                    if str(build.get("status")).lower() == "completed":
                        continue
                    run_id = positive_id(build.get("id"), "active run id")
                    supplied = build.get("templateParameters")
                    if not isinstance(supplied, Mapping) or any(
                        key not in supplied for key in parameters
                    ):
                        run, _ = client.get(f"pipelines/{pipeline_id}/runs/{run_id}")
                        supplied = run.get("templateParameters")
                    if not isinstance(supplied, Mapping) or any(
                        key not in supplied for key in parameters
                    ):
                        raise RuntimeError(
                            "Could not determine the target of an active deployment; no run queued"
                        )
                    if all(str(supplied[key]) == str(value) for key, value in parameters.items()):
                        raise EffectProviderExecutionError(
                            "A matching deployment is already queued or running; no run queued",
                            outputs={
                                "success": False,
                                "queued": False,
                                "busy": True,
                                "run_id": str(run_id),
                                "run_url": f"{client.base_url}/_build/results?buildId={run_id}",
                            },
                        )
                if not continuation:
                    break
                if continuation in seen or len(seen) >= 100:
                    raise RuntimeError(
                        "Active deployment pagination could not be completed; no run queued"
                    )
                seen.add(continuation)

    def _resolve_directory_config(self, directory_path: str) -> Any:
        context = self._context()
        resolver = getattr(context, "provider_config_resolver", None)
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            return resolve_for_directory(directory_path)
        return context.resolved_config

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AdoPipelineRunEffectProvider has no runtime context")
        return self.runtime_context

    @classmethod
    def _build_queue_payload(
        cls,
        *,
        branch: str | None,
        variables: Mapping[str, Any],
        template_parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if branch:
            payload["resources"] = {"repositories": {"self": {"refName": branch}}}
        if variables:
            payload["variables"] = {
                key: cls._variable_payload(value) for key, value in variables.items()
            }
        if template_parameters:
            payload["templateParameters"] = dict(template_parameters)
        return payload

    @staticmethod
    def _variable_payload(value: Any) -> dict[str, str]:
        if isinstance(value, Mapping) and "value" in value:
            raw_value = value.get("value")
        else:
            raw_value = value
        if isinstance(raw_value, bool):
            normalized = "true" if raw_value else "false"
        elif raw_value is None:
            normalized = ""
        else:
            normalized = str(raw_value)
        return {"value": normalized}

    @staticmethod
    def _coerce_response_object(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise RuntimeError("effect:ado:pipeline-run expected Azure DevOps JSON object response")
        return dict(payload)

    @classmethod
    def _output(
        cls,
        run: Mapping[str, Any],
        *,
        organization_url: str,
        project: str,
        pipeline_id: int,
        queued: bool,
        completed: bool,
    ) -> dict[str, Any]:
        pipeline = run.get("pipeline") if isinstance(run.get("pipeline"), Mapping) else {}
        result = cls._normalize_optional_string(run.get("result"))
        status = cls._normalize_optional_string(run.get("state") or run.get("status"))
        run_id = cls._normalize_optional_string(run.get("id")) or ""
        run_url = cls._run_url(run)
        success = completed and (result or "").casefold() in _SUCCESS_RESULTS
        return {
            "success": success,
            "queued": queued,
            "completed": completed,
            "run_id": run_id,
            "run_url": run_url,
            "pipeline_id": cls._coerce_int(pipeline.get("id"), default=pipeline_id),
            "pipeline_name": cls._normalize_optional_string(pipeline.get("name")),
            "status": status,
            "result": result,
            "project": project,
            "organization_url": organization_url.rstrip("/"),
            "started_at": cls._normalize_optional_string(
                run.get("createdDate") or run.get("created_at") or run.get("startTime")
            ),
            "finished_at": cls._normalize_optional_string(
                run.get("finishedDate") or run.get("finished_at") or run.get("finishTime")
            ),
        }

    @classmethod
    def _run_url(cls, run: Mapping[str, Any]) -> str | None:
        links = run.get("_links")
        if isinstance(links, Mapping):
            web = links.get("web")
            if isinstance(web, Mapping):
                href = cls._normalize_optional_string(web.get("href"))
                if href:
                    return href
        return cls._normalize_optional_string(run.get("webUrl") or run.get("url"))

    @classmethod
    def _is_terminal(cls, run: Mapping[str, Any]) -> bool:
        state = cls._normalize_optional_string(run.get("state") or run.get("status"))
        return (state or "").casefold() in _TERMINAL_STATES

    @staticmethod
    def _require_non_empty_string(value: Any, *, field_name: str) -> str:
        text = "" if value is None else str(value).strip()
        if not text:
            raise ValueError(f"effect:ado:pipeline-run requires {field_name}")
        return text

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_branch(cls, value: Any) -> str | None:
        text = cls._normalize_optional_string(value)
        if text is None:
            return None
        if text.startswith("refs/"):
            return text
        return f"refs/heads/{text}"

    @staticmethod
    def _coerce_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError(f"effect:ado:pipeline-run {field_name} must resolve to an object")
        return {str(key).strip(): item for key, item in value.items() if str(key).strip()}

    @classmethod
    def _coerce_variables(cls, value: Any) -> dict[str, Any]:
        return cls._coerce_mapping(value, field_name="variables")

    @classmethod
    def _coerce_positive_int(cls, value: Any, *, field_name: str) -> int:
        result = cls._coerce_int(value, default=None)
        if result is None or result <= 0:
            raise ValueError(f"effect:ado:pipeline-run {field_name} must be a positive integer")
        return result

    @staticmethod
    def _coerce_int(value: Any, *, default: int | None) -> int | None:
        if value is None or isinstance(value, bool):
            return default
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value: Any, *, field_name: str, default: float) -> float:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            raise ValueError(f"effect:ado:pipeline-run {field_name} must be a number")
        try:
            return float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"effect:ado:pipeline-run {field_name} must be a number") from exc

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        raise ValueError("effect:ado:pipeline-run check must resolve to a boolean")
