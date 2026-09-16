"""Read bounded prose from authorized bundles without extracting files."""

import hashlib
from pathlib import PurePosixPath
import tarfile
import zlib

from evander.artifact_spaces import ArtifactSpaceStore
from evander.published_artifacts import PublishedArtifactStore


def artifact_reader(repository, state, *, actor_id=None):
    def read(ref, deliverable):
        from ..providers import _artifact_space_path_from_ref, _require_team_artifact_space_read

        task = state.tasks.get(deliverable.task_id)
        if task is None or task.project_id not in state.projects:
            return None
        team_id = state.projects[task.project_id].team_id
        try:
            path = _artifact_space_path_from_ref(ref)
            spaces = ArtifactSpaceStore(repository.runtime_dir).read()
            _require_team_artifact_space_read(
                spaces,
                artifact_space_path=path,
                team_id=team_id,
                subject=f"team-agent:{actor_id or deliverable.producer_agent_id}",
            )
            versions = spaces.latest_versions_for_space(path, artifact_id="bundle")
            if not versions or not versions[0].artifact_key.startswith(f"team/{team_id}/"):
                return None
            version = versions[0]
            artifact = PublishedArtifactStore(repository.runtime_dir).get(version.artifact_key)
            if artifact.metadata.byte_count > 32 * 1024 * 1024:
                return None
            with artifact.blob_path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != artifact.metadata.sha256:
                    return None
            documents, omissions, total = [], [], 0
            with tarfile.open(artifact.blob_path, "r|gz") as archive:
                for index, member in enumerate(archive):
                    if index >= 200 or total + member.size > 4 * 1024 * 1024:
                        omissions.append("bundle read limit exceeded")
                        break
                    total += member.size
                    name = PurePosixPath(member.name)
                    if (
                        not member.isfile()
                        or name.is_absolute()
                        or ".." in name.parts
                        or name.suffix.lower() not in {".md", ".txt", ".rst"}
                    ):
                        continue
                    if member.size > 1024 * 1024:
                        omissions.append(f"document too large: {member.name}")
                        continue
                    stream = archive.extractfile(member)
                    if stream is None:
                        continue
                    with stream:
                        raw = stream.read(1024 * 1024 + 1)
                    try:
                        content = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        omissions.append(f"non-UTF8 document: {member.name}")
                        continue
                    documents.append(
                        {
                            "path": member.name,
                            "content": content,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    )
            if not documents:
                return None
            return {
                "id": version.id,
                "artifact_ref": ref,
                "artifact_key": version.artifact_key,
                "version": version.version,
                "created_at": version.created_at.isoformat(),
                "archive_sha256": artifact.metadata.sha256,
                "documents": documents,
                "omissions": omissions,
                "resolution": "published version at context capture",
            }
        except (OSError, ValueError, PermissionError, EOFError, tarfile.TarError, zlib.error):
            return None

    return read
