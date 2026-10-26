"""Exercise the worker playbook's optional-service selection and deployment contract."""

from pathlib import Path

from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / "ansible/roles/resource_cleanup_runtime/tasks/main.yml").read_text())
ENV = NativeEnvironment(undefined=StrictUndefined)


def render(value, **context):
    return ENV.from_string(value).render(**context)


def conditions(task, context):
    expressions = task.get("when", [])
    if isinstance(expressions, str):
        expressions = [expressions]
    return all(render("{{ " + expression + " }}", **context) for expression in expressions)


@pytest.mark.parametrize(
    "status,selected",
    [
        (None, False),
        ("static", True),
        ("enabled", True),
        ("disabled", True),
        ("masked", False),
        ("masked-runtime", False),
    ],
)
@pytest.mark.parametrize("addon", ["rabbitmq", "sqlserver", "test-fixtures"])
def test_deployment_selects_only_provisioned_unmasked_cleanup(addon, status, selected):
    task = next(
        task for task in TASKS if task.get("loop") == ["rabbitmq", "sqlserver", "test-fixtures"]
    )
    services = {} if status is None else {f"evander-{addon}-reap.service": {"status": status}}
    context = {
        "item": addon,
        "ansible_facts": {"services": services},
        "evander_resource_cleanup_addons": [],
    }
    assert conditions(task, context) is selected
    if selected:
        assert render(
            task["ansible.builtin.set_fact"]["evander_resource_cleanup_addons"], **context
        ) == [addon]


def test_worker_deployment_refreshes_packages_before_migration_and_verifies_afterwards():
    play = yaml.safe_load((ROOT / "ansible/worker.yml").read_text())[0]
    roles = [item["role"] for item in play["roles"]]
    assert (
        roles.index("python_env")
        < roles.index("resource_cleanup_runtime")
        < roles.index("container_worker_verification")
    )
    block = next(task["block"] for task in TASKS if "block" in task)
    install = next(
        task
        for task in block
        if task.get("ansible.builtin.command", {}).get("argv", [None])[0] == "uv"
    )
    migrate = next(
        task
        for task in block
        if task.get("ansible.builtin.command", {}).get("argv")
        == "{{ evander_resource_cleanup_migration_command }}"
    )
    verify = next(task for task in block if "block" in task)
    assert block.index(install) < block.index(migrate) < block.index(verify)
    context = {"item": "rabbitmq", "evander_deploy_path": "/reviewed", "evander_run_user": "worker"}
    assert [render(value, **context) for value in install["ansible.builtin.command"]["argv"]] == [
        "uv",
        "pip",
        "install",
        "--python",
        "/reviewed/.venv/bin/python",
        "--no-deps",
        "-e",
        "./packages/rabbitmq-addon",
    ]
    initialize = next(
        task
        for task in block
        if "evander_resource_cleanup_migration_command" in task.get("ansible.builtin.set_fact", {})
        and "loop" not in task
    )
    append = next(
        task
        for task in block
        if "evander_resource_cleanup_migration_command" in task.get("ansible.builtin.set_fact", {})
        and "loop" in task
    )
    context["evander_resource_cleanup_migration_command"] = [
        render(value, **context)
        for value in initialize["ansible.builtin.set_fact"][
            "evander_resource_cleanup_migration_command"
        ]
    ]
    command = render(
        append["ansible.builtin.set_fact"]["evander_resource_cleanup_migration_command"], **context
    )
    assert command == [
        "/reviewed/.venv/bin/python",
        "/reviewed/scripts/migrate_resource_cleanup.py",
        "--packages-installed",
        "--unit-source",
        "/reviewed",
        "--python",
        "/reviewed/.venv/bin/python",
        "--addon",
        "rabbitmq",
    ]


@pytest.mark.parametrize("state,restart", [("running", True), ("stopped", False), (None, False)])
def test_only_running_resource_apis_restart(state, restart):
    block = next(task["block"] for task in TASKS if "block" in task)
    task = next(task for task in block if "ansible.builtin.systemd" in task)
    services = {} if state is None else {"evander-rabbitmq-worker.service": {"state": state}}
    assert (
        conditions(task, {"item": "rabbitmq", "ansible_facts": {"services": services}}) is restart
    )


@pytest.mark.parametrize(
    "broken",
    [
        "Type=simple",
        "ActiveState=active",
        "SubState=running",
        "UnitFileState=enabled",
        "NRestarts=0",
        "ExecStart=worker --reap-loop",
    ],
)
def test_deployment_rejects_incomplete_cleanup_activation(broken):
    block = next(task["block"] for task in TASKS if "block" in task)
    checks = next(task["block"] for task in block if "block" in task)
    health = next(check for check in checks if "until" in check)
    lines = [
        "Type=simple",
        "ActiveState=active",
        "SubState=running",
        "UnitFileState=enabled",
        "NRestarts=0",
        "ExecStart=worker --reap-loop",
    ]
    assert all(
        render(
            "{{ " + test + " }}",
            evander_resource_cleanup_health={"stdout_lines": lines, "stdout": "\n".join(lines)},
        )
        for test in health["until"]
    )
    lines.remove(broken)
    assert not all(
        render(
            "{{ " + test + " }}",
            evander_resource_cleanup_health={"stdout_lines": lines, "stdout": "\n".join(lines)},
        )
        for test in health["until"]
    )


@pytest.mark.parametrize("active", [False, True])
def test_deployment_rejects_remaining_enabled_or_active_timers(active):
    block = next(task["block"] for task in TASKS if "block" in task)
    checks = next(task["block"] for task in block if "block" in task)
    for check in (item for item in checks if "failed_when" in item):
        context = {check["register"]: {"rc": 0 if active else 1}}
        assert render("{{ " + check["failed_when"] + " }}", **context) is active


def test_review_revision_guard_covers_deployed_cleanup_contract():
    values = yaml.safe_load((ROOT / "ansible/group_vars/evander_worker_hosts.yml").read_text())
    assert set(values["evander_worker_revision_guard_paths"]) >= {
        "ansible/roles/resource_cleanup_runtime",
        "scripts/migrate_resource_cleanup.py",
        "src/evander/resources",
        "packages/rabbitmq-addon",
        "packages/sqlserver-addon",
        "packages/test-fixtures-addon",
    }
