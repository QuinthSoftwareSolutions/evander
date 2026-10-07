// Template selection, role and session setup, and preview.
import { elements } from "./team-ui-dom.js";
import {
  escapeAttr,
  escapeHtml,
  formData,
  humanizeToken,
  taskStateLabel,
  text,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamTemplatesController(peers) {

  let templates = [];

  function normalizeTemplates(payload) {
    return Array.isArray(payload?.templates) ? payload.templates : [];
  }

  function fillTemplateSelect() {
    if (!elements.templateSelect) {
      return;
    }
    const currentValue = elements.templateSelect.value;
    const options = templates.map((template) => {
      return [template.id, `${template.name} (${template.id})`];
    });
    elements.templateSelect.innerHTML = options.length
      ? options
          .map(([value, label]) => `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`)
          .join("")
      : `<option value="">No templates available</option>`;
    elements.templateSelect.disabled = !options.length;
    if (options.some(([value]) => value === currentValue)) {
      elements.templateSelect.value = currentValue;
    } else if (options.length) {
      elements.templateSelect.value = options[0][0];
    }
  }

  function renderTemplateSetupFields({ preserveValues = true } = {}) {
    const form = document.querySelector("#setup-template-form");
    const template = selectedTemplate();
    const previousValues = preserveValues && form ? formData(form) : {};
    renderTemplateRoleFields(template, previousValues);
    renderTemplateGitFields(template);
    if (elements.templateRoleFields) {
      peers.selects.fillSelectsIn(elements.templateRoleFields);
      peers.tooltips.decorateFieldTooltips(elements.templateRoleFields);
    }
    renderTemplatePreview();
  }

  function renderTemplateRoleFields(template, previousValues = {}) {
    if (!elements.templateRoleFields) {
      return;
    }
    if (!template) {
      elements.templateRoleFields.innerHTML = "";
      return;
    }

    elements.templateRoleFields.innerHTML = `
      <p class="subheading">Roles</p>
      ${template.roles.map((role) => renderTemplateRoleSessionFields(role, previousValues)).join("")}
    `;
  }

  function renderTemplateRoleSessionFields(role, previousValues) {
    const roleKey = text(role.key);
    const existingAgentField = `${roleKey}_existing_agent_id`;
    const sessionField = `${roleKey}_session_id`;
    const sessionValue = text(previousValues[sessionField]);
    const existingAgentValue = text(previousValues[existingAgentField]);
    const roleLabel = text(role.label) || humanizeToken(roleKey);
    const suggestedRole = roleKey.replaceAll("_", "-");
    return `
      <fieldset class="template-role-fieldset" data-template-role-key="${escapeAttr(roleKey)}">
        <legend>${escapeHtml(roleLabel)}</legend>
        <label>
          Existing agent
          <select
            name="${escapeAttr(existingAgentField)}"
            data-options="agents"
            data-placeholder="Select existing agent"
            data-agent-source-picker
            data-session-field="${escapeAttr(sessionField)}"
            data-suggested-role="${escapeAttr(suggestedRole)}"
            data-current-value="${escapeAttr(existingAgentValue)}"
            data-tooltip-key="template_existing_agent_id"
          ></select>
        </label>
        <label>Session ID <input name="${escapeAttr(sessionField)}" value="${escapeAttr(sessionValue)}" required data-template-session-field data-tooltip-key="template_session_id" /></label>
      </fieldset>
    `;
  }

  function renderTemplateGitFields(template) {
    if (!elements.templateGitFields) {
      return;
    }
    elements.templateGitFields.hidden = !templateUsesGit(template);
  }

  function templateUsesGit(template) {
    return (template?.required_fields || []).some((field) => text(field.path).startsWith("project.git."));
  }

  function renderTemplatePreview() {
    if (!elements.templatePreview) {
      return;
    }
    const template = selectedTemplate();
    if (!template) {
      elements.templatePreview.innerHTML = `<p class="empty-state">No templates available.</p>`;
      return;
    }

    const leadRole = template.roles.find((role) => role.key === "project_lead");
    const leafRoles = template.roles
      .filter((role) => role.reports_to_role_key === (leadRole?.key || template.root_role_key))
      .map((role) => role.key)
      .join("/");
    const hierarchy = leadRole && leafRoles
      ? `${template.root_role_key} -> ${leadRole.key} -> ${leafRoles}`
      : template.root_role_key;

    elements.templatePreview.innerHTML = `
      <div class="template-preview-summary">
        <div class="meta-line">${escapeHtml(template.description)}</div>
        <div class="meta-line">Hierarchy: ${escapeHtml(hierarchy)}</div>
        <div class="meta-line">Required roles: ${escapeHtml(template.required_role_keys.join(", "))}</div>
      </div>

      <section class="template-preview-section">
        <h3>Planned roles</h3>
        <ul class="template-record-list">
          ${template.roles
            .map((role) => {
              const reportsTo = role.reports_to_role_key ? ` reports to ${role.reports_to_role_key}` : "";
              return `
                <li class="record-item">
                  <div class="record-line">
                    <strong>${escapeHtml(role.label)}</strong>
                    <span class="state-pill">${escapeHtml(role.key)}</span>
                  </div>
                  <div class="meta-line">${escapeHtml(role.description)}${escapeHtml(reportsTo)}</div>
                  <div class="meta-line">Max concurrent ephemeral agents: ${role.ephemeral_policy.max_concurrent}</div>
                </li>
              `;
            })
            .join("")}
        </ul>
      </section>

      <section class="template-preview-section">
        <h3>Required fields</h3>
        <ul class="template-record-list">
          ${template.required_fields
            .map((field) => {
              const required = field.required === false ? "optional" : "required";
              const example = field.example ? `Example: ${escapeHtml(field.example)}` : "";
              return `
                <li class="record-item">
                  <div class="record-line">
                    <strong>${escapeHtml(field.label)}</strong>
                    <span class="state-pill ${field.required === false ? "blocked" : "accepted"}">${escapeHtml(required)}</span>
                  </div>
                  <div class="meta-line">${escapeHtml(field.path)}</div>
                  ${field.description ? `<div class="meta-line">${escapeHtml(field.description)}</div>` : ""}
                  ${example ? `<div class="meta-line">${example}</div>` : ""}
                </li>
              `;
            })
            .join("")}
        </ul>
      </section>

      <section class="template-preview-section">
        <h3>Project goals</h3>
        <ul class="template-record-list">
          ${template.project_goals
            .map((goal) => {
              return `
                <li class="record-item">
                  <div class="record-line">
                    <strong>${escapeHtml(goal.description)}</strong>
                    <span class="state-pill ${escapeHtml(goal.state)}">${escapeHtml(goal.state)}</span>
                  </div>
                  <div class="meta-line">${escapeHtml(goal.id)}</div>
                </li>
              `;
            })
            .join("")}
        </ul>
      </section>

      <section class="template-preview-section">
        <h3>Initial tasks</h3>
        <ul class="template-record-list">
          ${template.tasks
            .map((task) => {
              const dependencyLine = (task.dependency_task_ids || []).length
                ? `<div class="meta-line">Depends on ${task.dependency_task_ids
                    .map((dependencyId) => escapeHtml(dependencyId))
                    .join(", ")}</div>`
                : "";
              return `
                <li class="record-item">
                  <div class="record-line">
                    <strong>${escapeHtml(task.title)}</strong>
                    <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
                  </div>
                  <div class="meta-line">${escapeHtml(task.id)} - owner ${escapeHtml(task.owner_role_key)} - creator ${escapeHtml(task.created_by_role_key)} - priority ${task.priority}</div>
                  ${dependencyLine}
                  <div class="meta-line">${renderIdentifierText(task.description)}</div>
                </li>
              `;
            })
            .join("")}
        </ul>
      </section>

      <section class="template-preview-section">
        <h3>Review profile</h3>
        <ul class="template-record-list">
          ${template.review_profiles
            .map((profile) => {
              return `
                <li class="record-item">
                  <div class="record-line">
                    <strong>${escapeHtml(profile.name)}</strong>
                    <span class="state-pill">${escapeHtml(profile.id)}</span>
                  </div>
                  <div class="meta-line">${escapeHtml(profile.description)}</div>
                  <div class="meta-line">Applicable roles: ${escapeHtml((profile.applicable_role_keys || []).join(", ") || "all reviewer roles")}</div>
                  ${renderTemplateCriteria(profile.criteria || [])}
                </li>
              `;
            })
            .join("")}
        </ul>
      </section>

        ${
          templateUsesGit(template)
            ? `<section class="template-preview-section">
                <h3>Git config</h3>
                <div class="meta-line">Git is optional. If used, provide a host repo path and a bare repo value.</div>
                <div class="meta-line">Bare repo values that start with / are treated as paths; other values are treated as names.</div>
              </section>`
            : ""
        }
      `;
  }

  function renderTemplateCriteria(criteria) {
    if (!criteria.length) {
      return "";
    }
    return `
      <ul class="inline-list">
        ${criteria
          .map((criterion) => {
            const required = criterion.required === false ? "optional" : "required";
            const description = criterion.description ? ` - ${escapeHtml(criterion.description)}` : "";
            return `<li>${escapeHtml(criterion.id)}: ${escapeHtml(criterion.label)} (${required})${description}</li>`;
          })
          .join("")}
      </ul>
    `;
  }

  function selectedTemplate() {
    const selectedId = text(elements.templateSelect?.value);
    if (selectedId) {
      return templates.find((template) => template.id === selectedId) || null;
    }
    return templates[0] || null;
  }

  function applyTemplates(payload) {
    templates = normalizeTemplates(payload);
  }

  return {
    applyTemplates,
    renderTemplatePreview,
    renderTemplateSetupFields,
    selectedTemplate,
    templateUsesGit,
  };
}
