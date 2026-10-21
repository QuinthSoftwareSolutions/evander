"""Exercise real provider adapters through the PA tool's durable action boundary."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from evander.classification.models import ClassificationSettings
from evander.classification.service import ClassificationService
from evander.classification.jev import JevProvider
from evander.classification.decisions import DecisionsProvider
from evander_browser_sessions_addon.models import BrowserCommand, BrowserError, RunRequest, Settings


@pytest.fixture(params=["jev", "openai_decisions"])
def execution(request, domain, hosted):
    service, bindings, peers = domain
    revision, settings = service.store.settings("root")
    settings.fast_execution.enabled = True
    service.store.save_settings("root", revision, settings)
    service.store.endpoint_features(hosted.id, {"fast_execution": 1})
    state = {
        "value": "",
        "saved": False,
        "provider_calls": [],
        "commands": [],
        "after_decision": None,
        "probability": 1,
        "mode": "normal",
        "attribution": [],
    }
    original = peers.call

    def peer(base, token, method, path, payload):
        if path.endswith("/command"):
            command = payload["command"]
            state["commands"].append(command)
            if command["action"] == "observe":
                return {
                    "observation_id": "observation-" + str(len(state["commands"])),
                    "page_epoch": "1",
                    "url": "https://application.test/accounts",
                    "elements": [
                        {
                            "ref": "node-1",
                            "role": "textbox",
                            "name": "Name",
                            "value": state["value"][:1000]
                            if state["mode"] == "clipped"
                            else state["value"],
                            "context": [{"role": "form", "name": "Create account"}],
                        },
                        {"ref": "node-2", "role": "button", "name": "Save", "value": ""},
                        {
                            "ref": "node-3",
                            "role": "alert",
                            "name": "Account saved" if state["saved"] else "",
                            "value": "",
                        },
                    ],
                    "truncated": state["mode"] == "truncated",
                }
            if state["mode"] == "unknown":
                raise BrowserError("Lost command response", "outcome_unknown", 409)
            if command["action"] == "fill":
                state["value"] = command["text"]
            if command["action"] == "click":
                state["saved"] = True
            return {"success": True}
        return original(base, token, method, path, payload)

    peers.call = peer
    provider = request.param

    async def decide(wire):
        body = json.loads(wire.content)
        state["provider_calls"].append(body)
        if state["after_decision"]:
            state["after_decision"]()
        if state["mode"] == "error":
            return httpx.Response(503)
        if not state["value"]:
            selected = {
                "action": "fill",
                "target": "node-1",
                "input": "v0",
                "key": "Enter",
                "direction": "down",
            }
        elif not state["saved"]:
            selected = {
                "action": "click",
                "target": "node-2",
                "input": "_none",
                "key": "Enter",
                "direction": "down",
            }
        else:
            selected = {
                "action": "done",
                "target": "_none",
                "input": "_none",
                "key": "Enter",
                "direction": "down",
            }
        if state["mode"] == "wait":
            selected["action"] = "wait"
        if provider == "jev":
            questions = body["questions"]
            answers = {}
            for name, q in questions.items():
                chosen = selected[name]
                if chosen not in q["criteria"]:
                    chosen = next(iter(q["criteria"]))
                other = next(key for key in q["criteria"] if key != chosen)
                probabilities = {
                    key: state["probability"]
                    if key == chosen
                    else 1 - state["probability"]
                    if key == other
                    else 0
                    for key in q["criteria"]
                }
                answers[name] = {
                    "type": "choice",
                    "choice": chosen,
                    "confidence": 1,
                    "probabilities": probabilities,
                }
        else:
            answers = []
            for q in body["questions"]:
                chosen = selected[q["name"]]
                options = [c["value"] for c in q["choices"]]
                if chosen not in options:
                    chosen = options[0]
                other = next(key for key in options if key != chosen)
                answers.append(
                    {
                        "name": q["name"],
                        "type": "choice",
                        "choice": chosen,
                        "confidence": 1,
                        "probabilities": [
                            {
                                "value": key,
                                "probability": state["probability"]
                                if key == chosen
                                else 1 - state["probability"]
                                if key == other
                                else 0,
                            }
                            for key in options
                        ],
                    }
                )
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )

    classifier = ClassificationService(
        providers={
            provider: (JevProvider if provider == "jev" else DecisionsProvider)(
                transport=httpx.MockTransport(decide)
            )
        },
        secret_resolver=lambda _: "synthetic-key",
    )
    evaluate = classifier.evaluate

    def evaluate_with_attribution(settings, payload, **kwargs):
        assert settings.providers[provider].max_retries == 0
        assert 0 < settings.providers[provider].timeout_seconds <= 5
        state["attribution"].append(kwargs["attribution"])
        return evaluate(settings, payload, **kwargs)

    classifier.evaluate = evaluate_with_attribution
    configured = ClassificationSettings(
        enabled=True, default_provider=provider, providers={provider: {"enabled": True}}
    )
    bindings.classifier = lambda _: (classifier, configured)

    def run(**kwargs):
        payload = dict(
            request_id="create-account",
            generation=hosted.control_generation,
            goal="Create an account named Example",
            values={"name": {"description": "Account name", "value": "Example"}},
        )
        payload.update(kwargs)
        return service.run("agent-a", hosted.id, RunRequest(**payload))

    return SimpleNamespace(
        service=service,
        session=hosted,
        state=state,
        run=run,
        bindings=bindings,
        settings=configured,
        classifier=classifier,
    )


def test_multistep_adapters_literals_fresh_final_state_and_retry(execution):
    e = execution
    result = e.run()
    assert result["status"] == "needs_verification"
    assert [step["action"] for step in result["steps"]] == ["fill", "click"]
    assert result["observation_current"]
    assert result["observation"]["elements"][-1]["name"] == "Account saved"
    assert all(c["include_image"] is False for c in e.state["commands"] if c["action"] == "observe")
    assert len(e.state["provider_calls"]) == 3
    assert e.state["attribution"][-1]["session_id"] == "agent-a"
    assert e.state["attribution"][-1]["trace_id"] == result["id"]
    before = len(e.state["commands"])
    assert e.run()["id"] == result["id"]
    assert len(e.state["commands"]) == before
    with pytest.raises(BrowserError, match="identity conflict"):
        e.run(goal="Different objective")


@pytest.mark.parametrize("control", ["pause", "claim"])
def test_control_change_while_deciding_prevents_input(execution, control):
    e = execution
    e.state["after_decision"] = lambda: e.service.control(e.session.id, "user:operator", control)
    result = e.run()
    assert result["status"] == "cancelled"
    assert not any(c["action"] != "observe" for c in e.state["commands"])


def test_automation_lease_blocks_other_observers_and_retry_does_not_start_second_run(execution):
    e = execution

    def during():
        assert e.run()["status"] == "running"
        with pytest.raises(BrowserError) as exc:
            e.service.command(
                e.session.id,
                "agent:agent-a",
                BrowserCommand(
                    id="competitor", generation=e.session.control_generation, action="observe"
                ),
                agent=True,
            )
        assert exc.value.code == "automation_busy"

    e.state["after_decision"] = during
    assert e.run()["status"] == "needs_verification"


@pytest.mark.parametrize(
    "mode,reason",
    [("unknown", "outcome_unknown"), ("truncated", "truncated"), ("error", "provider unavailable")],
)
def test_failure_yields_without_replay(execution, mode, reason):
    e = execution
    e.state["mode"] = mode
    result = e.run()
    assert reason in result["reason"]
    assert result["status"] == ("outcome_unknown" if mode == "unknown" else "needs_pa")
    calls = len(e.state["commands"])
    e.run()
    assert len(e.state["commands"]) == calls
    assert len(e.state["provider_calls"]) < 2


def test_low_probability_yields_even_if_vendor_confidence_is_one(execution):
    e = execution
    e.state["probability"] = 0.55
    result = e.run()
    assert result["status"] == "needs_pa"
    assert "ambiguous" in result["reason"]
    assert not e.state["value"]


def test_private_literals_not_sent_to_provider_or_persisted(execution):
    e = execution
    secret = "synthetic-private-value"
    result = e.run(
        goal="Enter the private account value",
        values={"name": {"description": "Account name", "value": secret, "sensitive": True}},
    )
    assert result["status"] == "needs_verification"
    assert e.state["value"] == secret
    assert secret not in json.dumps(e.state["provider_calls"])
    assert secret not in json.dumps(result)
    with e.service.store.connect() as db:
        assert secret not in str(
            [tuple(row) for row in db.execute("SELECT payload FROM browser_runs")]
        )
        assert secret not in str([tuple(row) for row in db.execute("SELECT payload FROM audit")])


def test_cancel_during_decision_and_stop_is_retry_safe(execution):
    e = execution

    def cancel():
        active = e.service.receipt(e.session)["active_run"]
        assert (
            e.service.cancel_run(e.session.id, "user:operator", active["id"])["status"]
            == "cancelled"
        )

    e.state["after_decision"] = cancel
    result = e.run()
    assert result["status"] == "cancelled"
    assert e.service.store.get(e.session.id).controller is None
    assert not any(c["action"] != "observe" for c in e.state["commands"])


def test_missing_values_never_invent_text(execution):
    result = execution.run(values={})
    assert result["status"] == "needs_pa"
    assert not execution.state["value"]


def test_step_limit_is_lowered_and_last_result_requires_verification(execution):
    result = execution.run(max_steps=1)
    assert result["status"] == "needs_pa"
    assert len(result["steps"]) == 1
    assert result["observation_current"]
    assert result["observation"]["elements"][0]["value"] == "Example"


def test_disabled_and_old_endpoints_do_not_dispatch(execution):
    e = execution
    e.service.store.endpoint_features(e.session.id, {})
    with pytest.raises(BrowserError) as exc:
        e.run()
    assert exc.value.code == "endpoint_upgrade_required"
    assert not e.state["provider_calls"]


def test_run_settings_validation_and_defaults():
    assert not Settings().fast_execution.enabled
    assert Settings().fast_execution.max_steps == 20
    assert Settings().fast_execution.timeout_seconds == 30
    for settings in (
        {"max_steps": 41},
        {"timeout_seconds": 61},
        {"min_probability": {"jev": float("nan")}},
    ):
        with pytest.raises(ValidationError):
            Settings(fast_execution=settings)


def test_privacy_policy_denial_never_reaches_provider(execution):
    e = execution

    class Deny:
        def require(self, **kwargs):
            raise PermissionError("Policy denies classification")

    e.classifier.privacy_guard = Deny()
    result = e.run()
    assert result["status"] == "needs_pa"
    assert not e.state["provider_calls"]
    assert not e.state["value"]


def test_revoked_tool_during_classification_prevents_input(execution):
    e = execution
    e.state["after_decision"] = lambda: e.bindings.denied.add("agent-a")
    result = e.run()
    assert result["status"] == "needs_pa"
    assert not e.state["value"]


def test_deadline_during_classification_never_dispatches_input(execution):
    e = execution
    clock = e.service.now
    e.state["after_decision"] = lambda: setattr(e.service, "now", lambda: clock() + 61)
    result = e.run(timeout_seconds=1)
    assert result["status"] == "needs_pa"
    assert not e.state["value"]


def test_waits_are_bounded_by_no_progress_detection(execution, monkeypatch):
    from evander_browser_sessions_addon.execution import BrowserRun

    e = execution
    e.state["mode"] = "wait"
    monkeypatch.setattr("evander_browser_sessions_addon.execution.time.sleep", lambda _: None)
    # BrowserRun's default function was captured at import, so avoid sleeping in this bounded unit probe.
    original = BrowserRun.__init__

    def init(self, service):
        original(self, service, sleep=lambda _: None)

    monkeypatch.setattr(BrowserRun, "__init__", init)
    result = e.run()
    assert result["status"] == "needs_pa"
    assert "without progress" in result["reason"]
    assert len(e.state["provider_calls"]) == 5


@pytest.mark.parametrize("action", ["observe", "click"])
def test_pending_command_restart_recovers_without_replaying(execution, action):
    from evander_browser_sessions_addon.state import Store

    e = execution
    request = RunRequest(
        request_id="interrupted", generation=e.session.control_generation, goal="Save account"
    )
    run, _ = e.service.store.claim_run(
        e.session.id, "agent:agent-a", request, "fingerprint", seconds=1, max_steps=1
    )
    command = BrowserCommand(
        id="command-on-wire",
        generation=e.session.control_generation,
        action=action,
        observation_id="observation" if action == "click" else None,
        ref="node-2" if action == "click" else None,
    )
    e.service.store.record_command(e.session.id, "agent:agent-a", command, run_id=run["id"])
    restarted = Store(e.service.runtime_dir, now=lambda: run["deadline_at"] + 1)
    receipt = restarted.run_get(e.session.id, run["id"])
    assert receipt["status"] == ("outcome_unknown" if action == "click" else "needs_pa")
    assert receipt["steps"] == (
        [{"command_id": "command-on-wire", "action": "click", "ref": "node-2", "outcome": "unknown"}]
        if action == "click"
        else []
    )
    assert not e.state["commands"]
    with restarted.connect() as db:
        assert db.execute("SELECT state FROM commands WHERE id=?", (command.id,)).fetchone()[0] == "unknown"
    assert restarted.claim_run(
        e.session.id, "agent:agent-a", request, "fingerprint", seconds=1, max_steps=1
    ) == (receipt, False)

    # A human can inspect the state and renew control for a fresh objective.
    e.service.store, e.service.now = restarted, restarted.now
    e.service.control(e.session.id, "user:operator", "pause")
    e.service.control(e.session.id, "user:operator", "handback")
    result = e.run(
        request_id="after-inspection",
        generation=restarted.get(e.session.id).control_generation,
    )
    assert result["status"] == "needs_verification"
    assert result["id"] != run["id"]
    assert restarted.run_get(e.session.id, run["id"]) == receipt
    assert all(c["id"] != command.id for c in e.state["commands"])


def test_command_completion_survives_restart_before_loop_progress(execution):
    from evander_browser_sessions_addon.state import Store

    e = execution
    request = RunRequest(
        request_id="interrupted", generation=e.session.control_generation, goal="Save account"
    )
    run, _ = e.service.store.claim_run(
        e.session.id, "agent:agent-a", request, "fingerprint", seconds=1, max_steps=1
    )
    command = BrowserCommand(
        id="saved",
        generation=e.session.control_generation,
        action="click",
        observation_id="observation",
        ref="node-2",
    )
    e.service.store.record_command(e.session.id, "agent:agent-a", command, run_id=run["id"])
    e.service.store.finish_command(command.id, "completed")
    restarted = Store(e.service.runtime_dir, now=lambda: run["deadline_at"] + 1)
    receipt = restarted.run_get(e.session.id, run["id"])
    assert receipt["status"] == "needs_pa"
    assert receipt["steps"] == [
        {"command_id": "saved", "action": "click", "ref": "node-2", "outcome": "completed"}
    ]


def test_other_agent_cannot_run_owned_session(execution):
    e = execution
    with pytest.raises(BrowserError) as exc:
        e.service.run(
            "agent-b",
            e.session.id,
            RunRequest(
                request_id="wrong-owner",
                generation=e.session.control_generation,
                goal="Save account",
            ),
        )
    assert exc.value.code == "not_found"
    assert not e.state["commands"]


def test_human_observation_cannot_invalidate_running_automation_refs(execution):
    e = execution

    def during():
        with pytest.raises(BrowserError) as exc:
            e.service.command(
                e.session.id,
                "user:operator",
                BrowserCommand(
                    id="human-observation",
                    generation=e.session.control_generation,
                    action="observe",
                ),
            )
        assert exc.value.code == "automation_busy"

    e.state["after_decision"] = during
    assert e.run()["status"] == "needs_verification"


def test_completion_returns_a_new_observation_after_decision(execution):
    e = execution

    def change_after_completion_choice():
        if e.state["saved"]:
            e.state["value"] = "Changed while deciding"

    e.state["after_decision"] = change_after_completion_choice
    result = e.run()
    assert result["status"] == "needs_verification"
    assert result["observation_current"]
    assert result["observation"]["elements"][0]["value"] == "Changed while deciding"


def test_legacy_command_migration_is_concurrent_and_preserves_receipts(tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from evander_browser_sessions_addon.state import Store

    root = tmp_path / "addons" / "browser-sessions-addon"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "state.db") as db:
        db.execute(
            "CREATE TABLE commands(id TEXT PRIMARY KEY,session_id TEXT,actor TEXT,state TEXT,at REAL,fingerprint TEXT)"
        )
        db.execute(
            "INSERT INTO commands VALUES('legacy-command','session','agent:a','unknown',0,'fingerprint')"
        )
    from threading import Barrier

    barrier = Barrier(6)

    def initialize(_):
        barrier.wait(timeout=5)
        return Store(tmp_path)

    with ThreadPoolExecutor(max_workers=6) as pool:
        stores = list(pool.map(initialize, range(6)))
    with stores[0].connect() as db:
        assert (
            db.execute("SELECT state FROM commands WHERE id='legacy-command'").fetchone()[0]
            == "unknown"
        )


def test_decision_usage_is_recorded_against_owning_pa_and_run(execution):
    from evander.model_usage.runtime import model_usage_store

    e = execution
    e.classifier.runtime_dir = e.service.runtime_dir
    result = e.run()
    events = model_usage_store(e.service.runtime_dir).list_events(session_id="agent-a")
    assert len(events) == 3
    assert all(event.directory_path == "root/demo" for event in events)
    assert all(event.trace_id == result["id"] for event in events)
    assert all(event.billing_kind == "api" and event.cost_attribution_recorded for event in events)
    assert all(event.input_tokens == 100 and event.output_tokens == 10 for event in events)


def test_clipped_private_value_is_not_forwarded_to_decision_provider(execution):
    e = execution
    e.state["mode"] = "clipped"
    secret = "private-value-" + "Z" * 2000
    result = e.run(
        goal="Enter the private account value",
        values={"name": {"description": "Account name", "value": secret, "sensitive": True}},
    )
    assert result["status"] == "needs_verification"
    assert e.state["value"] == secret
    assert secret[:80] not in json.dumps(e.state["provider_calls"])
    assert secret[:80] not in json.dumps(result)


def test_ui_stop_targets_active_run_and_fences_a_later_start(execution):
    e = execution
    completed = e.run()
    session = e.service.store.get(e.session.id)
    request = RunRequest(
        request_id="next-objective", generation=session.control_generation, goal="Next objective"
    )
    active, _ = e.service.store.claim_run(
        session.id, "agent:agent-a", request, "next-fingerprint", seconds=30, max_steps=1
    )
    # A retry aimed at the old run must not cancel a new objective.
    assert (
        e.service.cancel_run(session.id, "user:operator", completed["id"])["status"]
        == "needs_verification"
    )
    assert e.service.store.run_get(session.id, active["id"])["status"] == "running"
    # The UI's unnamed Stop always targets the active objective under the same lock.
    assert e.service.cancel_run(session.id, "user:operator")["id"] == active["id"]
    assert e.service.store.get(session.id).controller is None
    e.service.cancel_run(session.id, "user:operator")
    with pytest.raises(BrowserError):
        e.service.store.claim_run(
            session.id,
            "agent:agent-a",
            RunRequest(
                request_id="later-objective",
                generation=e.service.store.get(session.id).control_generation,
                goal="Another objective",
            ),
            "later-fingerprint",
            seconds=30,
            max_steps=1,
        )


def test_real_relay_stale_target_yields_without_uncertain_mutation(execution, monkeypatch):
    from evander_browser_sessions_addon.service import HttpPeer

    e = execution
    original = e.service.peer_factory
    attempts = []
    monkeypatch.setenv("BROWSER_TEST_TOKEN", "synthetic-relay-token")

    def peer(base, token):
        def relay(request):
            payload = json.loads(request.content) if request.content else None
            if request.url.path.endswith("/command") and payload["command"]["action"] != "observe":
                attempts.append(payload["command"])
                return httpx.Response(409, json={"code": "stale_target", "error": "private-peer-detail"})
            return httpx.Response(
                200, json=original(base, token).call(request.method, request.url.path, payload)
            )
        return HttpPeer(base, "BROWSER_TEST_TOKEN", transport=httpx.MockTransport(relay))

    e.service.peer_factory = peer
    result = e.run()
    assert result["status"] == "needs_pa"
    assert result["reason"].startswith("stale_target:")
    assert not result["observation_current"]  # The PA must observe the changed page.
    assert len(attempts) == len(e.state["provider_calls"]) == 1
    assert result["steps"] == [
        {"command_id": attempts[0]["id"], "action": "fill", "ref": "node-1", "outcome": "not_applied"}
    ]
    assert e.state["value"] == ""
    assert "private-peer-detail" not in json.dumps(result)
    with e.service.store.connect() as db:
        assert db.execute("SELECT state FROM commands WHERE id=?", (attempts[0]["id"],)).fetchone()[0] == "not_applied"
    assert e.run()["id"] == result["id"]
    assert len(attempts) == 1
