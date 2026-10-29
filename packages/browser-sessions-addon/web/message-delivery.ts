const labels: Record<string, string> = {
  pending: "Sending…", queued: "Queued for the next turn", steered: "Added to the active turn",
  started: "Delivered", delivery_unknown: "Delivery uncertain — check with your assistant before retrying",
  failed: "Delivery failed", cancelled: "Cancelled", answered: "Answered",
};
export function messageDelivery(root: HTMLElement, updates: any[], retry?: (id: string) => void) {
  const byId = new Map(updates.map(value => [value.id, value]));
  for (const item of Array.from(root.querySelectorAll<HTMLElement>("[data-message-id]"))) {
    const update = byId.get(item.dataset.messageId);
    if (!update) continue;
    let status = item.querySelector<HTMLElement>(".message-delivery");
    if (!status) {
      status = document.createElement("small"); status.className = "message-delivery";
      status.setAttribute("role", "status"); item.append(status);
    }
    const state = update.status === "answered" ? "answered" : update.delivery_status ?? "pending";
    status.dataset.delivery = state;
    const label = labels[state] ?? "Sending…";
    if (status.textContent !== label) status.textContent = label;
    let button = item.querySelector<HTMLButtonElement>(".retry-delivery");
    if (state === "delivery_unknown" && retry) {
      if (!button) {
        button = document.createElement("button"); button.className = "retry-delivery";
        button.type = "button"; button.textContent = "Retry message"; item.append(button);
      }
      button.disabled = false;
      button.onclick = () => { button!.disabled = true; retry(update.id); };
    } else button?.remove();
  }
}
