from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_azure_pipeline_deploys_control_plane_before_worker() -> None:
    pipeline_path = ROOT / "azure-pipelines.yml"
    text = pipeline_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)

    assert parsed["trigger"] == ["main"]
    assert parsed["pr"] == "none"
    assert parsed["variables"]["AGENT_SANDBOX_REPO_VERSION"] == "$(Build.SourceVersion)"
    assert parsed["variables"]["CONTROL_PLANE_TARGET"] == "${{ parameters.controlPlaneTarget }}"
    assert parsed["variables"]["WORKER_TARGET"] == "${{ parameters.workerTarget }}"
    assert "ANSIBLE_VAULT_PASSWORD" in text
    assert "'$(ANSIBLE_VAULT_PASSWORD)'" not in text
    assert "unexpanded_vault_macro='$''(ANSIBLE_VAULT_PASSWORD)'" in text
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
    assert parameters["controlPlaneSshKeySecureFile"]["default"] == (
        "truman-control-center_key.pem"
    )
    assert parameters["workerSshKeySecureFile"]["default"] == "truman-worker-1_key.pem"
    assert parameters["azureServiceConnection"]["default"] == "truman-azure-rm"
    assert parameters["controlPlanePublicIp"]["default"] == "52.158.33.185"
    assert parameters["controlPlaneSshNsgResourceGroup"]["default"] == "auto"
    assert parameters["controlPlaneSshNsgName"]["default"] == "auto"
    assert parameters["hostCodexNpmPackage"]["default"] == "@openai/codex"
    assert parameters["forceRunnerImageRebuild"]["default"] is False
    assert parameters["dotnetSdkVersion"]["default"] == "10.0.x"
    assert "DownloadSecureFile@1" in text
    assert "controlPlaneSshKey.secureFilePath" in text
    assert "workerSshKey.secureFilePath" in text
    assert "AGENT_SANDBOX_CONTROL_PLANE_SSH_KEY_FILE" in text
    assert "AGENT_SANDBOX_WORKER_SSH_KEY_FILE" in text
    assert "NodeTool@0" in text
    assert "displayName: Install worker Node.js runtime" in text
    assert "versionSpec: 24.14.0" in text
    assert "displayName: Install worker Playwright runtime" in text
    assert "npm ci --prefix frontend/workflow-editor --no-audit --no-fund" in text
    assert (
        'PLAYWRIGHT_BROWSERS_PATH="$playwright_browsers" \\\n'
        "                      npm exec --prefix frontend/workflow-editor -- playwright install chromium"
    ) in text
    assert "HOST_PLAYWRIGHT_BROWSERS: $(Pipeline.Workspace)/ms-playwright" in text
    assert (
        "HOST_PLAYWRIGHT_NODE_MODULES: "
        "$(Build.SourcesDirectory)/frontend/workflow-editor/node_modules"
    ) in text
    assert "npm install --prefix \"$codex_prefix\" --include=optional --no-audit --no-fund" in text
    assert "codex_platform_version=" in text
    assert "pkg.optionalDependencies && pkg.optionalDependencies[platformPackage]" in text
    assert (
        'npm install --prefix "$codex_prefix" --include=optional --no-audit --no-fund '
        '"${HOST_CODEX_PLATFORM_PACKAGE}@${codex_platform_version}"'
    ) in text
    assert 'export HOST_CODEX_ROOT="$codex_prefix/node_modules/@openai/codex"' in text
    assert "export HOST_CODEX_PLATFORM_PACKAGE" in text
    assert "Installed Codex package did not contain the expected executable" not in text
    assert "HOST_CODEX_NPM_PACKAGE: ${{ parameters.hostCodexNpmPackage }}" in text
    assert 'HOST_CODEX_PLATFORM_PACKAGE: "@openai/codex-linux-x64"' in text
    assert "RUNNER_FORCE_REBUILD: ${{ parameters.forceRunnerImageRebuild }}" in text
    assert 'dotnet_path="$(command -v dotnet || true)"' in text
    assert 'HOST_DOTNET_ROOT="$(dirname "$dotnet_real_path")"' in text
    assert "Expected \\$HOST_DOTNET_ROOT/dotnet to exist and be executable." in text
    assert "UseDotNet@2" in text
    assert "version: ${{ parameters.dotnetSdkVersion }}" in text
    assert "installationPath: $(Pipeline.Workspace)/dotnet" in text
    assert "HOST_DOTNET_ROOT: $(Pipeline.Workspace)/dotnet" in text
    assert 'node_path="$(command -v node || true)"' in text
    assert 'HOST_NODE_ROOT="$(dirname "$(dirname "$node_real_path")")"' in text
    assert "Expected bin/node, bin/npm, and bin/npx under HOST_NODE_ROOT." in text
    assert "PIPELINE_WORKSPACE: $(Pipeline.Workspace)" in text
    assert text.count("AzureCLI@2") == 4
    assert "azureSubscription: ${{ parameters.azureServiceConnection }}" in text
    assert "api.ipify.org" in text
    assert "Allow-AzurePipelines-SSH-Control-$(Build.BuildId)" in text
    assert "Allow-AzurePipelines-SSH-Worker-$(Build.BuildId)" in text
    assert "--source-address-prefixes \"$agent_ip/32\"" in text
    assert "--destination-port-ranges 22" in text
    assert "condition: always()" in text
    assert "controlPlaneSshNsgResourceGroup and controlPlaneSshNsgName" in text
    assert "az network public-ip list" in text
    assert "az network nsg rule create" in text
    assert "az network nsg rule delete" in text

    stage_names = [stage["stage"] for stage in parsed["stages"]]
    assert stage_names == ["DeployControlPlane", "DeployWorkerVm"]
    assert parsed["stages"][1]["dependsOn"] == "DeployControlPlane"

    worker_steps = parsed["stages"][1]["jobs"][0]["strategy"]["runOnce"]["deploy"]["steps"]
    worker_step_names = [step.get("displayName", "") for step in worker_steps]
    assert worker_step_names.index("Install worker Node.js runtime") < worker_step_names.index(
        "Install worker Playwright runtime"
    )
    assert worker_step_names.index("Install worker Playwright runtime") < worker_step_names.index(
        "Build container runner image archive"
    )
    assert worker_step_names.index("Build container runner image archive") < worker_step_names.index(
        "Deploy worker VM with Ansible"
    )
    assert worker_step_names.index("Install worker .NET SDK") < worker_step_names.index(
        "Deploy worker VM with Ansible"
    )
    assert "test -s dist/agent-sandbox-runner.tar.manifest" in text


