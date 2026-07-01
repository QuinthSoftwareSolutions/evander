from __future__ import annotations

import base64
from pathlib import Path


def module_project_root() -> Path:
    return Path(__file__).resolve().parents[4]


def guest_script_helper_b64(project_dir: Path, helper_name: str) -> str:
    helper_path = project_dir / "scripts" / "guest" / helper_name
    if not helper_path.exists():
        helper_path = module_project_root() / "scripts" / "guest" / helper_name
    return base64.b64encode(helper_path.read_bytes()).decode("ascii")


def guest_agent_pr_note_helper_b64(project_dir: Path) -> str:
    return guest_script_helper_b64(project_dir, "agent_pr_note.sh")


def guest_push_agent_helper_b64(project_dir: Path) -> str:
    return guest_script_helper_b64(project_dir, "push_agent.sh")


def guest_sync_base_helper_b64(project_dir: Path) -> str:
    return guest_script_helper_b64(project_dir, "sync_base.sh")


_module_project_root = module_project_root
_guest_script_helper_b64 = guest_script_helper_b64
_guest_agent_pr_note_helper_b64 = guest_agent_pr_note_helper_b64
_guest_push_agent_helper_b64 = guest_push_agent_helper_b64
_guest_sync_base_helper_b64 = guest_sync_base_helper_b64
