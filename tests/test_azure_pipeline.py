from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_azure_pipeline_deploys_control_plane_before_worker() -> None:
    pipeline_path = ROOT / "azure-pipelines.yml"
    text = pipeline_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)

    assert parsed["trigger"] == "none"
    assert parsed["pr"] == "none"
    assert parsed["variables"]["AGENT_SANDBOX_REPO_VERSION"] == "$(Build.SourceVersion)"
    assert parsed["variables"]["CONTROL_PLANE_TARGET"] == "${{ parameters.controlPlaneTarget }}"
    assert parsed["variables"]["WORKER_TARGET"] == "${{ parameters.workerTarget }}"
    assert "ANSIBLE_VAULT_PASSWORD" in text
    assert "ansible/inventory/azure.yml" in text
    assert "ansible/site.yml" in text
    assert "scripts/build_agent_sandbox_runner_image.sh" in text
    assert "ansible/worker.yml" in text
    assert '${CONTROL_PLANE_TARGET:-all}" != "all"' in text
    assert '${WORKER_TARGET:-all}" != "all"' in text

    parameters = {parameter["name"]: parameter for parameter in parsed["parameters"]}
    assert parameters["controlPlaneTarget"]["default"] == "all"
    assert parameters["controlPlaneTarget"]["values"] == ["all", "truman-control-plane"]
    assert parameters["workerTarget"]["default"] == "all"
    assert parameters["workerTarget"]["values"] == ["all", "truman-worker-1"]

    stage_names = [stage["stage"] for stage in parsed["stages"]]
    assert stage_names == ["DeployControlPlane", "DeployWorkerVm"]
    assert parsed["stages"][1]["dependsOn"] == "DeployControlPlane"

    worker_steps = parsed["stages"][1]["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
    worker_step_names = [step.get("displayName", "") for step in worker_steps]
    assert worker_step_names.index("Build container runner image archive") < worker_step_names.index(
        "Deploy worker VM with Ansible"
    )
