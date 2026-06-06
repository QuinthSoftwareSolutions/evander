from __future__ import annotations

import re

DEFAULT_LIBVIRT_VM_DEFAULT_TEMPLATE = "default"
DEFAULT_LIBVIRT_VM_TEMPLATES = {
    DEFAULT_LIBVIRT_VM_DEFAULT_TEMPLATE: "ubuntu-24.04-server-cloudimg-amd64.img",
}

_LIBVIRT_VM_TEMPLATE_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def normalize_libvirt_vm_templates(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("libvirt_vm_templates must be a JSON object")

    normalized: dict[str, str] = {}
    for raw_alias, raw_ref in value.items():
        alias = str(raw_alias or "").strip()
        if not alias:
            raise ValueError("libvirt_vm_templates aliases cannot be empty")
        if not _LIBVIRT_VM_TEMPLATE_ALIAS_RE.fullmatch(alias):
            raise ValueError(
                "libvirt_vm_templates aliases must match "
                "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
            )
        if alias in normalized:
            raise ValueError(f"duplicate libvirt_vm_templates alias: {alias!r}")
        if not isinstance(raw_ref, str):
            raise ValueError(f"libvirt_vm_templates[{alias!r}] image refs must be strings")
        image_ref = raw_ref.strip()
        if not image_ref:
            raise ValueError(f"libvirt_vm_templates[{alias!r}] image refs cannot be empty")
        normalized[alias] = image_ref
    return normalized


def resolve_libvirt_vm_template_selector(
    selector: str | None,
    *,
    default_template: str,
    templates: dict[str, str],
) -> dict[str, str | None]:
    normalized_default_template = str(default_template or "").strip()
    if not normalized_default_template:
        raise ValueError("libvirt VM default template cannot be empty")

    normalized_selector = str(selector or "").strip()
    if not normalized_selector:
        image_ref = templates.get(normalized_default_template)
        if image_ref is not None:
            return {
                "image_ref": image_ref,
                "template_alias": normalized_default_template,
            }
        return {"image_ref": normalized_default_template, "template_alias": None}

    image_ref = templates.get(normalized_selector)
    if image_ref is not None:
        return {"image_ref": image_ref, "template_alias": normalized_selector}

    aliases = ", ".join(sorted(templates)) if templates else "none"
    raise ValueError(
        "unknown libvirt VM template selector "
        f"{normalized_selector!r}; configured aliases: {aliases}; "
        f"default template: {normalized_default_template!r}"
    )
