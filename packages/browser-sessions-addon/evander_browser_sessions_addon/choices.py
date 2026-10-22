"""Generic control affordances for the bounded decision questions."""

ACTION_DESCRIPTIONS = {
    "click": (
        "Activate a button, link, checkbox, radio, tab or menu item, or open a closed "
        "combobox/picker. Open a picker with click, then use its supported keyboard controls. "
        "Do not submit while an earlier requested field is unfinished."
    ),
    "hover": "Move the pointer over a control only when the goal needs its hover behavior.",
    "fill": (
        "Replace a writable text field with an exact supplied literal. This is the normal "
        "choice for setting a field value, including an empty string to clear it. "
        "Use it for the next unfinished field before submitting; never fill a non-editable combobox."
    ),
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
}
ACTION_TARGET_INSTRUCTIONS = (
    " Action and target must describe the same immediate operation. Setting a text field "
    "value normally means fill, not type or key. For a non-editable combobox use click "
    "to open it, then supported keys/options; fill/type cannot operate it."
)


def target_actions(element, *, has_values):
    """Keep input choices consistent with the observed control, without site heuristics."""
    if element.get("disabled") in (True, "true"):
        return []
    actions = ["click", "hover", "key", "scroll"]
    editable = element.get("role") == "textbox" or (
        element.get("role") == "combobox" and element.get("editable") not in (None, False, "false")
    )
    if has_values and editable and element.get("readonly") not in (True, "true"):
        actions.extend(("fill", "type"))
    return actions


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


def decision_page(observation):
    """Bind model-local aliases back to the original endpoint references."""
    page = semantic_page(observation)
    aliases = {
        item["ref"]: "t" + str(index)
        for index, item in enumerate(page.get("elements", []))
        if isinstance(item, dict) and isinstance(item.get("ref"), str)
    }
    page["elements"] = [
        {**item, "ref": aliases[item["ref"]]} if item.get("ref") in aliases else item
        for item in page.get("elements", [])
    ]
    return page, aliases
