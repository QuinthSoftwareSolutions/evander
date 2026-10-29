"""Generic control affordances for the bounded decision questions."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation

from .models import BrowserError

TARGET_ROLES = {
    "button",
    "link",
    "textbox",
    "combobox",
    "date",
    "spinbutton",
    "checkbox",
    "radio",
    "tab",
    "menuitem",
    "option",
    "row",
    "gridcell",
}

ACTION_DESCRIPTIONS = {
    "click": (
        "Activate a button, link, checkbox, radio, tab or menu item, or open a closed "
        "combobox/picker. Open a picker with click, then use its supported keyboard controls. "
        "Do not submit while an earlier requested field is unfinished."
    ),
    "hover": "Move the pointer over a control only when the goal needs its hover behavior.",
    "fill": (
        "Replace writable text, a numeric spinbutton or a native date with an exact supplied literal. Dates use YYYY-MM-DD or an empty string to clear. This is the normal "
        "choice for setting a field value, including an empty string to clear it. "
        "Use it for the next unfinished field before submitting; never fill a non-editable combobox."
    ),
    "select": "Choose an exact label from a native combobox's observed options in one operation. Use a supplied input with that option label; do not use fill/type on a non-editable dropdown.",
    "type": (
        "Append an exact supplied literal as character key events only when the goal "
        "specifically requires typing events or appending rather than replacement. "
        "Ordinary form value replacement uses fill."
    ),
    "key": (
        "Send one supported keyboard key when the next step requires keyboard interaction, "
        "such as moving/confirming a dropdown option or submitting with Enter. "
        "Ordinary text-field replacement uses fill."
    ),
    "scroll": "Scroll a referenced region or the main page when needed to reach the next control.",
    "wait": "Wait for a pending page update before choosing the next action.",
    "done": (
        "The complete requested objective is visibly satisfied, including any required "
        "submission/result. Return for the owning agent to verify."
    ),
    "help": (
        "The next necessary operation cannot be chosen safely from current controls and "
        "exact supplied inputs, or requires information/unsupported UI. Return to the owning agent."
    ),
    "inspect": "Reveal a listed region's controls before choosing an action there. This does not send browser input.",
}
ACTION_TARGET_INSTRUCTIONS = (
    " Action and target must describe the same immediate operation. Setting a text field "
    "value normally means fill, not type or key. For a non-editable combobox use "
    "select when advertised to choose an exact observed option label. Otherwise use click "
    "to open it, then supported keys/options; fill/type cannot operate it."
)


def target_actions(element, *, has_values):
    """Keep input choices consistent with the observed control, without site heuristics."""
    if element.get("disabled") in (True, "true"):
        return []
    actions = ["click", "hover", "key", "scroll"]
    editable = element.get("role") == "textbox" or (
        element.get("role") == "combobox" and element.get("editable") not in (None, False, "false")
    ) or (element.get("role") == "spinbutton" and element.get("editable") not in (None, False, "false"))
    if has_values and editable and element.get("readonly") not in (True, "true"):
        actions.extend(("fill", "type"))
    if has_values and element.get("readonly") not in (True, "true"):
        if element.get("native_control") == "date" and element.get("native_input_verified") is True and element.get("value_format") == "YYYY-MM-DD":
            actions.append("fill")
        if element.get("native_control") == "select" and element.get("options") and element.get("expanded") not in (True,"true"):
            actions.append("select")
    return actions


def validate_input(element, action, value):
    if action == "fill" and element.get("native_control") == "date" and value:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or element.get("min") and value < element["min"] or element.get("max") and value > element["max"]:
                raise ValueError
            if element.get("step") != "any":
                step = Decimal(element.get("step") or "1")
                if not step.is_finite() or step <= 0:
                    step = Decimal(1)
                base = date.fromisoformat(element.get("step_base") or element.get("min") or "1970-01-01")
                if Decimal((parsed-base).days) % step:
                    raise ValueError
        except (ValueError, InvalidOperation):
            raise BrowserError("Date inputs require YYYY-MM-DD or an empty value", "batch_invalid_date", 409) from None
    if action == "select":
        options = [o["name"] for o in element.get("options", []) if not o.get("disabled")]
        if not value or len(value)>200 or options.count(value)!=1:
            raise BrowserError("The option label is unavailable or ambiguous", "batch_option_unavailable", 409)


def action_options(targets, values):
    available = {"scroll", "wait", "done", "help"}
    for element in targets:
        available.update(target_actions(element, has_values=bool(values)))
    return {name: text for name, text in ACTION_DESCRIPTIONS.items() if name in available}


def semantic_page(observation):
    """Receipt identities remain execution fences rather than model evidence."""
    page = {
        key: value
        for key, value in observation.items()
        if key not in {"observation_id", "page_epoch", "page_id", "image", "mime_type"}
    }
    if isinstance(page.get("pages"), list):
        page["pages"] = [
            {key: value for key, value in item.items() if key != "id"}
            if isinstance(item, dict)
            else item
            for item in page["pages"]
        ]
    return page


def eligible_targets(observation):
    return [
        item
        for item in observation.get("elements", [])
        if item.get("role") in TARGET_ROLES and item.get("disabled") not in (True, "true")
    ]


def decision_page(observation, region=None):
    """Project bounded choices; the original observation remains authoritative.

    Keep ordinary controls in view and expose large data regions explicitly.
    Inspecting a region reveals its original targets rather than silently
    discarding rows/cells or making hundreds of targets compete at once.
    """
    page = semantic_page(observation)
    aliases = {
        item["ref"]: "t" + str(index)
        for index, item in enumerate(page.get("elements", []))
        if isinstance(item, dict) and isinstance(item.get("ref"), str)
    }
    eligible = eligible_targets(observation)
    ordinary = [item for item in eligible if item["role"] not in {"row", "gridcell"}]
    data = [item for item in eligible if item["role"] in {"row", "gridcell"}]
    # Small pages retain all targets and their original order.
    if len(eligible) <= 100:
        primary, overflow = eligible, []
    else:
        primary, overflow = ordinary[:100], ordinary[100:] + data
    regions = {
        "_region" + str(index // 50): overflow[index : index + 50]
        for index in range(0, len(overflow), 50)
    }
    targets = primary + regions.get(region, [])
    visible = {item["ref"] for item in targets}
    eligible_refs = {item["ref"] for item in eligible}
    # Non-target headings, alerts and disabled controls remain useful evidence.
    visible.update(
        item["ref"] for item in page.get("elements", []) if item["ref"] not in eligible_refs
    )
    contexts, context_ids = {}, {}
    elements = []
    for item in page.get("elements", []):
        if item.get("ref") not in visible:
            continue
        projected = {**item, "ref": aliases[item["ref"]]}
        projected.pop("fingerprint", None)
        if "context" in item:
            projected["context"] = []
            for context in item["context"]:
                signature = json.dumps(context, sort_keys=True)
                if signature not in context_ids:
                    key = "c" + str(len(context_ids))
                    context_ids[signature] = key
                    contexts[key] = context
                projected["context"].append(context_ids[signature])
        elements.append(projected)
    page["elements"] = elements
    if contexts:
        page["contexts"] = contexts
    if regions:
        page["regions"] = {
            key: {
                "targets": len(items),
                "roles": sorted({item["role"] for item in items}),
                "preview": [item.get("name", "") for item in items[:3]],
            }
            for key, items in regions.items()
        }
        page["projection"] = {
            "total_targets": len(eligible),
            "visible_targets": len(targets),
            "selected_region": region if region in regions else None,
            "omitted_targets": len(eligible) - len(targets),
        }
    return page, aliases, targets, regions
