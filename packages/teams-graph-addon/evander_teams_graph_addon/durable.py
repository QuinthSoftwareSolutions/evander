"""Restrictive atomic operation journals, independent of refreshable Teams caches."""

from pathlib import Path

from evander.teams_outbound import JsonTeamsOutboundStore, outbound_key, outbound_message_lock


class OperationJournal:
    def __init__(self, runtime_dir, namespace):
        self.root = Path(runtime_dir) / "teams-operations" / namespace
        self.writer = JsonTeamsOutboundStore(self.root / "unused")

    def key(self, *parts):
        import json

        return outbound_key("operation", json.dumps(parts))

    def lock(self, key):
        return outbound_message_lock(self.root / "locks", "operation", key)

    def read(self, key):
        import json

        try:
            return json.loads((self.root / (key + ".json")).read_text())
        except FileNotFoundError:
            return None

    def write(self, key, value):
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.writer._write(self.root / (key + ".json"), value)
