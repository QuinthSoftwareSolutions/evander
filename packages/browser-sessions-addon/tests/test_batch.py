"""Guarded plans exercise the real service/SQLite boundaries, not a model stub's claims."""

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from threading import Event
import uuid

import pytest
from pydantic import ValidationError

from evander_browser_sessions_addon.models import BrowserCommand, BrowserError, RunRequest
from evander_browser_sessions_addon.state import Store

pytest_plugins = ["test_execution"]


PLAN = [
    {"action": "fill", "target": {"role": "textbox", "name": "Name"}, "input": "name"},
    {"action": "click", "target": {"role": "button", "name": "Save"}},
]


@pytest.fixture
def batch(execution):
    e = execution
    def no_classifier(_):
        raise AssertionError("Plans must not acquire a classification provider")
    e.bindings.classifier = no_classifier
    def observe():
        return e.service.command(e.session.id, "agent:agent-a", BrowserCommand(
            id=uuid.uuid4().hex, generation=e.session.control_generation,
            action="observe", include_image=False,
        ), agent=True)
    def request(**kwargs):
        observation = kwargs.pop("observation", None) or observe()
        payload = dict(request_id="planned-account", generation=e.session.control_generation,
                       observation_id=observation["observation_id"], goal="Create the supplied account",
                       values={"name": {"description": "Name", "value": "Example"}},
                       completion=None, plan=PLAN)
        payload.update(kwargs)
        return RunRequest(**payload)
    e.observe, e.request = observe, request
    e.execute = lambda r: e.service.run("agent-a", e.session.id, r)
    e.inputs = lambda: [c for c in e.state["commands"] if c["action"] != "observe"]
    return e


def test_plan_completes_without_provider_and_retries_only_read_receipt(batch):
    e = batch
    request = e.request()
    receipt = e.execute(request)
    assert receipt["status"] == "needs_verification" and receipt["observation_current"]
    assert e.state["value"] == "Example" and e.state["saved"]
    assert [s["step_index"] for s in receipt["steps"]] == [0, 1]
    assert [s["outcome"] for s in receipt["steps"]] == ["completed", "completed"]
    assert not e.state["provider_calls"] and receipt["mode"] == "plan"
    before = len(e.state["commands"])
    retry = e.execute(request)
    assert retry["id"] == receipt["id"] and retry["steps"] == receipt["steps"]
    assert "observation" not in retry and len(e.state["commands"]) == before
    changed = request.model_copy(update={"goal": "A different objective"})
    with pytest.raises(BrowserError) as error:
        e.execute(changed)
    assert error.value.code == "request_conflict"


def test_completion_already_true_does_not_skip_explicit_actions(batch):
    e = batch
    e.state["value"] = "Example"
    result = e.execute(e.request(completion={"kind": "value", "target": {"role": "textbox", "name": "Name"}, "input": "name"}))
    assert result["status"] == "needs_verification" and e.state["saved"]
    assert [c["action"] for c in e.inputs()] == ["fill", "click"]


def test_repeated_target_allows_confirmed_own_value_changes(batch):
    e = batch
    result = e.execute(e.request(
        values={"name": {"description": "First", "value": "First"}, "second": {"description": "Second", "value": "Second"}},
        plan=[PLAN[0], {**PLAN[0], "input": "second"}],
    ))
    assert result["status"] == "needs_verification"
    assert e.state["value"] == "Second" and len(e.inputs()) == 2 and not e.state["saved"]


@pytest.mark.parametrize("change", ["value", "selection", "duplicate", "truncated"])
def test_initial_owner_snapshot_is_a_mutable_state_fence(batch, change):
    e = batch
    request = e.request()
    if change == "value":
        e.state["value"] = "Changed externally"
    elif change == "selection":
        e.state["observation_hook"] = lambda o: o["elements"][1].update(selected=True)
    elif change == "duplicate":
        e.state["extra_elements"] = [{"ref": "duplicate", "role": "textbox", "name": "Name", "value": ""}]
    else:
        e.state["mode"] = "truncated"
    result = e.execute(request)
    assert result["status"] == "needs_pa" and not e.inputs()
    assert not e.state["saved"]


