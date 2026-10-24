"""Deployment contracts for Docker's HTTPS repository behind the private apt proxy."""

from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("endpoint", "allowed"),
    [
        ("packages.microsoft.com:443", True),
        ("download.docker.com:443", True),
        ("download.docker.com:80", False),
        ("download.docker.com:8443", False),
        ("download.docker.com.evil.test:443", False),
        ("other.download.docker.com:443", False),
        ("downloadXdockerXcom:443", False),
        ("packages.microsoft.com.evil.test:443", False),
        ("unrelated.example:443", False),
    ],
)
def test_apt_cache_tunnels_only_exact_package_hosts_on_https(endpoint, allowed):
    defaults = yaml.safe_load((ROOT / "ansible/group_vars/all/defaults.yml").read_text())
    pattern = defaults["evander_apt_cache_passthrough_pattern"]
    assert bool(re.fullmatch(pattern, endpoint)) is allowed


def source_repair_tasks():
    tasks = yaml.safe_load((ROOT / "ansible/roles/worker_apt_proxy/tasks/main.yml").read_text())
    enabled = next(t for t in tasks if t.get("when") == "evander_worker_use_apt_proxy | bool")
    repair = next(t for t in enabled["block"] if "ansible.builtin.replace" in t)
    return enabled["block"], repair


@pytest.mark.parametrize(
    ("before", "expected"),
    [
        (
            "Types: deb\nURIs: http://download.docker.com/linux/ubuntu\nSuites: noble\n"
            "Components: stable\nSigned-by: /etc/apt/keyrings/docker.asc\n",
            "Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: noble\n"
            "Components: stable\nSigned-by: /etc/apt/keyrings/docker.asc\n",
        ),
        (
            "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] http://download.docker.com/linux/ubuntu noble stable\n",
            "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu noble stable\n",
        ),
        (
            "  deb-src http://download.docker.com/linux/debian bookworm stable # package sources\n",
            "  deb-src https://download.docker.com/linux/debian bookworm stable # package sources\n",
        ),
        (
            "URIs:\thttp://download.docker.com/linux/ubuntu/\n",
            "URIs:\thttps://download.docker.com/linux/ubuntu/\n",
        ),
        ("URIs: https://download.docker.com/linux/ubuntu\n", "URIs: https://download.docker.com/linux/ubuntu\n"),
        ("# deb http://download.docker.com/linux/ubuntu noble stable\n", "# deb http://download.docker.com/linux/ubuntu noble stable\n"),
        ("deb http://archive.ubuntu.com/ubuntu noble main\n", "deb http://archive.ubuntu.com/ubuntu noble main\n"),
        ("URIs: http://download.docker.com.evil.test/linux/ubuntu\n", "URIs: http://download.docker.com.evil.test/linux/ubuntu\n"),
        ("URIs: http://download.docker.com/unrelated\n", "URIs: http://download.docker.com/unrelated\n"),
    ],
)
def test_worker_docker_source_repair_preserves_options_and_is_idempotent(before, expected):
    _, task = source_repair_tasks()
    repair = task["ansible.builtin.replace"]
    after = re.sub(repair["regexp"], repair["replace"], before)
    assert after == expected
    assert re.sub(repair["regexp"], repair["replace"], after) == expected


def test_source_repair_covers_both_apt_formats_before_first_refresh():
    block, repair = source_repair_tasks()
    discover = next(t for t in block if "ansible.builtin.find" in t)
    find = discover["ansible.builtin.find"]
    assert find["paths"] == ["/etc/apt", "/etc/apt/sources.list.d"]
    assert set(find["patterns"]) == {"*.sources", "*.list"}
    assert find["file_type"] == "file" and not find["recurse"] and not find["follow"]
    assert block.index(discover) < block.index(repair)
    assert repair["loop"] == "{{ " + discover["register"] + ".files }}"
    assert repair["ansible.builtin.replace"]["path"] == "{{ item.path }}"
    play = yaml.safe_load((ROOT / "ansible/worker.yml").read_text())[0]
    roles = [role["role"] for role in play["roles"]]
    assert roles.index("worker_apt_proxy") < roles.index("host_prereqs")