def test_azure_deploy_repo_matches_pipeline_source_of_truth() -> None:
    pipeline_text = (ROOT / "azure-pipelines.yml").read_text(encoding="utf-8")
    azure_vars = yaml.safe_load(
        (ROOT / "ansible" / "group_vars" / "azure.yml").read_text(encoding="utf-8")
    )

    assert 'agent_sandbox_repo_version=$AGENT_SANDBOX_REPO_VERSION' in pipeline_text
    assert azure_vars["agent_sandbox_repo_url"] == (
        "git@ssh.dev.azure.com:v3/exampleindustry/Example%20Industry%20APP/Truman"
    )
    assert "agent-orchestrator.git" not in azure_vars["agent_sandbox_repo_url"]
    assert azure_vars["agent_sandbox_install_azure_cli"] is True
    assert azure_vars["agent_sandbox_example_app_repo_source"] == (
        "git@ssh.dev.azure.com:v3/exampleindustry/Example%20Industry%20APP/ExampleIndustryAPP"
    )
    assert azure_vars["agent_sandbox_example_app_repo_cache_key"] == (
        "{{ agent_sandbox_example_app_repo_source | hash('sha256') }}"
    )
    assert azure_vars["agent_sandbox_example_app_deploy_demo_script_path"] == (
        "{{ agent_sandbox_runtime_dir }}/repo-cache/"
        "{{ agent_sandbox_example_app_repo_cache_key }}-worktree/"
        "Scripts/Azure/Environment/deploy-demo-environment.sh"
    )
    assert azure_vars["agent_sandbox_shared_secrets_env"][
        "EXAMPLE_APP_DEPLOY_DEMO_SCRIPT_PATH"
    ] == "{{ agent_sandbox_example_app_deploy_demo_script_path }}"
    assert (
        azure_vars["agent_sandbox_shared_secrets_env"]["AZURE_DEVOPS_DEPLOY_PAT"]
        == "{{ vault_azure_devops_pat }}"
    )


def test_azure_inventory_uses_pipeline_supplied_ssh_key_paths() -> None:
    inventory_path = ROOT / "ansible" / "inventory" / "azure.yml"
    text = inventory_path.read_text(encoding="utf-8")

    assert "/home/jonathan" not in text
    assert "AGENT_SANDBOX_CONTROL_PLANE_SSH_KEY_FILE" in text
    assert "AGENT_SANDBOX_WORKER_SSH_KEY_FILE" in text
    assert "agent_sandbox_control_plane_ssh_private_key_file" in text
    assert "agent_sandbox_worker_ssh_private_key_file" in text
    assert "ProxyCommand" in text
    assert "StrictHostKeyChecking=accept-new" in text
    assert "IdentitiesOnly=yes" in text
