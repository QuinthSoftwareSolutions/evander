#!/usr/bin/env python3
"""Offline, reproducible prompt/read comparison using synthetic Team fixtures.

No runtime repositories, network requests, model calls, or operator writes.
The baseline is trusted local repository code at an immutable commit, not a
historical token replay. Character/byte results must not be called billed savings.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types
from typing import Any

from evander_team_addon import providers
from evander_team_addon.services.wake_prompt_context import (
    build_compact_wake_context,
    build_dispatch_summary,
    compact_context_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = "243aed9f"
FIXTURES = ROOT / "tests/fixtures/team_context/representative.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, timeout=15
    ).stdout


def _baseline_module(commit: str, path: str, name: str) -> types.ModuleType:
    source = _git("show", f"{commit}:{path}")
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / path)
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _size(value: str) -> dict[str, int]:
    return {"characters": len(value), "utf8_bytes": len(value.encode("utf-8"))}


def _duplicates(value: Any) -> int:
    """Repeated leaf-string bytes, minimum 40 characters; excludes JSON keys."""
    strings: Counter[str] = Counter()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and len(item) >= 40:
            strings[item] += 1

    visit(value)
    return sum((count - 1) * len(text.encode()) for text, count in strings.items())


def fixture_dispatch(fixture: dict[str, Any]) -> dict[str, Any]:
    """Expand the representative full read without storing its records twice."""
    lease = fixture["lease"]
    full = {
        key: deepcopy(lease[key])
        for key in (
            "dispatch",
            "tasks",
            "projects",
            "inbox",
            "blockers",
            "reviews",
            "external_source_evidence_snapshots",
        )
    }
    full["git_work_contexts"] = [
        deepcopy(row)
        for repo in lease["project_repos"]
        for row in repo.get("git_work_contexts", [])
    ]
    for task in full["tasks"]:
        task["git_work_contexts"] = [
            deepcopy(row) for row in full["git_work_contexts"] if row["task_id"] == task["id"]
        ]
    return full


def compare(*, baseline: str = DEFAULT_BASELINE, fixtures_path: Path = FIXTURES) -> dict[str, Any]:
    commit = _git("rev-parse", "--verify", "--end-of-options", f"{baseline}^{{commit}}").strip()
    old_wake = _baseline_module(
        commit,
        "src/evander_team_addon/services/wake_prompt_context.py",
        "evander_team_addon.services._comparison_baseline_wake",
    )
    old_provider = _baseline_module(
        commit,
        "src/evander_team_addon/providers.py",
        "evander_team_addon._comparison_baseline_providers",
    )
    old_mcp = _baseline_module(
        commit, "src/evander_team_addon/mcp.py", "evander_team_addon._comparison_baseline_mcp"
    )
    old_provider.build_compact_wake_context = old_wake.build_compact_wake_context
    old_provider.READ_TEAM_MCP_TOOL_NAMES = old_mcp.READ_TEAM_MCP_TOOL_NAMES
    data = json.loads(fixtures_path.read_text())
    rows = []
    for fixture in data["fixtures"]:
        full = fixture_dispatch(fixture)
        full_json = compact_context_json(full)
        summary = build_dispatch_summary(full)
        summary_json = compact_context_json(summary)
        retrieved = []
        for reference in fixture["targeted_reads"]:
            record = next(
                row for row in full[reference["collection"]] if row["id"] == reference["id"]
            )
            retrieved.append(
                {
                    **reference,
                    "tool": summary["retrieval"][reference["collection"]]["tool"],
                    "utf8_bytes": len(compact_context_json(record).encode()),
                }
            )
        retrieval_bytes = sum(row["utf8_bytes"] for row in retrieved)
        all_detail_bytes = sum(
            len(compact_context_json(row).encode())
            for key in summary["retrieval"]
            for row in full.get(key, [])
        )
        for fresh in (True, False):
            lease = {**fixture["lease"], "has_prior_successful_wake": not fresh}
            # Model a saved lineage thread for the retained management examples.
            if not fresh and lease["runtime"].get("context_clearing_policy") == "work_lineage":
                lease = {
                    **lease,
                    "runtime": {
                        **lease["runtime"],
                        "work_lineage_thread_ids": {
                            lease["dispatch"]["work_lineage_id"]: "saved-example-thread"
                        },
                    },
                }
            before = old_provider._build_prompt(lease, effective_resume_mode="same_context")
            after = providers._build_prompt(lease, effective_resume_mode="same_context")
            guidance_before = old_provider._team_agent_guidance_content(agent=lease["agent"])
            guidance_after = providers._team_agent_guidance_content(agent=lease["agent"])
            before_context = old_wake.build_compact_wake_context(lease, fresh_context=fresh)
            after_context = build_compact_wake_context(lease, fresh_context=fresh)
            rows.append(
                {
                    "fixture": fixture["name"],
                    "mode": "fresh" if fresh else "retained",
                    "wake_before": _size(before),
                    "wake_after": _size(after),
                    "wake_plus_guidance_bytes_before": len((before + guidance_before).encode()),
                    "wake_plus_guidance_bytes_after": len((after + guidance_after).encode()),
                    "wake_reduction_percent": round(
                        100 * (1 - len(after.encode()) / len(before.encode())), 2
                    ),
                    "wake_duplicate_string_bytes_before": _duplicates(before_context),
                    "wake_duplicate_string_bytes_after": _duplicates(after_context),
                    "full_read": _size(full_json),
                    "summary_read": _size(summary_json),
                    "read_duplicate_string_bytes_before": _duplicates(full),
                    "read_duplicate_string_bytes_after": _duplicates(summary),
                    "targeted_retrievals": retrieved,
                    "additional_read_calls_vs_full": len(retrieved),
                    "targeted_read_bytes_including_retrieval": len(summary_json.encode())
                    + retrieval_bytes,
                    "targeted_read_reduction_percent": round(
                        100
                        * (
                            1
                            - (len(summary_json.encode()) + retrieval_bytes)
                            / len(full_json.encode())
                        ),
                        2,
                    ),
                    "all_detail_read_calls": sum(
                        len(full.get(key, [])) for key in summary["retrieval"]
                    ),
                    "all_detail_read_bytes_including_summary": len(summary_json.encode())
                    + all_detail_bytes,
                }
            )
    agent = data["fixtures"][0]["lease"]["agent"]
    return {
        "baseline_commit": commit,
        "fixture_sha256": hashlib.sha256(fixtures_path.read_bytes()).hexdigest(),
        "provenance": data["provenance"],
        "managed_guidance_before": _size(old_provider._team_agent_guidance_content(agent=agent)),
        "managed_guidance_after": _size(providers._team_agent_guidance_content(agent=agent)),
        "notes": [
            "Wake sizes include the ordinary provider prompt; they exclude MCP schemas, system prompts and managed guidance loading.",
            "Reads are compact serialized fixture records, excluding tool envelopes and request overhead. Full mode remains the baseline contract.",
            "Targeted reads select task-0 and evidence-0 only, not completion of the entire dispatch; selected reads cost two extra calls versus full.",
            "All-detail column retrieves every scoped task, inbox, blocker, review, project and evidence record; this exposes worst-case rehydration overhead.",
            "Duplicate content counts repeated leaf-string bytes of at least 40 characters, excluding JSON keys, with each duplicate counted once.",
            "Managed guidance bytes are reported separately; loading them, rebuilding contexts, retries, and rework must be measured in a live pilot.",
            "50% wake and 35% targeted-read reductions are ambitious measurement targets, not functional acceptance gates or billing estimates.",
        ],
        "rows": rows,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Team context offline comparison",
        "",
        f"Baseline: `{report['baseline_commit']}`.",
        f"Fixture SHA-256: `{report['fixture_sha256']}`.",
        "",
        report["provenance"],
        "",
        "| Fixture | Context | Wake bytes before → after | Wake reduction | Full → summary read bytes | With selected reads | Read reduction | All detail with summary |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['fixture']} | {row['mode']} | {row['wake_before']['utf8_bytes']:,} → {row['wake_after']['utf8_bytes']:,} | {row['wake_reduction_percent']}% | {row['full_read']['utf8_bytes']:,} → {row['summary_read']['utf8_bytes']:,} | {row['targeted_read_bytes_including_retrieval']:,} | {row['targeted_read_reduction_percent']}% | {row['all_detail_read_bytes_including_summary']:,} |"
        )
    lines += [
        "",
        "Each targeted scenario adds two calls to recover the complete selected task and evidence record. "
        "This measures starting a selected item, not completing all scoped work. The JSON report records each retrieval and duplicate-content metric.",
        "",
        f"Managed guidance: {report['managed_guidance_before']['utf8_bytes']:,} → {report['managed_guidance_after']['utf8_bytes']:,} UTF-8 bytes. "
        "This increase is separate from the wake measurements; its actual loading frequency matters.",
        "",
    ]
    lines += [
        "The following sensitivity calculation includes a full copy of managed guidance once per wake. "
        "It is an assumption about loading frequency, not a measurement of model history or caching.",
        "",
        "| Fixture | Context | Wake + guidance bytes before → after | Duplicate wake string bytes before → after | Duplicate read string bytes before → after | All-detail extra calls |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['fixture']} | {row['mode']} | {row['wake_plus_guidance_bytes_before']:,} → {row['wake_plus_guidance_bytes_after']:,} | "
            f"{row['wake_duplicate_string_bytes_before']:,} → {row['wake_duplicate_string_bytes_after']:,} | "
            f"{row['read_duplicate_string_bytes_before']:,} → {row['read_duplicate_string_bytes_after']:,} | {row['all_detail_read_calls']} |"
        )
    lines.append("")
    lines.extend(f"- {note}" for note in report["notes"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    report = compare(baseline=args.baseline, fixtures_path=args.fixtures)
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2) + "\n")
    if args.markdown_output:
        args.markdown_output.write_text(markdown(report))
    print(markdown(report), end="")


if __name__ == "__main__":
    main()
