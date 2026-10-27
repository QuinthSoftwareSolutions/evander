"""Owner-authored bounded plans. Durable receipts never resume or replay input."""

from __future__ import annotations

import hashlib
import json
import uuid
from urllib.parse import quote, quote_plus

from .checkpoints import matches, triggered
from .choices import target_actions
from .execution import BrowserRun, redact
from .models import BrowserCommand, BrowserError

STATE_KEYS = (
    "role", "name", "value", "checked", "selected", "expanded", "disabled",
    "readonly", "editable", "required", "context",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def page_identity(observation):
    identity = tuple(observation.get(k) for k in ("page_id", "page_epoch", "url"))
    if not all(isinstance(item, str) and item for item in identity):
        raise BrowserError("Endpoint cannot establish batch document identity", "batch_identity_required", 409)
    if observation.get("truncated"):
        raise BrowserError("A batch requires a complete observation", "batch_incomplete", 409)
    return identity


def target_state(element):
    return digest({k: element.get(k) for k in STATE_KEYS})


def target_identity(element):
    return digest({k: element.get(k) for k in ("role", "name", "context")})


def observation_fingerprint(observation):
    try:
        page = page_identity(observation)
    except BrowserError:
        return None
    return digest({"page": page, "elements": [
        {"ref": e.get("ref"), "state": target_state(e)} for e in observation.get("elements", [])
    ]})


def resolve(observation, target):
    if target is None:
        return None
    found = [e for e in observation.get("elements", []) if matches(e, target)]
    if len(found) != 1:
        raise BrowserError("Planned target is missing or ambiguous", "batch_target_changed", 409)
    element = found[0]
    if len(str(element.get("value", "")).encode("utf-16-le", "surrogatepass")) >= 2000:
        raise BrowserError("A clipped target value cannot ground a batch", "batch_incomplete", 409)
    return element


def confirmed_change(action, before, after, values, checkpoints):
    """Allow only predictable input effects, or an explicit satisfied state check."""
    expected = dict(before)
    if action.action in {"fill", "type"}:
        value = values[action.input].value
        if action.action == "type":
            if not isinstance(before.get("value"), str):
                raise BrowserError("Typing needs an observable starting value", "batch_state_changed", 409)
            value = before["value"] + value
        expected["value"] = value
    elif action.action == "click":
        for key in ("checked", "expanded", "selected"):
            previous = before.get(key)
            toggle = key == "expanded" or (key == "checked" and before["role"] == "checkbox")
            select = (key == "checked" and before["role"] == "radio") or (
                key == "selected" and before["role"] in {"tab", "option"}
            )
            if (toggle or select) and previous in (True, False, "true", "false"):
                changed = previous not in (True, "true") if toggle else True
                proposed = str(changed).lower() if isinstance(previous, str) else changed
                expected[key] = proposed
    for checkpoint in checkpoints:
        condition = checkpoint.condition
        if condition.kind in {"value", "checked", "expanded", "disabled"} and matches(after, condition.target):
            if condition.kind == "value":
                # A fill/type may not silently substitute a different supplied literal.
                if action.action not in {"fill", "type"}:
                    expected["value"] = values[condition.input].value
            else:
                state = after.get(condition.kind)
                expected[condition.kind] = str(condition.expected).lower() if isinstance(state, str) else condition.expected
    if target_state(expected) != target_state(after):
        raise BrowserError("Input did not produce its expected observed state", "batch_state_changed", 409)


class BrowserBatch(BrowserRun):
    def execute_plan(self, agent, session_id, request, fingerprint, settings):
        actor = "agent:" + agent
        run, claimed = self.store.claim_run(
            session_id, actor, request, fingerprint,
            seconds=min(5, settings.timeout_seconds, request.timeout_seconds or 5),
            max_steps=len(request.plan),
        )
        if not claimed:
            return run
        observation = None
        current = False
        mutating = False
        terminal, reason = "needs_pa", "Inspect the page before continuing"
        completion = None
        satisfied = set()
        secrets = sorted({form for v in request.values.values() if v.sensitive and v.value
                          for form in (v.value, quote(v.value, safe=""), quote_plus(v.value))}, key=len, reverse=True)
        try:
            observation = self.observe(agent, session_id, actor, run)
            current = True
            if observation_fingerprint(observation) != run["observation_fingerprint"]:
                raise BrowserError("The owner's observed page changed before execution", "batch_observation_changed", 409)
            page = page_identity(observation)
            initial = [resolve(observation, action.target) for action in request.plan]
            expected = {e["ref"]: target_state(e) for e in observation["elements"]}
            identities = {e["ref"]: target_identity(e) for e in initial if e is not None}
            # Validate the complete plan, including later inputs, before any input.
            for action, element in zip(request.plan, initial):
                self.validate_action(action, element, request.values)
            due_by_step = self.checkpoint_schedule(request, initial)
            for index, action in enumerate(request.plan):
                self.check(agent, session_id, run)
                if page_identity(observation) != page:
                    raise BrowserError("Page changed; inspect before planning more input", "batch_page_changed", 409)
                # Do not let a fresh observation silently reauthorize changed assumptions.
                for future, before in zip(request.plan[index:], initial[index:]):
                    element = resolve(observation, future.target)
                    if before is not None and (
                        element["ref"] != before["ref"] or target_state(element) != expected[before["ref"]]
                    ):
                        raise BrowserError("A planned target's state changed", "batch_target_changed", 409)
                element = resolve(observation, action.target)
                self.validate_action(action, element, request.values)
                options = {"ref": element["ref"]} if element else {}
                if action.input is not None:
                    options["text"] = request.values[action.input].value
                elif action.key is not None:
                    options["text"] = action.key
                if action.action == "scroll":
                    options.update(delta_x=action.delta_x, delta_y=action.delta_y)
                command = BrowserCommand(
                    id=uuid.uuid4().hex, generation=run["generation"], action=action.action,
                    observation_id=observation["observation_id"], revalidate=True,
                    deadline_at=run["deadline_at"], **options,
                )
                mutating, current = True, False
                self.service.command(session_id, actor, command, agent=True, run_id=run["id"], step_index=index)
                mutating = False
                for checkpoint in due_by_step[index]:
                    observation = self.wait_checkpoint(agent, session_id, actor, run, checkpoint, request.values)
                    current = True
                    satisfied.add(checkpoint.id)
                if index + 1 < len(request.plan):
                    observation = self.observe(agent, session_id, actor, run)
                    current = True
                    if page_identity(observation) != page:
                        raise BrowserError("Page changed during a batch", "batch_page_changed", 409)
                    if element is not None:
                        after = resolve(observation, action.target)
                        before = initial[index]
                        if after["ref"] != before["ref"] or target_identity(after) != identities[before["ref"]]:
                            raise BrowserError("Edited target identity changed", "batch_target_changed", 409)
                        confirmed_change(action, element, after, request.values, due_by_step[index])
                        expected[before["ref"]] = target_state(after)
                    if {e["ref"]: target_state(e) for e in observation["elements"]} != expected:
                        raise BrowserError("Observed controls or page context changed unexpectedly", "batch_state_changed", 409)
            # Explicit plans execute all actions even when completion was true initially.
            observation = self.observe(agent, session_id, actor, run)
            current = True
            if request.completion is not None:
                while True:
                    completion = self.completed(request, observation, satisfied)
                    if completion is not None:
                        break
                    self.sleep(min(0.1, self.remaining(run)))
                    observation = self.observe(agent, session_id, actor, run)
            terminal, reason = "needs_verification", "Planned actions completed; the owning agent must verify the goal"
        except BrowserError as exc:
            terminal = "outcome_unknown" if mutating and exc.code != "stale_target" else "needs_pa"
            reason = exc.code + ": inspect current state before continuing"
        except Exception:
            terminal = "outcome_unknown" if mutating else "needs_pa"
            reason = "Batch interrupted; inspect current state before continuing"
        finally:
            if current:
                try:
                    self.check(agent, session_id, run)
                except Exception:
                    current = False
            result = self.store.update_run(session_id, run["id"], status=terminal, reason=reason, completion=completion)
            if result["status"] != terminal:
                current = False
        return {**result, "observation": redact(observation, secrets), "observation_current": current}

    @staticmethod
    def validate_action(action, element, values):
        if element is not None and action.action not in target_actions(element, has_values=bool(values)):
            raise BrowserError("Planned action is unavailable for its target", "batch_action_unavailable", 409)

    @staticmethod
    def checkpoint_schedule(request, initial):
        schedule = [[] for _ in request.plan]
        start = 0
        for checkpoint in request.checkpoints:
            for index in range(start, len(request.plan)):
                action, target = request.plan[index], initial[index]
                if triggered(checkpoint, action.action, target["ref"] if target else None, action.input, action.key):
                    schedule[index].append(checkpoint)
                    start = index
                    break
            else:
                raise BrowserError("A checkpoint cannot be reached by this plan", "batch_checkpoint_unreachable", 409)
        return schedule
