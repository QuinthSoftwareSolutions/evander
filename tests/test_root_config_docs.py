from pathlib import Path


def test_root_only_config_docs_describe_root_as_live_baseline() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs/operations.md").read_text(encoding="utf-8")
    configuration_reference = (repo / "docs/configuration-reference.md").read_text(encoding="utf-8")

    assert "is the live top-level baseline" in readme
    assert "Legacy gateway YAML is only a migration/import source" in readme
    assert "Stasis(evander_didww,inbound:<did-or-route-id>)" in readme
    assert "Configure `root` directory overlays directly" in operations
    assert "is the live top-level layer" in operations
    assert "/voice-sessions/inbound-phone-calls" in operations
    assert "/voice-sessions/{voice_session_id}/bridge-result" in operations
    assert "Legacy gateway YAML import keys" in configuration_reference
    assert "is not the live runtime baseline" in configuration_reference
    assert "DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS" in configuration_reference
    assert "DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL" in configuration_reference
    assert "DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV" in configuration_reference


def test_teams_docs_cover_host_delegated_identity_binding() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = " ".join((repo / "README.md").read_text(encoding="utf-8").split())
    operations = " ".join((repo / "docs/operations.md").read_text(encoding="utf-8").split())
    configuration_reference = " ".join(
        (repo / "docs/configuration-reference.md").read_text(encoding="utf-8").split()
    )

    assert "device code flow" in readme
    assert "per-directory Teams identity binding" in readme
    assert "teams_graph_access_token_env" in readme
    assert "trigger:teams:new-message" in readme
    assert "trigger:communication:new-message" in readme
    assert "device code flow" in operations
    assert "Admin Web -> Configuration -> People & Communications" in operations
    assert "teams-auth login" in operations
    assert "host-only" in operations
    assert "teams_graph_access_token_env" in operations
    assert "trigger:communication:new-message" in operations
    assert "host-side ingest polling" in operations
    assert "discord-wake" in operations
    assert "Teams delegated auth follows the same host-side model" in configuration_reference
    assert "providers.json" in configuration_reference
    assert "GRAPH_TEAMS_ACCESS_TOKEN" in configuration_reference
    assert "trigger:teams:new-message" in configuration_reference
    assert "trigger:communication:new-message" in configuration_reference
    assert "independent from the wake workers" in configuration_reference
