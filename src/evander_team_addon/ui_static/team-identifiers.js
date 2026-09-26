/* Identifier presentation only: stored messages and structured values stay intact. */
const FULL_SHA = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i;
const HEX_ID = /^[a-f0-9]{32}$/i;
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const GENERATED_ID = /^[a-z][a-z0-9_-]*[-_][a-f0-9]{32}$/i;

export function isOpaqueIdentifier(value, { explicit = false } = {}) {
  const text = String(value ?? "");
  return FULL_SHA.test(text) || HEX_ID.test(text) || UUID.test(text) || GENERATED_ID.test(text)
    || (explicit && text.length >= 24 && /^[a-z0-9_][a-z0-9_.:-]*$/i.test(text)
      && /(?:^|[-_:])[a-f0-9]{16,}(?=$|[-_:])/i.test(text));
}

function presentation(value, { kind, label } = {}) {
  const type = kind || (FULL_SHA.test(value) ? "sha" : "id");
  const name = label || (type === "sha" ? "SHA" : "ID");
  const short = type === "sha" ? value.slice(0, 8) : `${value.slice(0, 4)}…${value.slice(-6)}`;
  return { type, name, caption: `${name} ${short}` };
}

/** Split plain narrative without interpreting HTML or altering URLs, paths, or code blocks. */
export function identifierSegments(value) {
  const source = String(value ?? "");
  const segments = [];
  const appendText = (text) => {
    if (!text) return;
    const previous = segments.at(-1);
    if (previous?.type === "text") previous.value += text;
    else segments.push({ type: "text", value: text });
  };
  const appendPlain = (text) => {
    const tokens = /[a-z0-9_][a-z0-9_.:@/\\-]*/gi;
    let offset = 0;
    for (const match of text.matchAll(tokens)) {
      const token = match[0].replace(/[.:]+$/, "");
      const preceding = text[match.index - 1] || "";
      const following = text[match.index + match[0].length] || "";
      if (!isOpaqueIdentifier(token) || /[\p{L}\p{N}_./\\@-]/u.test(preceding)
        || /[\p{L}\p{N}_]/u.test(following)) continue;
      appendText(text.slice(offset, match.index));
      segments.push({ type: "identifier", value: token });
      offset = match.index + token.length;
    }
    appendText(text.slice(offset));
  };
  // A standalone inline-code value is an explicit hint, while commands stay literal.
  const protectedText = /(`{3,}|~{3,})[\s\S]*?(?:\1|$)|(`{1,2})([\s\S]*?)\2|(?:[a-z][a-z0-9+.-]*:)?\/\/[^\s<>"']+/gi;
  let offset = 0;
  for (const match of source.matchAll(protectedText)) {
    appendPlain(source.slice(offset, match.index));
    if (match[3] && isOpaqueIdentifier(match[3], { explicit: true })) {
      segments.push({ type: "identifier", value: match[3] });
    } else {
      appendText(match[0]);
    }
    offset = match.index + match[0].length;
  }
  appendPlain(source.slice(offset));
  return segments;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

export function renderIdentifierText(value) {
  return identifierSegments(value).map((segment) => {
    if (segment.type === "text") return escapeHtml(segment.value);
    const { type, name, caption } = presentation(segment.value);
    return `<button type="button" class="identifier-badge" data-identifier-kind="${type}" data-identifier-value="${escapeHtml(segment.value)}" title="${escapeHtml(segment.value)}\nClick to copy" aria-label="Copy ${name}: ${escapeHtml(segment.value)}" aria-live="polite">${escapeHtml(caption)}</button>`;
  }).join("");
}

export function createIdentifierBadge(documentRoot, value, options = {}) {
  const text = String(value);
  const { type, name, caption } = presentation(text, options);
  const button = documentRoot.createElement("button");
  button.type = "button";
  button.className = "identifier-badge";
  button.dataset.identifierKind = type;
  button.dataset.identifierValue = text;
  button.title = `${text}\nClick to copy`;
  button.setAttribute("aria-label", `Copy ${name}: ${text}`);
  button.setAttribute("aria-live", "polite");
  button.textContent = caption;
  return button;
}

export function appendIdentifierText(parent, value, documentRoot = parent.ownerDocument) {
  for (const segment of identifierSegments(value)) {
    parent.append(segment.type === "identifier"
      ? createIdentifierBadge(documentRoot, segment.value)
      : documentRoot.createTextNode(segment.value));
  }
  return parent;
}

async function copyValue(button) {
  const documentRoot = button.ownerDocument;
  const value = button.dataset.identifierValue;
  try {
    const clipboard = documentRoot.defaultView?.navigator.clipboard;
    if (clipboard?.writeText) {
      await clipboard.writeText(value);
      return;
    }
  } catch {
    // Local HTTP installations and restricted browsers may need the legacy path.
  }
  const field = documentRoot.createElement("textarea");
  field.value = value;
  field.readOnly = true;
  field.style.cssText = "position:fixed;opacity:0;pointer-events:none;";
  const focus = documentRoot.activeElement;
  // Keep the fallback inside an open modal; background controls may be inert.
  (button.closest("dialog") || documentRoot.body).append(field);
  try {
    field.select();
    if (!documentRoot.execCommand?.("copy")) throw new Error("Copy unavailable");
  } finally {
    field.remove();
    focus?.focus({ preventScroll: true });
  }
}

export function attachIdentifierCopy(root) {
  const timers = new Map();
  const handler = async (event) => {
    const button = event.target.closest?.("button[data-identifier-value]");
    if (!button || !root.contains(button)) return;
    event.preventDefault();
    event.stopPropagation();
    const original = button.dataset.identifierCaption || button.textContent;
    button.dataset.identifierCaption = original;
    clearTimeout(timers.get(button));
    try {
      await copyValue(button);
      button.textContent = "Copied";
      button.dataset.copyState = "copied";
    } catch {
      button.textContent = "Copy failed";
      button.dataset.copyState = "error";
    }
    timers.set(button, setTimeout(() => {
      button.textContent = original;
      delete button.dataset.copyState;
      timers.delete(button);
    }, 1800));
  };
  root.addEventListener("click", handler);
  return () => {
    root.removeEventListener("click", handler);
    for (const timer of timers.values()) clearTimeout(timer);
  };
}