def test_intervening_command_invalidates_grounding_even_when_ui_is_unchanged(batch):
    e = batch
    request = e.request()
    e.service.command(e.session.id, "agent:agent-a", BrowserCommand(
        id="intervening-hover", generation=e.session.control_generation,
        action="hover", observation_id=request.observation_id, ref="node-2",
    ), agent=True)
    with pytest.raises(BrowserError) as error:
        e.execute(request)
    assert error.value.code == "observation_required" and not e.state["value"]


@pytest.mark.parametrize("missing", ["page_id", "page_epoch"])
def test_endpoint_must_prove_page_and_document_identity(batch, missing):
    e = batch
    e.state["observation_hook"] = lambda o: o.pop(missing)
    request = e.request()
    with pytest.raises(BrowserError) as error:
        e.execute(request)
    assert error.value.code == "batch_identity_required" and not e.inputs()


@pytest.mark.parametrize("change", ["page_id", "page_epoch", "url", "target", "truncated", "control", "grant"])
def test_changes_after_first_action_stop_remaining_plan(batch, change):
    e = batch
    def changed(o):
        if not e.state["value"]:
            return
        if change in {"page_id", "page_epoch", "url"}:
            o[change] += "-changed"
        elif change == "target":
            o["elements"][1]["disabled"] = True
        elif change == "truncated":
            o["truncated"] = True
        elif change == "control":
            e.service.control(e.session.id, "user:operator", "claim")
        else:
            e.bindings.denied.add("agent-a")
    e.state["observation_hook"] = changed
    result = e.execute(e.request())
    assert result["status"] in {"needs_pa", "cancelled", "outcome_unknown"}
    if change in {"control", "grant"}:
        assert not result["observation_current"]
    assert e.state["value"] == "Example" and not e.state["saved"]
    assert [c["action"] for c in e.inputs()] == ["fill"]


def test_entire_plan_is_validated_before_first_input(batch):
    e = batch
    invalid = [PLAN[0], {"action": "fill", "target": PLAN[1]["target"], "input": "name"}]
    result = e.execute(e.request(plan=invalid))
    assert result["status"] == "needs_pa" and not e.inputs() and not e.state["value"]


@pytest.mark.parametrize("kind", ["combobox", "dialog", "alert", "new_control", "replaced_ref"])
def test_unplanned_context_changes_cannot_reauthorize_a_later_submit(batch, kind):
    e = batch
    if kind == "combobox":
        e.state["extra_elements"] = [{"ref": "account", "role": "combobox", "name": "Account", "value": "Account A"}]
    def change(observation):
        if not e.state["value"]:
            return
        if kind == "combobox":
            observation["elements"][-1]["value"] = "Account B"
        elif kind == "dialog":
            observation["elements"][1]["context"] = [{"role": "dialog", "name": "Other account"}]
        elif kind == "alert":
            observation["elements"][2]["name"] = "Account changed externally"
        elif kind == "new_control":
            observation["elements"].append({"ref": "new", "role": "checkbox", "name": "Additional option"})
        else:
            observation["elements"][1]["ref"] = "replacement-button"
    e.state["observation_hook"] = change
    result = e.execute(e.request())
    assert result["status"] == "needs_pa" and not e.state["saved"]
    assert [c["action"] for c in e.inputs()] == ["fill"]


def test_private_values_never_enter_receipts_or_audit(batch):
    e = batch
    secret = "synthetic-sensitive /?@ value"
    result = e.execute(e.request(plan=PLAN[:1], values={"name": {"description": "Private name", "value": secret, "sensitive": True}}))
    assert result["status"] == "needs_verification" and e.state["value"] == secret
    assert secret not in json.dumps(result)
    with e.service.store.connect() as db:
        for table in ["browser_runs", "audit", "private_sessions"]:
            assert secret not in str([tuple(r) for r in db.execute("SELECT payload FROM " + table)])


@pytest.mark.parametrize("action", ["fill", "type"])
def test_unexpected_edited_value_is_not_accepted_as_a_confirmed_own_change(batch, action):
    e = batch
    def overwrite(observation):
        if e.state["value"]:
            observation["elements"][0]["value"] = "Unexpected external value"
    e.state["observation_hook"] = overwrite
    result = e.execute(e.request(plan=[{**PLAN[0], "action": action}, PLAN[1]]))
    assert result["status"] == "needs_pa" and not e.state["saved"]
    assert [c["action"] for c in e.inputs()] == [action]


