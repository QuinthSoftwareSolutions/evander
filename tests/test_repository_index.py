from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile

import pytest

from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index.export import export_snapshot
from evander.code_index.importer import import_artifact
from evander.code_index.models import (
    IndexArtifact,
    IndexSettings,
    Occurrence,
    Relationship,
    SourceDocument,
)
from evander.code_index.scip import read_scip
from evander.code_index.store import IndexRevisionConflict

SHA = "a" * 40
OTHER_SHA = "b" * 40
REPO = "mr_" + "1" * 20
TEXT = "public interface IRetry {}\npublic class DurableRetry : IRetry {}\nclass Consumer { DurableRetry retry; }\n"


class ManagedGit:
    def pin_retained_snapshot(self, repo, directory, **kwargs):
        self.get_repository(repo, directory)
        return {"pin_ref": kwargs["pin_ref"], "commit_sha": kwargs["commit_sha"]}

    def get_repository(self, repo, directory_path=None):
        if repo != REPO or directory_path not in {None, "root/app", "root/app/team"}:
            raise PermissionError("Repository outside directory")
        return {"managed_repo_id": repo, "directory_path": "root/app"}

    def resolve_snapshot(self, repo, directory, **kwargs):
        self.get_repository(repo, directory)
        return {"commit_sha": kwargs.get("commit_sha") or SHA}


@pytest.fixture
def service(tmp_path):
    result = RepositoryIndexService(tmp_path, ManagedGit())
    result.save_settings(
        REPO,
        "root/app",
        IndexSettings(enabled=True, scip=True, semantic=False, refresh_on_git_change=False).model_dump(),
        expected_revision=0,
    )
    return result


def artifact(sha=SHA, *, scip=True):
    result = IndexArtifact(
        commit_sha=sha,
        documents=[SourceDocument(path="Domain/Retry.cs", text=TEXT)],
        projects=[{"path": "Retry.csproj", "status": "ready"}],
        occurrences=[
            Occurrence(
                path="Domain/Retry.cs",
                symbol="IRetry#",
                line=1,
                column=17,
                end_line=1,
                end_column=23,
                definition=True,
            ),
            Occurrence(
                path="Domain/Retry.cs",
                symbol="DurableRetry#",
                line=2,
                column=13,
                end_line=2,
                end_column=25,
                definition=True,
            ),
            Occurrence(
                path="Domain/Retry.cs",
                symbol="DurableRetry#",
                line=3,
                column=17,
                end_line=3,
                end_column=29,
            ),
        ],
        relationships=[
            Relationship(symbol="DurableRetry#", target="IRetry#", kind="implementation")
        ],
    )
    return result if scip else IndexArtifact(commit_sha=sha, documents=result.documents)


def publish(service, sha=SHA, *, scip=True):
    build = service.ensure(REPO, "root/app", sha)
    build = service.store.get(build["id"])
    coverage = import_artifact(service.store, build, artifact(sha, scip=scip))
    service.store.update(build["id"], status="ready", coverage=coverage)
    return service.store.get(build["id"])


def test_revision_scope_and_deduplication(service):
    with pytest.raises(IndexRevisionConflict):
        service.save_settings(REPO, "root/app", IndexSettings().model_dump(), expected_revision=0)
    with pytest.raises(PermissionError):
        service.save_settings(
            REPO, "root/app/team", IndexSettings().model_dump(), expected_revision=1
        )
    first = service.ensure(REPO, "root/app", SHA)
    assert service.ensure(REPO, "root/app", SHA)["id"] == first["id"]
    assert service.ensure(REPO, "root/app", OTHER_SHA)["id"] != first["id"]
    with pytest.raises(PermissionError):
        service.status(REPO, "root/sibling")


def test_status_pages_newest_first_and_filters_ready_builds(service):
    oldest_ready = publish(service, SHA)
    newest_ready = publish(service, OTHER_SHA)
    newest_failed = service.ensure(REPO, "root/app", "c" * 40)
    service.store.update(newest_failed["id"], status="failed")
    with service.store.connect(write=True) as db:
        for created, build in (
            ("2026-09-17T08:00:00+00:00", oldest_ready),
            ("2026-09-17T09:00:00+00:00", newest_ready),
            ("2026-09-17T10:00:00+00:00", newest_failed),
        ):
            db.execute("UPDATE snapshots SET created=? WHERE id=?", (created, build["id"]))

    first_page = service.status(REPO, "root/app", builds_limit=2)
    assert [build["id"] for build in first_page["builds"]] == [
        newest_failed["id"],
        newest_ready["id"],
    ]
    assert first_page["builds_total"] == 3
    assert first_page["builds_offset"] == 0
    assert first_page["status"] == "failed"

    ready_page = service.status(
        REPO,
        "root/app",
        build_statuses=("ready",),
        builds_limit=1,
        builds_offset=1,
    )
    assert [build["id"] for build in ready_page["builds"]] == [oldest_ready["id"]]
    assert ready_page["builds_total"] == 2
    assert ready_page["builds_limit"] == 1
    assert ready_page["builds_offset"] == 1
    assert ready_page["status"] == "failed"

    with pytest.raises(ValueError, match="builds_limit"):
        service.status(REPO, "root/app", builds_limit=0)
    with pytest.raises(ValueError, match="builds_offset"):
        service.status(REPO, "root/app", builds_offset=-1)


