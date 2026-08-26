from pathlib import Path


def default_orchestration_database_path(runtime_dir: Path | str) -> Path:
    return Path(runtime_dir) / "orchestration.db"
