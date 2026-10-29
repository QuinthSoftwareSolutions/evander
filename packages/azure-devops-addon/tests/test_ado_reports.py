from __future__ import annotations

from datetime import UTC, datetime

from evander_azure_devops_addon.reports import build_ado_daily_status_report_data


def test_build_ado_daily_status_report_data_returns_template_block() -> None:
    payload = build_ado_daily_status_report_data(
        items=[
            {
                "id": "1",
                "state": "Active",
                "type": "Task",
                "assigned_to": "Ada",
                "fields": {"Microsoft.VSTS.Scheduling.Effort": 3},
                "relations": [
                    {
                        "rel": "ArtifactLink",
                        "url": "vstfs:///Git/PullRequestId/project%2Frepo%2F42",
                        "attributes": {"name": "Pull Request"},
                    }
                ],
            },
            {
                "id": "2",
                "state": "Committed",
                "type": "Task",
                "assigned_to": None,
                "fields": {"Microsoft.VSTS.Scheduling.Effort": "2,5"},
            },
            {
                "id": "3",
                "state": "Done",
                "type": "Bug",
                "fields": {"Microsoft.VSTS.Scheduling.Effort": 4.25},
                "relations": [
                    {
                        "rel": "AttachedFile",
                        "url": "https://dev.azure.com/example/_apis/wit/attachments/1",
                        "attributes": {"name": "screenshot.png"},
                    }
                ],
            },
            {
                "id": "4",
                "state": "New",
                "type": "Task",
                "assigned_to": "Grace",
                "fields": {},
            },
        ],
        project="Example",
        team="Platform",
        iteration_path="Example\\Sprint 12",
        iteration_start_date="2026-05-04T00:00:00Z",
        iteration_finish_date="2026-05-18T00:00:00Z",
        report_date="2026-05-12",
        elapsed_sprint_percent=50,
        elapsed_time_mode="manual_override",
        now=datetime(2026, 5, 12, 8, 0, tzinfo=UTC),
    )

    assert payload["template_data"] == {
        "title": "Daily Sprint Status",
        "reportDate": "2026-05-12",
        "sprintLabel": "Sprint 12",
        "sprintLength": "2-week sprint",
        "items": {
            "unassigned": 2,
            "notStarted": 2,
            "inProgress": 1,
            "withPrs": 1,
            "done": 1,
            "total": 4,
        },
        "effort": {"done": 4.25, "total": 9.75},
        "elapsedSprintPercent": 50,
    }
    assert payload["percentages"]["completedEffortPercent"] == 43.6
    assert payload["percentages"]["notStartedShare"] == 50
    assert payload["percentages"]["prCoverage"] == 25
    assert payload["source"]["in_progress_states"] == ["In Progress", "Active"]
    assert payload["source"]["items_missing_effort"] == 1
    assert payload["source"]["elapsed_time_calculation"]["mode"] == "manual_override"
    assert '"withPrs": 1' in payload["template_data_json"]


def test_elapsed_percent_ignores_override_without_manual_mode() -> None:
    payload = build_ado_daily_status_report_data(
        items=[],
        project="Example Industry App",
        team="TechForce",
        iteration_path="Example Industry App\\2026w25",
        iteration_start_date="2026-06-15T00:00:00Z",
        iteration_finish_date="2026-06-26T00:00:00Z",
        elapsed_sprint_percent=100,
        workday_start="09:00",
        workday_end="17:00",
        now=datetime(2026, 6, 24, 15, 0, tzinfo=UTC),
    )

    assert payload["template_data"]["elapsedSprintPercent"] == 77.8
    elapsed = payload["source"]["elapsed_time_calculation"]
    assert elapsed["mode"] == "swedish_work_time"
    assert elapsed["working_minutes_elapsed"] == 3360
    assert elapsed["working_minutes_total"] == 4320
    assert payload["source"]["ignored_elapsed_sprint_percent_override"] == 100


def test_elapsed_percent_uses_swedish_work_time_and_public_holidays() -> None:
    payload = build_ado_daily_status_report_data(
        items=[],
        project="Example",
        team="Platform",
        iteration_path="Example\\Sprint with Ascension",
        iteration_start_date="2026-05-11T00:00:00Z",
        iteration_finish_date="2026-05-15T00:00:00Z",
        now=datetime(2026, 5, 15, 8, 0, tzinfo=UTC),
    )

    # Mon-Wed are complete, Thu 2026-05-14 is Ascension Day, and Fri is two hours in.
    assert payload["template_data"]["elapsedSprintPercent"] == 80.6
    elapsed = payload["source"]["elapsed_time_calculation"]
    assert elapsed["mode"] == "swedish_work_time"
    assert elapsed["working_minutes_elapsed"] == 1740
    assert elapsed["working_minutes_total"] == 2160
    assert {"date": "2026-05-14", "name": "Ascension Day"} in elapsed["excluded_dates"]


def test_sprint_length_uses_work_week_label_for_monday_to_friday_two_week_sprint() -> None:
    payload = build_ado_daily_status_report_data(
        items=[],
        project="Example",
        team="Platform",
        iteration_path="Example\\2026w19",
        iteration_start_date="2026-05-04T00:00:00Z",
        iteration_finish_date="2026-05-15T00:00:00Z",
        now=datetime(2026, 5, 12, 8, 0, tzinfo=UTC),
    )

    assert payload["template_data"]["sprintLength"] == "2-week sprint"


def test_elapsed_percent_can_include_common_swedish_non_working_eves() -> None:
    payload = build_ado_daily_status_report_data(
        items=[],
        project="Example",
        team="Platform",
        iteration_path="Example\\Midsummer week",
        iteration_start_date="2026-06-15",
        iteration_finish_date="2026-06-19",
        now=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        include_common_swedish_non_working_days=True,
    )
    without_eves = build_ado_daily_status_report_data(
        items=[],
        project="Example",
        team="Platform",
        iteration_path="Example\\Midsummer week",
        iteration_start_date="2026-06-15",
        iteration_finish_date="2026-06-19",
        now=datetime(2026, 6, 19, 10, 0, tzinfo=UTC),
        include_common_swedish_non_working_days=False,
    )

    assert payload["template_data"]["elapsedSprintPercent"] == 100
    assert {"date": "2026-06-19", "name": "Midsummer Eve"} in payload["source"][
        "elapsed_time_calculation"
    ]["excluded_dates"]
    assert without_eves["template_data"]["elapsedSprintPercent"] == 88.9


def test_elapsed_percent_can_use_calendar_time_mode() -> None:
    payload = build_ado_daily_status_report_data(
        items=[],
        project="Example",
        team="Platform",
        iteration_path="Example\\Sprint with Ascension",
        iteration_start_date="2026-05-11T00:00:00Z",
        iteration_finish_date="2026-05-15T00:00:00Z",
        elapsed_time_mode="calendar_time",
        now=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
    )

    assert payload["template_data"]["elapsedSprintPercent"] == 50
    assert payload["source"]["elapsed_time_calculation"]["mode"] == "calendar_time"
