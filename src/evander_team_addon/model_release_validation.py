"""Release package relationships and frozen-evidence invariants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model_common import TeamRecordState

if TYPE_CHECKING:
    from .model_organization import Project
    from .model_releases import ReleasePackage
    from .model_state import TeamState


def validate_release_packages(state: TeamState, errors: list[str]) -> None:
    """Append release diagnostics in aggregate and package check order."""
    for release in state.release_packages.values():
        project = state.projects.get(release.project_id)
        if project is None:
            errors.append(f"release {release.id} references missing project {release.project_id}")
            continue
        _ReleasePackageValidation(
            state=state,
            release=release,
            project=project,
            errors=errors,
        ).validate()


@dataclass(frozen=True)
class _ReleasePackageValidation:
    """Keep one package's reference checks in aggregate diagnostic order."""

    state: TeamState
    release: ReleasePackage
    project: Project
    errors: list[str]

    def validate(self) -> None:
        self._validate_owner()
        self._validate_review_cohort()
        self._validate_predecessor()
        self._validate_deliverables()
        self._validate_waivers()
        self._validate_verification_runs()
        self._validate_reviews()
        self._validate_current_verification_runs()
        self._validate_manifest()

    def _validate_owner(self) -> None:
        if self.release.owner_agent_id is not None:
            owner = self.state.agents.get(self.release.owner_agent_id)
            terminal_historical_owner = (
                self.release.state
                in {
                    TeamRecordState.ACCEPTED,
                    TeamRecordState.REJECTED,
                    TeamRecordState.CANCELLED,
                }
                and owner is not None
                and owner.team_id is None
                and owner.retired_team_id == self.project.team_id
            )
            if owner is None or (
                owner.team_id != self.project.team_id and not terminal_historical_owner
            ):
                self.errors.append(
                    f"release {self.release.id} references invalid owner {self.release.owner_agent_id}"
                )

    def _validate_review_cohort(self) -> None:
        if self.release.review_cohort_id is not None:
            if self.release.manifest is None or not self.release.manifest.review_requirements:
                self.errors.append(
                    f"release {self.release.id} review cohort requires frozen review requirements"
                )
            else:
                current_reviews = [
                    self.state.review_requests[review_id]
                    for review_id in self.release.review_request_ids
                    if review_id in self.state.review_requests
                    and self.state.review_requests[review_id].superseded_by_review_request_id
                    is None
                    # Cancellation retires the final reviews but preserves
                    # their membership in the frozen historical cohort.
                    and (
                        self.release.state == TeamRecordState.CANCELLED
                        or self.state.review_requests[review_id].retired_at is None
                    )
                ]
                current_members = [
                    (review.reviewer_agent_id, review.review_profile_id)
                    for review in current_reviews
                ]
                required_members = [
                    (requirement.reviewer_agent_id, requirement.review_profile_id)
                    for requirement in self.release.manifest.review_requirements
                ]
                if sorted(current_members) != sorted(required_members):
                    self.errors.append(
                        f"release {self.release.id} cohort membership does not match its "
                        "frozen review requirements"
                    )

    def _validate_predecessor(self) -> None:
        if self.release.supersedes_release_package_id is not None:
            predecessor = self.state.release_packages.get(
                self.release.supersedes_release_package_id
            )
            if predecessor is None:
                self.errors.append(
                    f"release {self.release.id} supersedes missing release "
                    f"{self.release.supersedes_release_package_id}"
                )
            elif predecessor.id == self.release.id:
                self.errors.append(f"release {self.release.id} cannot supersede itself")
            elif predecessor.project_id != self.release.project_id:
                self.errors.append(
                    f"release {self.release.id} supersedes release {predecessor.id} "
                    "from another project"
                )

    def _validate_deliverables(self) -> None:
        for deliverable_id in self.release.deliverable_ids:
            deliverable = self.state.deliverables.get(deliverable_id)
            if deliverable is None:
                self.errors.append(
                    f"release {self.release.id} references missing deliverable {deliverable_id}"
                )
                continue
            task = self.state.tasks.get(deliverable.task_id)
            if task is not None and task.project_id != self.project.id:
                self.errors.append(
                    f"release {self.release.id} includes deliverable {deliverable_id} outside project"
                )

    def _validate_waivers(self) -> None:
        waiver_deliverable_ids: set[str] = set()
        for waiver in self.release.verification_waivers:
            if waiver.deliverable_id not in self.release.deliverable_ids:
                self.errors.append(
                    f"release {self.release.id} waiver references deliverable "
                    f"{waiver.deliverable_id} outside the package"
                )
            if waiver.deliverable_id in waiver_deliverable_ids:
                self.errors.append(
                    f"release {self.release.id} has duplicate waiver for {waiver.deliverable_id}"
                )
            waiver_deliverable_ids.add(waiver.deliverable_id)

    def _validate_verification_runs(self) -> None:
        for run_id in self.release.verification_run_ids:
            run = self.state.release_verification_runs.get(run_id)
            if run is None:
                self.errors.append(
                    f"release {self.release.id} references missing verification run {run_id}"
                )
                continue
            if run.release_package_id != self.release.id:
                self.errors.append(
                    f"release {self.release.id} references verification run {run_id} "
                    f"owned by {run.release_package_id}"
                )
                continue
            if (
                self.release.candidate_commit_sha is not None
                and run.candidate_commit_sha != self.release.candidate_commit_sha
            ):
                self.errors.append(
                    f"release {self.release.id} verification run {run_id} targets "
                    f"{run.candidate_commit_sha}, not candidate "
                    f"{self.release.candidate_commit_sha}"
                )
            if run.correction_task_id is not None:
                correction_task = self.state.tasks.get(run.correction_task_id)
                if correction_task is None or correction_task.project_id != self.release.project_id:
                    self.errors.append(
                        f"release verification run {run.id} references invalid "
                        f"correction task {run.correction_task_id}"
                    )
            if run.supersedes_verification_run_id is not None:
                previous = self.state.release_verification_runs.get(
                    run.supersedes_verification_run_id
                )
                if previous is None or previous.release_package_id != self.release.id:
                    self.errors.append(
                        f"release verification run {run.id} supersedes an invalid run"
                    )
                elif previous.superseded_by_verification_run_id != run.id:
                    self.errors.append(
                        f"release verification run {run.id} has non-reciprocal lineage"
                    )
            if run.superseded_by_verification_run_id is not None:
                successor = self.state.release_verification_runs.get(
                    run.superseded_by_verification_run_id
                )
                if successor is None or successor.release_package_id != self.release.id:
                    self.errors.append(
                        f"release verification run {run.id} has an invalid successor"
                    )
                elif successor.supersedes_verification_run_id != run.id:
                    self.errors.append(
                        f"release verification run {run.id} has non-reciprocal lineage"
                    )

    def _validate_reviews(self) -> None:
        for review_id in self.release.review_request_ids:
            review = self.state.review_requests.get(review_id)
            if review is None:
                self.errors.append(
                    f"release {self.release.id} references missing review request {review_id}"
                )
                continue
            if review.release_package_id != self.release.id:
                self.errors.append(
                    f"release {self.release.id} references review {review_id} owned by "
                    f"{review.release_package_id}"
                )
                continue
            if review.supersedes_review_request_id is not None:
                previous_review = self.state.review_requests.get(
                    review.supersedes_review_request_id
                )
                if previous_review is None or previous_review.release_package_id != self.release.id:
                    self.errors.append(f"release review {review.id} supersedes an invalid review")
                elif previous_review.superseded_by_review_request_id != review.id:
                    self.errors.append(f"release review {review.id} has non-reciprocal lineage")
            if review.superseded_by_review_request_id is not None:
                successor_review = self.state.review_requests.get(
                    review.superseded_by_review_request_id
                )
                if (
                    successor_review is None
                    or successor_review.release_package_id != self.release.id
                ):
                    self.errors.append(f"release review {review.id} has an invalid successor")
                elif successor_review.supersedes_review_request_id != review.id:
                    self.errors.append(f"release review {review.id} has non-reciprocal lineage")
            if (
                self.release.candidate_commit_sha is not None
                and review.candidate_commit_sha != self.release.candidate_commit_sha
            ):
                self.errors.append(
                    f"release {self.release.id} review {review_id} targets "
                    f"{review.candidate_commit_sha}, not candidate "
                    f"{self.release.candidate_commit_sha}"
                )

    def _validate_current_verification_runs(self) -> None:
        current_runs = [
            self.state.release_verification_runs[run_id]
            for run_id in self.release.verification_run_ids
            if run_id in self.state.release_verification_runs
            and self.state.release_verification_runs[run_id].superseded_by_verification_run_id
            is None
        ]
        if len(current_runs) > 1:
            self.errors.append(f"release {self.release.id} has multiple current verification runs")

    def _validate_manifest(self) -> None:
        if self.release.manifest is not None:
            if self.release.manifest.project_id != self.release.project_id:
                self.errors.append(
                    f"release {self.release.id} manifest belongs to project "
                    f"{self.release.manifest.project_id}"
                )
            if self.release.manifest.release_package_id != self.release.id:
                self.errors.append(
                    f"release {self.release.id} manifest belongs to "
                    f"{self.release.manifest.release_package_id}"
                )
            if self.release.manifest.candidate_commit_sha != self.release.candidate_commit_sha:
                self.errors.append(
                    f"release {self.release.id} manifest candidate does not match the release"
                )
            manifest_deliverable_ids = [
                evidence.deliverable_id for evidence in self.release.manifest.deliverables
            ]
            if len(manifest_deliverable_ids) != len(set(manifest_deliverable_ids)):
                self.errors.append(f"release {self.release.id} manifest has duplicate deliverables")
            if set(manifest_deliverable_ids) != set(self.release.deliverable_ids):
                self.errors.append(
                    f"release {self.release.id} manifest deliverables do not match the package"
                )
            for evidence in self.release.manifest.deliverables:
                if evidence.review_manifest is None:
                    continue
                accepted_shas = {review.reviewed_commit_sha for review in evidence.accepted_reviews}
                if (
                    accepted_shas
                    and evidence.review_manifest.reviewed_commit_sha not in accepted_shas
                ):
                    self.errors.append(
                        f"release {self.release.id} deliverable {evidence.deliverable_id} "
                        "manifest does not match accepted review evidence"
                    )
