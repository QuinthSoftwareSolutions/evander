from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_git_provider_prepare_remote_converts_azure_ssh_to_https_with_pat() -> None:
    helper = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "git_provider_auth.sh"
    env = os.environ.copy()
    env["AZURE_DEVOPS_PR_PAT"] = "test-pat"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f"source {helper} && "
                "git_provider_prepare_remote "
                "'git@ssh.dev.azure.com:v3/exampleindustry/Example%20Industry%20APP/ExampleIndustryAPP' && "
                'printf \'%s\n%s\n\' "$GIT_PROVIDER_REMOTE_URL" "$GIT_PROVIDER_HTTP_EXTRA_HEADER"'
            ),
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    remote_url, auth_header = result.stdout.splitlines()
    assert (
        remote_url
        == "https://dev.azure.com/exampleindustry/Example%20Industry%20APP/_git/ExampleIndustryAPP"
    )
    assert auth_header.startswith("Authorization: Basic ")


def test_git_provider_prepare_remote_leaves_azure_ssh_url_unchanged_without_pat() -> None:
    helper = Path(__file__).resolve().parents[1] / "scripts" / "lib" / "git_provider_auth.sh"
    env = os.environ.copy()
    env.pop("AZURE_DEVOPS_PR_PAT", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f"source {helper} && "
                "git_provider_prepare_remote "
                "'git@ssh.dev.azure.com:v3/exampleindustry/Example%20Industry%20APP/ExampleIndustryAPP' && "
                'printf \'%s\n%s\n\' "$GIT_PROVIDER_REMOTE_URL" "$GIT_PROVIDER_HTTP_EXTRA_HEADER"'
            ),
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    remote_url, auth_header = result.stdout.splitlines()
    assert (
        remote_url
        == "git@ssh.dev.azure.com:v3/exampleindustry/Example%20Industry%20APP/ExampleIndustryAPP"
    )
    assert auth_header == ""
