from __future__ import annotations

import re

_CONTAINER_WORKER_IMAGE_ALIAS_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)


def normalize_container_worker_images(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("container_worker_images must be a JSON object")

    normalized: dict[str, str] = {}
    for raw_alias, raw_ref in value.items():
        alias = str(raw_alias or "").strip()
        if not alias:
            raise ValueError("container_worker_images aliases cannot be empty")
        if not _CONTAINER_WORKER_IMAGE_ALIAS_RE.fullmatch(alias):
            raise ValueError(
                "container_worker_images aliases must match "
                "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
            )
        if alias in normalized:
            raise ValueError(f"duplicate container_worker_images alias: {alias!r}")
        if not isinstance(raw_ref, str):
            raise ValueError(
                f"container_worker_images[{alias!r}] image refs must be strings"
            )
        image_ref = raw_ref.strip()
        if not image_ref:
            raise ValueError(
                f"container_worker_images[{alias!r}] image refs cannot be empty"
            )
        normalized[alias] = image_ref
    return normalized


def resolve_container_worker_image_selector(
    selector: str | None,
    *,
    default_image: str,
    images: dict[str, str],
) -> dict[str, str | None]:
    normalized_default_image = str(default_image or "").strip()
    if not normalized_default_image:
        raise ValueError("container worker default image cannot be empty")

    normalized_selector = str(selector or "").strip()
    if not normalized_selector or normalized_selector == normalized_default_image:
        return {"image": normalized_default_image, "image_alias": None}

    image = images.get(normalized_selector)
    if image is not None:
        return {"image": image, "image_alias": normalized_selector}

    aliases = ", ".join(sorted(images)) if images else "none"
    raise ValueError(
        "unknown container worker image selector "
        f"{normalized_selector!r}; configured aliases: {aliases}; "
        f"default image: {normalized_default_image!r}"
    )