def test_successful_typing_confirms_the_exact_append_before_the_next_action(batch):
    e = batch
    e.state["value"] = "Existing prefix: "
    result = e.execute(e.request(plan=[{**PLAN[0], "action": "type"}, PLAN[1]]))
    assert result["status"] == "needs_verification" and e.state["saved"]
    assert e.state["value"] == "Existing prefix: Example"


@pytest.mark.parametrize("applied", [True, False])
def test_checkbox_transition_must_be_observed_before_later_input(batch, applied):
    e = batch
    def checkbox(observation):
        observation["elements"][0].update(role="checkbox", name="Active", checked=bool(e.state["saved"] and applied))
        observation["elements"][2]["name"] = ""  # This click is a checkbox, not a saved-result action.
    e.state["observation_hook"] = checkbox
    result = e.execute(e.request(plan=[
        {"action": "click", "target": {"role": "checkbox", "name": "Active"}}, PLAN[1],
    ]))
    assert result["status"] == ("needs_verification" if applied else "needs_pa")
    assert len(e.inputs()) == (2 if applied else 1)


@pytest.mark.parametrize("checkpoint", [True, False])
def test_changed_keyboard_value_needs_an_explicit_state_checkpoint(batch, checkpoint):
    e = batch
    def dropdown(observation):
        moved = any(c["action"] == "key" for c in e.inputs())
        observation["elements"][0].update(role="combobox", name="Category", value="South" if moved else "North")
    e.state["observation_hook"] = dropdown
    checkpoints = [{
        "id": "south-selected", "after": {"action": "key", "ref": "node-1", "key": "ArrowDown"},
        "condition": {"kind": "value", "target": {"ref": "node-1"}, "input": "name"},
    }] if checkpoint else []
    result = e.execute(e.request(
        plan=[{"action": "key", "target": {"role": "combobox", "name": "Category"}, "key": "ArrowDown"}, PLAN[1]],
        values={"name": {"description": "Selected category", "value": "South"}}, checkpoints=checkpoints,
    ))
    assert result["status"] == ("needs_verification" if checkpoint else "needs_pa")
    assert len(e.inputs()) == (2 if checkpoint else 1)


def test_checkpoint_state_must_still_hold_on_the_observation_before_later_input(batch):
    e = batch
    reads_after_key = []
    def checkbox(observation):
        if any(c["action"] == "key" for c in e.inputs()):
            reads_after_key.append(True)
        observation["elements"][0].update(role="checkbox", name="Active", checked=len(reads_after_key) == 1)
    e.state["observation_hook"] = checkbox
    result = e.execute(e.request(
        plan=[{"action": "key", "target": {"ref": "node-1"}, "key": "Space"}, PLAN[1]],
        checkpoints=[{
            "id": "active", "after": {"action": "key", "ref": "node-1", "key": "Space"},
            "condition": {"kind": "checked", "target": {"ref": "node-1"}, "expected": True},
        }],
    ))
    assert result["checkpoints"][0]["status"] == "satisfied"
    assert result["status"] == "needs_pa" and not e.state["saved"]
    assert [c["action"] for c in e.inputs()] == ["key"]


@pytest.mark.parametrize("restriction", ["disabled", "step_cap", "old_endpoint", "expired_observation"])
def test_plan_feature_caps_and_owner_observation_age_are_checked_before_input(batch, restriction):
    e = batch
    request = e.request()
    if restriction in {"disabled", "step_cap"}:
        revision, settings = e.service.store.settings("root")
        if restriction == "disabled":
            settings.fast_execution.enabled = False
        else:
            settings.fast_execution.max_steps = 1
        e.service.store.save_settings("root", revision, settings)
    elif restriction == "old_endpoint":
        e.service.store.endpoint_features(e.session.id, {})
    else:
        original = e.service.store.now
        e.service.store.now = lambda: original() + 31
    with pytest.raises(BrowserError) as error:
        e.execute(request)
    assert error.value.code == {"disabled": "fast_execution_disabled", "step_cap": "plan_budget",
                               "old_endpoint": "endpoint_upgrade_required", "expired_observation": "observation_required"}[restriction]
    assert not e.inputs() and not e.service.store.run_status(e.session.id)["active_run"]


