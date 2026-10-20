from collections.abc import Mapping

from .base import QueryProviderBase, scoped_query_directory_path


class SecurityScanQueryProvider(QueryProviderBase):
    def execute(self, *, resolved_config: Mapping | None = None):
        if self.runtime_context is None or self.query_definition is None:
            raise RuntimeError("Security scan query has no runtime")
        config = {"scan_id": self.query_definition.scan_id}
        config.update(resolved_config or {})
        service = getattr(self.runtime_context.admin_service, "security_scan_service", None)
        if service is None:
            raise RuntimeError("Security scan service is unavailable")
        try:
            result = service.read_metadata(
                str(config["scan_id"]),
                directory_path=scoped_query_directory_path(self.runtime_context),
            )
        except ValueError:
            # An unknown ID and an ID in another directory are indistinguishable.
            return {"found": False, "scan_id": str(config["scan_id"]), "resumable": False}
        return {"found": True, **result}