def test_search_navigation_and_exact_commit(service):
    publish(service)
    result = service.search(REPO, "root/app", SHA, "durable retry", mode="lexical")
    assert result["hits"][0]["path"] == "Domain/Retry.cs"
    assert service.search(REPO, "root/app", OTHER_SHA, "retry")["status"] == "unavailable"
    assert (
        service.navigate(REPO, "root/app", SHA, "DurableRetry#", kind="references")["hits"][0][
            "line"
        ]
        == 3
    )
    assert (
        service.navigate(REPO, "root/app", SHA, "IRetry#", kind="implementations")["hits"][0][
            "symbol"
        ]
        == "DurableRetry#"
    )
    assert service.search(REPO, "root/app", SHA, "retry", mode="hybrid")["warnings"]


def test_import_rejects_wrong_commit_tampering_and_ranges(service):
    build = service.store.get(service.ensure(REPO, "root/app", SHA)["id"])
    with pytest.raises(ValueError, match="commit"):
        import_artifact(service.store, build, artifact(OTHER_SHA))
    with pytest.raises(ValueError, match="Git snapshot"):
        import_artifact(
            service.store, build, artifact(), tracked_blobs={"Domain/Retry.cs": "0" * 40}
        )
    changed = artifact()
    changed.occurrences[0].end_column = 500
    with pytest.raises(ValueError, match="column"):
        import_artifact(service.store, build, changed)
    blob = hashlib.sha1(f"blob {len(TEXT.encode())}\0".encode() + TEXT.encode()).hexdigest()
    assert (
        import_artifact(service.store, build, artifact(), tracked_blobs={"Domain/Retry.cs": blob})[
            "navigation"
        ]
        == "ready"
    )


@pytest.mark.parametrize("path", ["../secrets.cs", "/etc/test.cs", "a\\b.cs", "a//b.cs", "a\nb.cs"])
def test_source_path_boundaries(path):
    with pytest.raises(ValueError):
        SourceDocument(path=path, text="data")


def test_exclusions_are_enforced_again_at_import(service):
    build = service.store.get(service.ensure(REPO, "root/app", SHA)["id"])
    data = artifact()
    data.documents.extend(
        [
            SourceDocument(path="node_modules/library.cs", text="PRIVATE"),
            SourceDocument(path="appsettings.secret.json", text="PRIVATE"),
            SourceDocument(path="packages/cache/library.ts", text="PRIVATE"),
        ]
    )
    import_artifact(service.store, build, data)
    service.store.update(build["id"], status="ready")
    assert not service.search(REPO, "root/app", SHA, "PRIVATE", mode="lexical")["hits"]


def test_packages_exclusion_is_overridable_without_disabling_hard_exclusions():
    settings = IndexSettings(includes=["packages/**"])
    assert not settings.includes_file("packages/client/src/index.ts")
    settings.excludes.remove("**/packages/**")
    assert settings.includes_file("packages/client/src/index.ts")
    for path in (
        "packages/client/node_modules/dependency/index.ts",
        "packages/client/.git/config",
        "packages/client/obj/generated.cs",
        "packages/client/bin/compiled.cs",
        "packages/client/dist/index.ts",
        "packages/client/.env",
        "packages/client/credentials.json",
        "packages/client/appsettings.secret.json",
        "packages/client/key.pem",
    ):
        assert not settings.includes_file(path), path