def test_observation_finishing_late_cannot_restore_grounding_after_a_newer_command(batch):
    e = batch
    store = e.service.store
    generation = e.session.control_generation
    read = BrowserCommand(id="delayed-observe", generation=generation, action="observe")
    store.record_command(e.session.id, "agent:agent-a", read)
    store.record_command(e.session.id, "agent:agent-a", BrowserCommand(
        id="newer-input", generation=generation, action="hover", ref="node-2", observation_id="current",
    ))
    store.finish_command(read.id, "completed", observation={
        "id": "obsolete", "actor": "agent:agent-a", "generation": generation,
        "observed_at": e.service.now(), "fingerprint": "old-digest",
    })
    assert "batch_observation" not in store.private(e.session.id)


def test_stale_explicit_reference_never_falls_back_to_a_matching_label(batch):
    e = batch
    result = e.execute(e.request(plan=[{
        **PLAN[0], "target": {"ref": "obsolete", "role": "textbox", "name": "Name"},
    }]))
    assert result["status"] == "needs_pa" and not e.inputs()


def test_checkpoint_receipts_are_durable_before_later_actions_and_retries(batch):
    e = batch
    request = e.request(checkpoints=[{
        "id": "name-confirmed", "after": {"action": "fill", "input": "name"},
        "condition": {"kind": "value", "target": {"ref": "node-1"}, "input": "name"},
    }])
    result = e.execute(request)
    assert result["status"] == "needs_verification"
    assert result["checkpoints"][0]["id"] == "name-confirmed"
    assert result["checkpoints"][0]["status"] == "satisfied"
    assert e.execute(request)["checkpoints"] == result["checkpoints"]


def test_unreachable_checkpoint_rejects_the_whole_plan_before_input(batch):
    e = batch
    result = e.execute(e.request(checkpoints=[{
        "id": "unreachable", "after": {"action": "click", "ref": "another-button"},
        "condition": {"kind": "present", "target": {"ref": "node-1"}},
    }]))
    assert result["status"] == "needs_pa" and not e.inputs()


@pytest.mark.parametrize("fault", ["peer_setup", "finish_persistence"])
def test_dispatch_errors_retire_pending_commands_without_replay(batch, monkeypatch, fault):
    e = batch
    request = e.request()
    if fault == "peer_setup":
        record, peer = e.service.store.record_command, e.service.relay_peer
        armed = []
        def record_command(*args, **kwargs):
            record(*args, **kwargs)
            if not args[2].observing:
                armed.append(True)
        def relay(session):
            if armed:
                armed.clear()
                raise RuntimeError("Synthetic credential/connection failure")
            return peer(session)
        monkeypatch.setattr(e.service.store, "record_command", record_command)
        monkeypatch.setattr(e.service, "relay_peer", relay)
    else:
        finish = e.service.store.finish_command
        failed = []
        def finish_command(command_id, status, **kwargs):
            with e.service.store.connect() as db:
                input_command = not db.execute("SELECT observing FROM commands WHERE id=?", (command_id,)).fetchone()[0]
            if input_command and not failed:
                failed.append(True)
                raise RuntimeError("Synthetic receipt persistence failure")
            return finish(command_id, status, **kwargs)
        monkeypatch.setattr(e.service.store, "finish_command", finish_command)
    result = e.execute(request)
    assert result["status"] == "outcome_unknown" and result["steps"][0]["outcome"] == "unknown"
    with e.service.store.connect() as db:
        assert not db.execute("SELECT 1 FROM commands WHERE state='submitted'").fetchone()
    count = len(e.inputs())
    assert e.execute(request)["id"] == result["id"] and len(e.inputs()) == count
    e.service.store.finish_command(result["steps"][0]["command_id"], "completed")
    assert e.service.store.run_get(e.session.id, result["id"])["steps"] == result["steps"]
    if fault == "peer_setup":
        monkeypatch.setattr(e.service.store, "record_command", record)
        monkeypatch.setattr(e.service, "relay_peer", peer)
    followup = e.execute(e.request(request_id="explicit-new-plan", plan=PLAN[:1]))
    assert followup["status"] == "needs_verification"  # No permanent submitted-command blocker.


