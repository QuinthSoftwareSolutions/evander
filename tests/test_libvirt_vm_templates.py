import pytest

from evander.vm.defaults_config import EffectiveVmDefaultsConfig
from evander.vm.templates import (
    normalize_libvirt_vm_templates,
    resolve_libvirt_vm_template_selector,
)


def test_libvirt_vm_templates_round_trip_through_effective_vm_defaults_config() -> None:
    payload = {
        "libvirt_vm_default_template": " default ",
        "libvirt_vm_templates": {
            " default ": " ubuntu-24.04-server-cloudimg-amd64.img ",
            "ubuntu-noble": " noble-server.qcow2 ",
        },
    }

    config = EffectiveVmDefaultsConfig.model_validate(payload)
    round_trip = EffectiveVmDefaultsConfig.model_validate(config.model_dump(mode="json"))

    assert config.libvirt_vm_default_template == "default"
    assert config.libvirt_vm_templates == {
        "default": "ubuntu-24.04-server-cloudimg-amd64.img",
        "ubuntu-noble": "noble-server.qcow2",
    }
    assert config.model_dump(mode="json")["libvirt_vm_default_template"] == "default"
    assert config.model_dump(mode="json")["libvirt_vm_templates"] == {
        "default": "ubuntu-24.04-server-cloudimg-amd64.img",
        "ubuntu-noble": "noble-server.qcow2",
    }
    assert round_trip.libvirt_vm_default_template == "default"
    assert round_trip.libvirt_vm_templates == config.libvirt_vm_templates
    assert resolve_libvirt_vm_template_selector(
        None,
        default_template=config.libvirt_vm_default_template,
        templates=config.libvirt_vm_templates,
    ) == {
        "image_ref": "ubuntu-24.04-server-cloudimg-amd64.img",
        "template_alias": "default",
    }
    assert resolve_libvirt_vm_template_selector(
        "ubuntu-noble",
        default_template=config.libvirt_vm_default_template,
        templates=config.libvirt_vm_templates,
    ) == {
        "image_ref": "noble-server.qcow2",
        "template_alias": "ubuntu-noble",
    }


def test_libvirt_vm_template_selector_supports_direct_default_image_ref() -> None:
    config = EffectiveVmDefaultsConfig.model_validate(
        {
            "libvirt_vm_default_template": " noble-server.qcow2 ",
            "libvirt_vm_templates": {"default": "ubuntu-24.04-server-cloudimg-amd64.img"},
        }
    )

    assert resolve_libvirt_vm_template_selector(
        None,
        default_template=config.libvirt_vm_default_template,
        templates=config.libvirt_vm_templates,
    ) == {"image_ref": "noble-server.qcow2", "template_alias": None}
    assert resolve_libvirt_vm_template_selector(
        " ",
        default_template=config.libvirt_vm_default_template,
        templates=config.libvirt_vm_templates,
    ) == {"image_ref": "noble-server.qcow2", "template_alias": None}


def test_libvirt_vm_template_selector_rejects_unknown_alias() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"unknown libvirt VM template selector 'missing'; configured aliases: default, dotnet; "
            r"default template: 'default'"
        ),
    ):
        resolve_libvirt_vm_template_selector(
            "missing",
            default_template="default",
            templates=normalize_libvirt_vm_templates(
                {"default": "ubuntu-24.04-server-cloudimg-amd64.img", "dotnet": "dotnet.img"}
            ),
        )


def test_libvirt_vm_template_selector_rejects_raw_image_ref_selector() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"unknown libvirt VM template selector 'dotnet.img'; configured aliases: default, dotnet; "
            r"default template: 'default'"
        ),
    ):
        resolve_libvirt_vm_template_selector(
            "dotnet.img",
            default_template="default",
            templates=normalize_libvirt_vm_templates(
                {"default": "ubuntu-24.04-server-cloudimg-amd64.img", "dotnet": "dotnet.img"}
            ),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"libvirt_vm_templates": {"": "ubuntu.img"}},
            "aliases cannot be empty",
        ),
        (
            {"libvirt_vm_templates": {"bad alias": "ubuntu.img"}},
            "must match",
        ),
        (
            {"libvirt_vm_templates": {"default": ""}},
            "cannot be empty",
        ),
        (
            {"libvirt_vm_templates": {"default": 123}},
            "must be strings",
        ),
        (
            {
                "libvirt_vm_templates": {
                    "default": "ubuntu.img",
                    " default ": "ubuntu-alt.img",
                }
            },
            "duplicate libvirt_vm_templates alias",
        ),
    ],
)
def test_libvirt_vm_templates_reject_invalid_catalog_entries(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        EffectiveVmDefaultsConfig.model_validate(payload)