def test_portable_export_queries_without_evander(service, tmp_path):
    build = publish(service)
    publish(service, OTHER_SHA)
    target = tmp_path / "export.zip"
    export_snapshot(service.store, build, target)
    folder = tmp_path / "external"
    with zipfile.ZipFile(target) as archive:
        archive.extractall(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["commit_sha"] == SHA
    assert "endpoint" not in manifest["embedding"]
    for name, expected in manifest["checksums"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    output = subprocess.check_output(
        [
            "python3",
            "-I",
            str(folder / "query.py"),
            "--db",
            str(folder / "index.sqlite"),
            "search",
            "durable retry",
        ],
        timeout=10,
    )
    assert json.loads(output)["hits"][0]["path"] == "Domain/Retry.cs"
    output = subprocess.check_output(
        [
            "python3",
            "-I",
            str(folder / "query.py"),
            "--db",
            str(folder / "index.sqlite"),
            "navigate",
            "IRetry#",
            "--kind",
            "implementations",
        ],
        timeout=10,
    )
    assert json.loads(output)["hits"][0]["symbol"] == "DurableRetry#"


def test_implementation_expansion_is_bounded_in_service_and_export(service, tmp_path):
    from evander.code_index.portable_query import navigate, open_index

    build = publish(service)
    with service.store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO relationships VALUES(?,?,?,?)",
            ((build["id"], f"Implementor{i}#", "IRetry#", "implementation") for i in range(2001)),
        )
    with pytest.raises(ValueError, match="2000 symbols"):
        service.navigate(REPO, "root/app", SHA, "IRetry#", kind="implementations")
    destination = tmp_path / "wide.zip"
    export_snapshot(service.store, build, destination)
    with zipfile.ZipFile(destination) as bundle:
        bundle.extractall(tmp_path / "wide")
    with open_index(tmp_path / "wide" / "index.sqlite") as db:
        with pytest.raises(ValueError, match="2000 symbols"):
            navigate(db, "IRetry#", kind="implementations")


def test_cancel_and_failed_rebuild_preserve_published_snapshot(service):
    old = publish(service)
    replacement = service.ensure(REPO, "root/app", SHA, rebuild=True)
    assert replacement["id"] != old["id"]
    service.cancel(REPO, "root/app", replacement["id"])
    service.store.update(replacement["id"], status="ready")
    assert service.store.get(replacement["id"])["status"] == "cancelled"
    assert service.snapshot(REPO, "root/app", SHA)["id"] == old["id"]


@pytest.mark.parametrize("scip", [False, True])
def test_worker_reconciliation_and_restart(service, scip):
    saved = service.settings(REPO, "root/app")
    service.save_settings(
        REPO, "root/app", {**saved["settings"], "scip": scip}, expected_revision=saved["revision"]
    )

    class Worker:
        def __init__(self):
            self.retired = []

        def start(self, build, *, before_submit):
            before_submit()
            return build["id"]

        def status(self, build):
            return "completed"

        def artifact(self, build):
            return artifact(scip=scip).model_dump_json()

        def retire_session(self, build):
            self.retired.append(build["id"])

    service.worker = Worker()
    service._tracked_blobs = lambda build: {
        "Domain/Retry.cs": hashlib.sha1(f"blob {len(TEXT)}\0".encode() + TEXT.encode()).hexdigest()
    }
    build = service.ensure(REPO, "root/app", SHA)
    assert service.reconcile("root/app")["processed"] == 1
    assert service.store.get(build["id"])["status"] == "running"
    assert service.reconcile("root/app")["processed"] == 1
    assert service.store.get(build["id"])["status"] == "ready"
    assert service.store.get(build["id"])["coverage"]["navigation"] == (
        "ready" if scip else "disabled"
    )
    assert service.worker.retired == [build["id"]]
    assert service.store.get(build["id"])["metrics"]["worker_session_cleanup_completed_at"]
    assert service.store.get(build["id"])["status"] == "ready"
    assert service.search(REPO, "root/app", SHA, "retry", mode="lexical")["hits"]
    result = service.navigate(REPO, "root/app", SHA, "IRetry#")
    assert result["status"] == ("ready" if scip else "disabled")
    assert bool(result["hits"]) is scip


def test_import_rejects_scip_data_without_opt_in(service):
    saved = service.settings(REPO, "root/app")
    service.save_settings(
        REPO, "root/app", {**saved["settings"], "scip": False}, expected_revision=saved["revision"]
    )
    build = service.store.get(service.ensure(REPO, "root/app", SHA)["id"])
    with pytest.raises(ValueError, match="SCIP data requires scip=true"):
        import_artifact(service.store, build, artifact())


def test_scip_wire_format_and_malformed_input():
    # Index{document{path:"a.cs",occurrence{range:[0,0,1],symbol:"S",roles:Definition}}}
    data = bytes.fromhex("12120a04612e6373120a0a030000011201531801")
    occurrences, relationships, paths = read_scip(data)
    assert paths == {"a.cs"}
    assert occurrences[0].line == 1 and occurrences[0].definition
    assert relationships == []
    with pytest.raises(ValueError):
        read_scip(data[:-1])