def test_concurrent_identical_requests_share_one_execution(batch):
    e = batch
    request = e.request()
    entered, release = Event(), Event()
    def block(_):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
    e.state["observation_hook"] = block
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(e.execute, request)
        try:
            assert entered.wait(3)
            duplicate = pool.submit(e.execute, request).result(timeout=3)
            assert duplicate["status"] == "running"
        finally:
            release.set()
        result = first.result(timeout=5)
    assert duplicate["id"] == result["id"]
    assert result["status"] == "needs_verification" and len(e.inputs()) == 2


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Process-death qualification uses fork")
@pytest.mark.parametrize("phase", ["before_send", "after_send", "after_commit"])
def test_process_death_never_resumes_remaining_actions(batch, tmp_path, phase):
    e = batch
    request = e.request()
    applied = tmp_path / "applied.txt"
    def child():
        if phase == "before_send":
            peer = e.service.relay_peer
            def crash_before(session):
                with e.service.store.connect() as db:
                    pending = db.execute("SELECT 1 FROM commands WHERE observing=0 AND state='submitted'").fetchone()
                if pending:
                    os._exit(53)
                return peer(session)
            e.service.relay_peer = crash_before
        else:
            finish = e.service.store.finish_command
            def crash_finish(command_id, status, **kwargs):
                with e.service.store.connect() as db:
                    input_command = not db.execute("SELECT observing FROM commands WHERE id=?", (command_id,)).fetchone()[0]
                if input_command and status == "completed":
                    applied.write_text(e.state["value"])
                    if phase == "after_commit":
                        finish(command_id, status, **kwargs)
                    os._exit(53)
                return finish(command_id, status, **kwargs)
            e.service.store.finish_command = crash_finish
        e.execute(request)
        os._exit(54)
    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(6)
    if process.is_alive():
        process.kill()
        process.join(2)
    assert process.exitcode == 53
    assert applied.exists() == (phase != "before_send")
    if applied.exists():
        assert applied.read_text() == "Example"
    before = len(e.state["commands"])
    pending = e.execute(request)
    assert pending["status"] == "running"  # Another process must not steal the live deadline.
    clock = e.service.now
    restarted = Store(e.service.runtime_dir, now=lambda: clock() + 10)
    e.service.store, e.service.now = restarted, restarted.now
    recovered = e.execute(request)
    assert recovered["status"] == ("needs_pa" if phase == "after_commit" else "outcome_unknown")
    assert [s["outcome"] for s in recovered["steps"]] == (["completed"] if phase == "after_commit" else ["unknown"])
    assert len(e.state["commands"]) == before and not e.state["saved"]


@pytest.mark.parametrize("patch", [
    {"plan": []}, {"plan": PLAN * 5}, {"timeout_seconds": 6},
    {"observation_id": None}, {"plan": [{**PLAN[0], "input": "missing"}]},
    {"plan": [{"action": "navigate", "target": {"ref": "node-1"}}]},
])
def test_plan_contract_rejects_invalid_or_unbounded_requests(batch, patch):
    with pytest.raises(ValidationError):
        batch.request(**patch)


def test_observation_exposes_supported_operations_for_native_and_readonly_controls(batch):
    e = batch
    e.state["extra_elements"] = [
        {"ref":"date","role":"date","name":"Schedule","native_control":"date","native_input_verified":True,"value_format":"YYYY-MM-DD","value":"","date_parts":{"year":0,"month":0,"day":0}},
        {"ref":"number","role":"spinbutton","name":"Quantity","editable":"plaintext","value":"2"},
        {"ref":"choice","role":"combobox","name":"Region","native_control":"select","options":[{"name":"North","disabled":False}]},
        {"ref":"readonly","role":"textbox","name":"Computed","readonly":True},
        {"ref":"unsupported","role":"date","name":"Other date"},
    ]
    observed = {e["ref"]:e for e in e.observe()["elements"]}
    assert "fill" in observed["date"]["allowed_actions"] and "type" not in observed["date"]["allowed_actions"]
    assert {"fill","type"} <= set(observed["number"]["allowed_actions"])
    assert "select" in observed["choice"]["allowed_actions"]
    assert not {"fill","type"} & set(observed["choice"]["allowed_actions"])
    assert not {"fill","type"} & set(observed["readonly"]["allowed_actions"])
    assert "fill" not in observed["unsupported"]["allowed_actions"]


