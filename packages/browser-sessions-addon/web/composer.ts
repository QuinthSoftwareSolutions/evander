/** Enter sends; Shift+Enter and input-method composition retain normal editing. */
export function enterToSend(body: HTMLTextAreaElement, form: HTMLFormElement) {
  body.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!event.repeat && body.value.trim()) form.requestSubmit();
  });
}
