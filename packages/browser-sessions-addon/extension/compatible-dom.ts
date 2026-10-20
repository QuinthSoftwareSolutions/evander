// Runs only in Chrome's isolated world in the approved main document. No code,
// selectors or CDP methods supplied by the agent are evaluated here.
export function applicationDOM(method: string, params: any, origin: string): any {
  if (location.origin !== origin) return { error: "origin_denied" };
  const scope = globalThis as any;
  const state = scope.__evanderApplicationDOM ??= {
    ids: new WeakMap<Element, number>(), nodes: new Map<number, WeakRef<Element>>(),
    next: 1, pressed: null, dirty: new WeakSet<Element>(),
  };
  const element = (id: number): HTMLElement => {
    const value = state.nodes.get(id)?.deref();
    if (!value?.isConnected) throw new Error("stale_observation");
    return value as HTMLElement;
  };
  const hit = (x: number, y: number): HTMLElement => {
    let value = document.elementFromPoint(x, y);
    while (value?.shadowRoot) {
      const inner = value.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === value) break;
      value = inner;
    }
    // Never enter another extension's frame, or another document implicitly.
    if (!value || value.closest("iframe,frame,object,embed")) throw new Error("unsupported_target");
    return value as HTMLElement;
  };
  const active = (allowFrame = false): HTMLElement => {
    let value = document.activeElement;
    while (value?.shadowRoot?.activeElement) value = value.shadowRoot.activeElement;
    if (!value || (!allowFrame && value.matches("iframe,frame,object,embed"))) throw new Error("unsupported_target");
    return value as HTMLElement;
  };
  const edit = (target: HTMLElement, text: string, inputType = "insertText") => {
    if (target.matches(":disabled") || target.getAttribute("readonly") !== null) throw new Error("unsupported_target");
    if (!target.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, composed: true,
      cancelable: true, inputType, data: text }))) return;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
      const replace = state.replace === target; state.replace = null;
      const start = replace ? 0 : target.selectionStart ?? target.value.length;
      const end = replace ? target.value.length : target.selectionEnd ?? start;
      const value = target.value.slice(0, start) + text + target.value.slice(end);
      const prototype = target instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(target, value);
      try { target.setSelectionRange(start + text.length, start + text.length); } catch {}
    } else if (target.isContentEditable) {
      const selection = getSelection();
      if (!selection?.rangeCount || !target.contains(selection.anchorNode)) throw new Error("unsupported_target");
      const range = selection.getRangeAt(0); range.deleteContents();
      const node = document.createTextNode(text); range.insertNode(node);
      range.setStartAfter(node); range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
    } else return; // Page-level scanner/shortcut handlers still received their key event.
    state.dirty.add(target);
    target.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType, data: text }));
  };
  try {
    if (method === "Page.getFrameTree") return { frameTree: { frame: { id: "main", url: location.href } } };
    if (method === "Page.getLayoutMetrics") return { cssVisualViewport: {
      clientWidth: innerWidth, clientHeight: innerHeight,
    } };
    if (method === "Accessibility.getFullAXTree") {
      state.nodes.clear();
      const nodes: any[] = [];
      const name = (node: Element) => {
        const labelled = (node.getAttribute("aria-labelledby") ?? "").split(/\s+/)
          .map(id => node.getRootNode() instanceof Document ? document.getElementById(id)?.textContent :
            (node.getRootNode() as ShadowRoot).getElementById(id)?.textContent).filter(Boolean).join(" ");
        return (labelled || node.getAttribute("aria-label") ||
          (node as HTMLInputElement).labels && Array.from((node as HTMLInputElement).labels!).map(label => label.textContent).join(" ") ||
          node.getAttribute("alt") || node.getAttribute("title") ||
          (node instanceof HTMLInputElement && ["button", "submit", "reset"].includes(node.type) ? node.value : node.textContent) || "")
          .replace(/\s+/g, " ").trim().slice(0, 400);
      };
      const walk = (root: Document | ShadowRoot) => {
        for (const node of Array.from(root.querySelectorAll("*"))) {
          if (nodes.length >= 600) return;
          if (node.shadowRoot) walk(node.shadowRoot);
          if (node.closest('[hidden],[inert],[aria-hidden="true"]') ||
              getComputedStyle(node).visibility === "hidden" || getComputedStyle(node).display === "none") continue;
          const tag = node.tagName.toLowerCase();
          const role = node.getAttribute("role")?.split(/\s+/)[0] ||
            (node instanceof HTMLInputElement ? ({checkbox:"checkbox",radio:"radio",button:"button",submit:"button",reset:"button",hidden:""}[node.type] ?? "textbox") :
              ({button:"button",a:node.hasAttribute("href") ? "link" : "",textarea:"textbox",select:"combobox",option:"option",
                h1:"heading",h2:"heading",h3:"heading",h4:"heading",h5:"heading",h6:"heading",dialog:"dialog",tr:"row",td:"gridcell",th:"gridcell"} as Record<string,string>)[tag]);
          if (!role || !["button","link","textbox","combobox","checkbox","radio","tab","menuitem","option","heading","dialog","alert","gridcell","row"].includes(role)) continue;
          let id = state.ids.get(node);
          if (!id) { id = state.next++; state.ids.set(node, id); }
          state.nodes.set(id, new WeakRef(node));
          nodes.push({ backendDOMNodeId: id, role: { value: role }, name: { value: name(node) } });
        }
      };
      walk(document); return { nodes };
    }
    if (method === "DOM.scrollIntoViewIfNeeded") {
      element(params.backendNodeId).scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" }); return {};
    }
    if (method === "DOM.getBoxModel") {
      const rect = element(params.backendNodeId).getBoundingClientRect();
      return { model: { content: [rect.left,rect.top,rect.right,rect.top,rect.right,rect.bottom,rect.left,rect.bottom] } };
    }
    if (method === "DOM.focus") {
      const prior = active(true);
      if (state.dirty.delete(prior)) prior.dispatchEvent(new Event("change", { bubbles: true }));
      const node = element(params.backendNodeId); node.focus();
      if (active() !== node) throw new Error("unsupported_target");
      if (node.isContentEditable && !node.contains(getSelection()?.anchorNode ?? null)) {
        const range = document.createRange(); range.selectNodeContents(node); range.collapse(false);
        getSelection()?.removeAllRanges(); getSelection()?.addRange(range);
      }
      return {};
    }
    if (method === "Input.insertText") {
      const target = active();
      if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target.isContentEditable)) throw new Error("unsupported_target");
      edit(target, params.text); return {};
    }
    if (method === "Input.dispatchKeyEvent") {
      const target = active(), down = params.type === "keyDown";
      const keyCode = params.windowsVirtualKeyCode ?? (params.key?.length === 1 ? params.key.toUpperCase().charCodeAt(0) : 0);
      const options = { key: params.key, code: params.code ?? "", keyCode, which: keyCode,
        ctrlKey: !!(params.modifiers & 2), bubbles: true, composed: true, cancelable: true };
      const accepted = target.dispatchEvent(new KeyboardEvent(down ? "keydown" : "keyup", options));
      if (!down || !accepted) return {};
      if (options.ctrlKey && params.key === "a") {
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) { target.select(); state.replace = target; }
        else if (target.isContentEditable) { const range = document.createRange(); range.selectNodeContents(target); getSelection()?.removeAllRanges(); getSelection()?.addRange(range); }
      } else if (params.text !== undefined) {
        const character = params.text.charCodeAt(0);
        if (target.dispatchEvent(new KeyboardEvent("keypress", { ...options, charCode: character, keyCode: character, which: character }))) {
          if (params.key === "Enter" && !(target instanceof HTMLTextAreaElement) && !target.isContentEditable) {
            if (target instanceof HTMLInputElement) target.form?.requestSubmit();
            else if (target.matches("button,a[href]")) target.click();
          } else if (params.key === " " && target.matches("button,input[type=checkbox],input[type=radio]")) target.click();
          else edit(target, params.text === "\r" ? "\n" : params.text);
        }
      } else if (params.key === "Tab") {
        const items = Array.from(document.querySelectorAll<HTMLElement>('a[href],button,input,textarea,select,[tabindex]'))
          .filter(node => !node.matches(":disabled,[hidden]") && node.tabIndex >= 0 && node.getClientRects().length);
        items[(items.indexOf(target) + 1) % items.length]?.focus();
      } else if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
        const start = target.selectionStart, end = target.selectionEnd;
        if (start !== null && end !== null) {
          if (params.key === "Backspace" || params.key === "Delete") {
            const left = start === end && params.key === "Backspace" ? Math.max(0, start - 1) : start;
            const right = start === end && params.key === "Delete" ? end + 1 : end;
            target.setSelectionRange(left, right); edit(target, "", params.key === "Backspace" ? "deleteContentBackward" : "deleteContentForward");
          } else if (params.key === "ArrowLeft" || params.key === "ArrowRight") {
            const position = params.key === "ArrowLeft" ? Math.max(0, start - 1) : Math.min(target.value.length, end + 1);
            target.setSelectionRange(position, position);
          }
        }
      }
      return {};
    }
    if (method === "Input.dispatchMouseEvent") {
      const target = hit(params.x, params.y);
      const options = { clientX: params.x, clientY: params.y, bubbles: true, composed: true, cancelable: true, button: 0 };
      if (params.type === "mouseWheel") {
        if (!target.dispatchEvent(new WheelEvent("wheel", { ...options, deltaX: params.deltaX, deltaY: params.deltaY }))) return {};
        let scroll: HTMLElement | null = target;
        while (scroll && !(scroll.scrollHeight > scroll.clientHeight && /auto|scroll/.test(getComputedStyle(scroll).overflowY))) scroll = scroll.parentElement;
        (scroll ?? document.scrollingElement)?.scrollBy({ left: params.deltaX, top: params.deltaY, behavior: "instant" });
      } else if (params.type === "mouseMoved") {
        if (state.hovered !== target) {
          if (state.hovered?.isConnected) {
            state.hovered.dispatchEvent(new PointerEvent("pointerleave", { ...options, bubbles: false, pointerId: 1, pointerType: "mouse" }));
            state.hovered.dispatchEvent(new MouseEvent("mouseleave", { ...options, bubbles: false }));
            state.hovered.dispatchEvent(new MouseEvent("mouseout", options));
          }
          target.dispatchEvent(new PointerEvent("pointerenter", { ...options, bubbles: false, pointerId: 1, pointerType: "mouse" }));
          target.dispatchEvent(new MouseEvent("mouseover", options));
          target.dispatchEvent(new MouseEvent("mouseenter", { ...options, bubbles: false }));
          state.hovered = target;
        }
        target.dispatchEvent(new PointerEvent("pointermove", { ...options, pointerId: 1, pointerType: "mouse" }));
        target.dispatchEvent(new MouseEvent("mousemove", options));
      } else if (params.type === "mousePressed") {
        const pointer = target.dispatchEvent(new PointerEvent("pointerdown", { ...options, pointerId: 1, pointerType: "mouse", buttons: 1 }));
        const mouse = target.dispatchEvent(new MouseEvent("mousedown", { ...options, buttons: 1 }));
        state.pressed = pointer && mouse ? target : null;
        if (state.pressed) {
          const prior = active(true);
          if (prior !== target && state.dirty.delete(prior)) prior.dispatchEvent(new Event("change", { bubbles: true }));
          target.focus();
        }
      } else if (params.type === "mouseReleased") {
        target.dispatchEvent(new PointerEvent("pointerup", { ...options, pointerId: 1, pointerType: "mouse" }));
        target.dispatchEvent(new MouseEvent("mouseup", options));
        if (state.pressed === target) target.click();
        state.pressed = null;
      }
      return {};
    }
    if (method === "Page.navigate") { location.assign(params.url); return {}; }
    if (method === "Page.reload") { location.reload(); return {}; }
    throw new Error("compatibility_unsupported");
  } catch (error: any) { return { error: error.message }; }
}
