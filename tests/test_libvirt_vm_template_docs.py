from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "libvirt-vm-template-operations.md"


def test_libvirt_vm_template_runbook_is_linked_from_core_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    operations = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert "libvirt-vm-template-operations.md" in readme
    assert "libvirt-vm-template-operations.md" in operations


def test_libvirt_vm_template_runbook_covers_operator_contract() -> None:
    contents = RUNBOOK.read_text(encoding="utf-8").lower()

    required_terms = [
        "qcow2",
        "cloud-init-compatible",
        "not a docker image workflow",
        "libvirt_vm_templates",
        "libvirt_vm_default_template",
        "effect:agent:create",
        "vm_image",
        "container_image",
        "libvirt_base_image",
        "metadata.libvirt_vm_template",
        "qemu-img info",
        "runtime/sessions.json",
        "missing base image",
        "unknown libvirt vm template selector",
        "ssh-ready",
        "bootstrap failure",
        "dotnet",
        "nuget",
        "mssql",
        "playwright",
        "codex_skills",
    ]

    for term in required_terms:
        assert term in contents
