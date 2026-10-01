// Shared formatting and form field helpers.


function prioritySortValue(priority) {
  const order = { high: 0, normal: 1, low: 2 };
  return order[text(priority)] ?? 1;
}

function formatCount(value) {
  return numberOrZero(value).toLocaleString(undefined, {
    maximumFractionDigits: 0,
  });
}

function formatUsd(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "$0.00";
  }
  if (Math.abs(amount) >= 1000) {
    return amount.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    });
  }
  if (Math.abs(amount) >= 10) {
    return amount.toLocaleString(undefined, {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  return amount.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  });
}

function renderSelectOptions(options, { selectedValue = "", placeholder = "Default", includePlaceholder = true } = {}) {
  const selected = text(selectedValue);
  return `${includePlaceholder ? `<option value="">${escapeHtml(placeholder)}</option>` : ""}${options
    .map(([value, label, tooltip]) => {
      const optionValue = text(value);
      const optionTooltip = text(tooltip);
      return `<option value="${escapeAttr(optionValue)}"${optionTooltip ? ` title="${escapeAttr(optionTooltip)}"` : ""}${optionValue === selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("")}`;
}

function renderMultiSelectOptions(options, selectedValues = []) {
  const selected = new Set(splitList(selectedValues));
  return options
    .map(([value, label, tooltip]) => {
      const optionValue = text(value);
      const optionTooltip = text(tooltip);
      return `<option value="${escapeAttr(optionValue)}"${optionTooltip ? ` title="${escapeAttr(optionTooltip)}"` : ""}${selected.has(optionValue) ? " selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
}

function clampPage(page, totalPages) {
  return Math.min(Math.max(1, Number(page) || 1), Math.max(1, totalPages));
}

function isFutureTimestamp(value) {
  const raw = text(value);
  if (!raw) {
    return false;
  }
  const timestamp = Date.parse(raw);
  return Number.isFinite(timestamp) && timestamp > Date.now();
}

function uniqueTextList(value) {
  const items = Array.isArray(value) ? value : [];
  const seen = new Set();
  const result = [];
  items.forEach((item) => {
    const entry = text(item);
    if (!entry || seen.has(entry)) {
      return;
    }
    seen.add(entry);
    result.push(entry);
  });
  return result;
}

function readablePreview(value, limit = 220) {
  const normalized = text(value).replace(/\s+/g, " ");
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(1, limit - 1)).trimEnd()}…`;
}

function formData(form) {
  const values = Object.fromEntries(new FormData(form).entries());
  form.querySelectorAll("select[multiple][name]").forEach((field) => {
    values[field.name] = Array.from(field.selectedOptions)
      .map((option) => option.value)
      .filter(Boolean);
  });
  return values;
}

function formIdentifier(form) {
  return form.getAttribute("id") || "";
}

function formSemanticIdentifier(form) {
  return form?.dataset?.formId || formIdentifier(form);
}

async function responseText(response) {
  const textValue = await response.text();
  if (!textValue) {
    return `${response.status} ${response.statusText}`;
  }
  try {
    const payload = JSON.parse(textValue);
    return payload.error || textValue;
  } catch {
    return textValue;
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, milliseconds);
  });
}

function optionsFromMap(map, preferredOrder = []) {
  const entries = [...map.entries()];
  const orderMap = new Map(preferredOrder.map((value, index) => [value, index]));
  entries.sort((left, right) => {
    const leftOrder = orderMap.has(left[0]) ? orderMap.get(left[0]) : Number.POSITIVE_INFINITY;
    const rightOrder = orderMap.has(right[0]) ? orderMap.get(right[0]) : Number.POSITIVE_INFINITY;
    if (leftOrder !== rightOrder) {
      return leftOrder - rightOrder;
    }
    return left[1].localeCompare(right[1]) || String(left[0]).localeCompare(String(right[0]));
  });
  return entries;
}

function taskStateLabel(task) {
  if (!task || task.state !== "waiting_for_review") {
    return humanizeToken(task?.state);
  }
  if (task.waiting_reason === "superior_acceptance") {
    return "Waiting for acceptance";
  }
  return "Waiting for review";
}

function humanizeToken(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/\b([a-z])/g, (match) => match.toUpperCase());
}

function byId(left, right) {
  return String(left.id || "").localeCompare(String(right.id || ""));
}

function byPriority(left, right) {
  return (
    Number(left.priority ?? 0) - Number(right.priority ?? 0) ||
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    byId(left, right)
  );
}

function values(collection) {
  return Object.values(collection || {});
}

function text(value) {
  return String(value ?? "").trim();
}

function domIdPart(value) {
  return text(value).replace(/[^A-Za-z0-9_-]+/g, "-") || "item";
}

function permissionLabel(permission) {
  return humanizeToken(permission);
}

function numberOrZero(value) {
  const parsed = Number.parseInt(text(value), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function numberOrNull(value) {
  const raw = text(value);
  if (!raw) {
    return null;
  }
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

function splitList(value) {
  if (Array.isArray(value)) {
    return value.map(text).filter(Boolean);
  }
  return text(value)
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseGoals(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [id, description = "", stateValue = "pending"] = line
        .split("|")
        .map((part) => part.trim());
      return {
        id,
        description: description || id,
        state: stateValue || "pending",
      };
    });
}

function goalsToText(goals) {
  return (goals || [])
    .map((goal) => [goal.id, goal.description, goal.state || "pending"].join(" | "))
    .join("\n");
}

function parseCriterionResults(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [criterion_id, stateValue = "passed", finding = "", evidenceRefs = ""] = line
        .split("|")
        .map((part) => part.trim());
      return {
        criterion_id,
        state: stateValue || "passed",
        finding,
        evidence_refs: splitList(evidenceRefs),
      };
    });
}

function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, (char) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char];
  });
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function fieldText(form, name) {
  if (!form || !name) {
    return "";
  }
  return text(form.elements?.[name]?.value);
}

function setFieldValue(form, name, value) {
  if (!form || !name) {
    return;
  }
  const field = form.elements?.[name];
  if (!field) {
    return;
  }
  field.value = text(value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.dispatchEvent(new Event("change", { bubbles: true }));
}

export { prioritySortValue, formatCount, formatUsd, renderSelectOptions, renderMultiSelectOptions, clampPage, isFutureTimestamp, uniqueTextList, readablePreview, formData, formIdentifier, formSemanticIdentifier, responseText, delay, optionsFromMap, taskStateLabel, humanizeToken, byId, byPriority, values, text, domIdPart, permissionLabel, numberOrZero, numberOrNull, splitList, parseGoals, goalsToText, parseCriterionResults, escapeHtml, escapeAttr, fieldText, setFieldValue };
