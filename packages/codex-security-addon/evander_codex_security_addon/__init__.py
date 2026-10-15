from evander.addons import AddonRegistration

from .provider import CodexSecurityScanProvider


def register_addon():
    return AddonRegistration(
        addon_id="codex-security-addon",
        display_name="Codex Security",
        security_scan_providers={"codex-security": CodexSecurityScanProvider},
        metadata={"description": "Standard and deep repository security scans in agent sessions."},
    )
