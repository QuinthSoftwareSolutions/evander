// Field and role permission help.
import { fieldTooltips, rolePermissionTooltips } from "./team-ui-config.js";
import {
  escapeAttr,
  escapeHtml,
  formSemanticIdentifier,
  permissionLabel,
  splitList,
  text,
} from "./team-ui-format.js";

export function createTeamTooltipsController(peers) {

  let tooltipCounter = 0;

  function decorateFieldTooltips(root = document) {
    root.querySelectorAll("form label").forEach((label) => {
      if (label.dataset.tooltipBound === "true") {
        return;
      }
      const field = label.querySelector("input[name], select[name], textarea[name]");
      if (!field) {
        return;
      }
      const tooltip = tooltipForField(label, field);
      if (!tooltip) {
        return;
      }
      label.dataset.tooltipBound = "true";
      label.classList.add("has-tooltip");
      insertTooltip(label, field, tooltip);
    });
  }

  function tooltipForField(label, field) {
    const form = field.form;
    const formId = formSemanticIdentifier(form);
    const aliasedFormId = formId === "create-team-from-teams-form" ? "create-team-form" : "";
    const fieldName = text(field.name);
    const tooltipKey = text(field.dataset.tooltipKey);
    const editType = text(form?.dataset?.editType);
    const keys = [];
    if (editType) {
      keys.push(`edit:${editType}:${fieldName}`);
    }
    if (formId) {
      keys.push(`${formId}:${fieldName}`);
      if (tooltipKey) {
        keys.push(`${formId}:${tooltipKey}`);
      }
    }
    if (aliasedFormId) {
      keys.push(`${aliasedFormId}:${fieldName}`);
      if (tooltipKey) {
        keys.push(`${aliasedFormId}:${tooltipKey}`);
      }
    }
    keys.push(fieldName);
    if (tooltipKey) {
      keys.push(tooltipKey);
    }
    const labelText = text(label.textContent);
    if (labelText) {
      keys.push(labelText.toLowerCase());
    }
    for (const key of keys) {
      if (fieldTooltips[key]) {
        return fieldTooltips[key];
      }
    }
    return "";
  }

  function insertTooltip(label, field, tooltip) {
    const tooltipNode = createTooltipNode(tooltip);
    if (field.type === "checkbox" || label.classList.contains("checkbox-label")) {
      label.append(" ");
      label.appendChild(tooltipNode);
      return;
    }

    const labelRow = document.createElement("span");
    labelRow.className = "field-label-row";
    const labelText = document.createElement("span");
    labelText.className = "field-label-text";
    while (label.firstChild && label.firstChild !== field) {
      labelText.appendChild(label.firstChild);
    }
    labelText.textContent = text(labelText.textContent);
    labelRow.appendChild(labelText);
    labelRow.appendChild(tooltipNode);
    label.insertBefore(labelRow, field);
  }

  function createTooltipNode(tooltip) {
    const wrapper = document.createElement("span");
    wrapper.className = "field-tooltip";
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "button");
    wrapper.setAttribute("aria-label", `Help: ${tooltip}`);

    const icon = document.createElement("span");
    icon.className = "field-tooltip-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "i";

    const bubble = document.createElement("span");
    bubble.className = "field-tooltip-bubble";
    bubble.setAttribute("role", "tooltip");
    bubble.id = `team-field-tooltip-${++tooltipCounter}`;
    bubble.textContent = tooltip;
    wrapper.setAttribute("aria-describedby", bubble.id);

    wrapper.appendChild(icon);
    wrapper.appendChild(bubble);
    return wrapper;
  }

  function rolePermissionTooltip(permission) {
    return rolePermissionTooltips[text(permission)] || "Role-specific Team Add-on capability.";
  }

  function renderRolePermissionTooltip(permission) {
    const tooltip = rolePermissionTooltip(permission);
    const tooltipId = `team-field-tooltip-${++tooltipCounter}`;
    return `
      <span class="field-tooltip permission-token-tooltip" tabindex="0" role="button" aria-label="${escapeAttr(`Help: ${tooltip}`)}" aria-describedby="${escapeAttr(tooltipId)}">
        <span class="field-tooltip-icon" aria-hidden="true">i</span>
        <span class="field-tooltip-bubble" role="tooltip" id="${escapeAttr(tooltipId)}">${escapeHtml(tooltip)}</span>
      </span>
    `;
  }

  function renderRolePermissionToken(permission) {
    const label = permissionLabel(permission);
    return `
      <span class="permission-token" title="${escapeAttr(rolePermissionTooltip(permission))}">
        <span>${escapeHtml(label)}</span>
        ${renderRolePermissionTooltip(permission)}
      </span>
    `;
  }

  function renderRolePermissionsInline(permissions, emptyLabel = "no special permissions") {
    const items = splitList(permissions);
    if (!items.length) {
      return escapeHtml(emptyLabel);
    }
    return items.map(renderRolePermissionToken).join(", ");
  }

  return {
    decorateFieldTooltips,
    renderRolePermissionToken,
    renderRolePermissionsInline,
    rolePermissionTooltip,
  };
}
