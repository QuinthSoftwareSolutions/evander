"""Discovery and enablement of this separately packaged add-on project."""

from pathlib import Path

from evander.addons import AddonLoaderConfig, discover_addons, load_addons


def test_project_is_discoverable_and_requires_explicit_enablement():
    project = Path(__file__).resolve().parents[1]
    config = AddonLoaderConfig(package_dirs=[str(project.parent)])
    records = discover_addons(config)
    record = next(
        item for item in records
        if item.registration and item.registration.addon_id == "wordpress-addon"
    )
    assert record.error is None
    assert record.registration is not None
    assert load_addons(config).registrations == ()

    config.enabled_addon_ids = ["wordpress-addon"]
    catalog = load_addons(config)
    registration, = catalog.registrations
    assert registration.addon_id == "wordpress-addon"
    assert set(registration.connector_drivers) == {"wordpress"}
    assert set(registration.website_session_adapters) == {"wordpress"}