@pytest.mark.parametrize("kind",["date","missing_option","duplicate_option","disabled_option"])
def test_invalid_later_native_input_rejects_the_entire_plan_before_first_input(batch,kind):
    e=batch
    if kind=="date":
        target={"ref":"native","role":"date","name":"Schedule","native_control":"date","native_input_verified":True,"value_format":"YYYY-MM-DD","value":""}
        action,value="fill","2027-02-29"
    else:
        options=[{"name":"North","disabled":kind=="disabled_option"}]
        if kind=="duplicate_option":
            options.append({"name":"North","disabled":False})
        target={"ref":"native","role":"combobox","name":"Region","native_control":"select","options":options}
        action,value="select","Missing" if kind=="missing_option" else "North"
    e.state["extra_elements"]=[target]
    result=e.execute(e.request(
        values={"name":{"description":"Name","value":"Example"},"native":{"description":"Native value","value":value}},
        plan=[{"action":"fill","target":{"ref":"node-1"},"input":"name"},
              {"action":action,"target":{"ref":"native"},"input":"native"}],
    ))
    assert result["status"]=="needs_pa" and not e.inputs() and not e.state["value"]


def test_partial_date_edits_invalidate_the_owner_snapshot_even_with_no_complete_date(batch):
    e=batch
    date={"ref":"date","role":"date","name":"Schedule","native_control":"date","native_input_verified":True,"value_format":"YYYY-MM-DD","value":"","date_parts":{"year":0,"month":0,"day":0}}
    e.state["extra_elements"]=[date]
    request=e.request()
    date["date_parts"]["month"]=3
    result=e.execute(request)
    assert result["status"]=="needs_pa" and not e.inputs()


def test_sensitive_date_parts_are_redacted_along_with_the_canonical_value(batch):
    e=batch
    def date(observation):
        parts=list(map(int,e.state["value"].split("-"))) if e.state["value"] else [0,0,0]
        observation["elements"][0].update(role="date",native_control="date",native_input_verified=True,value_format="YYYY-MM-DD",date_parts=dict(zip(("year","month","day"),parts)))
    e.state["observation_hook"]=date
    result=e.execute(e.request(
        values={"name":{"description":"Private date","value":"2027-03-18","sensitive":True}},
        plan=[{"action":"fill","target":{"ref":"node-1"},"input":"name"}],
    ))
    assert result["status"]=="needs_verification" and e.state["value"]=="2027-03-18"
    assert "2027-03-18" not in json.dumps(result)
    assert result["observation"]["elements"][0]["date_parts"]=="[redacted]"


def test_action_schema_requires_the_fields_needed_by_each_operation(batch):
    schema=RunRequest.model_json_schema()
    assert {"target","input"} <= set(schema["$defs"]["PlannedInput"]["required"])
    assert {"target","key"} <= set(schema["$defs"]["PlannedKey"]["required"])
    with pytest.raises(ValidationError):
        batch.request(plan=[{"action":"key","key":"Enter"}])
    with pytest.raises(ValidationError):
        batch.request(plan=[{"action":"fill","target":{"ref":"node-1"}}])


@pytest.mark.parametrize("constraint,value",[
    ({"min":"2020-06-15"},"2020-01-01"),
    ({"max":"2030-06-15"},"2030-12-31"),
    ({"step":"2","step_base":"2027-03-18"},"2027-03-19"),
])
def test_known_native_date_constraints_reject_a_later_step_before_any_input(batch,constraint,value):
    e=batch
    e.state["extra_elements"]=[{"ref":"native","role":"date","name":"Schedule","native_control":"date","native_input_verified":True,
                                "value_format":"YYYY-MM-DD","value":"",**constraint}]
    result=e.execute(e.request(
        values={"name":{"description":"Name","value":"Example"},"date":{"description":"Date","value":value}},
        plan=[{"action":"fill","target":{"ref":"node-1"},"input":"name"},
              {"action":"fill","target":{"ref":"native"},"input":"date"}],
    ))
    assert result["status"]=="needs_pa" and not e.inputs()


@pytest.mark.parametrize("field,value",[
    ("min","2027-01-01"),("max","2029-01-01"),("step","2"),("step_base","2027-03-18"),
])
def test_native_date_constraint_changes_invalidate_grounding_before_unrelated_input(batch,field,value):
    e=batch
    date={"ref":"native","role":"date","name":"Schedule","native_control":"date","native_input_verified":True,
          "value_format":"YYYY-MM-DD","value":"","min":"2020-01-01","max":"2030-12-31","step":"1","step_base":"2020-01-01"}
    e.state["extra_elements"]=[date]
    request=e.request()
    date[field]=value
    result=e.execute(request)
    assert result["status"]=="needs_pa" and not e.inputs() and not e.state["value"]
