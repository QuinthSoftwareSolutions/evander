export {};
declare const chrome: any;
const request = new URLSearchParams(location.hash.slice(1)).get("request");
history.replaceState(null, "", location.pathname);
const allow = document.getElementById("allow") as HTMLButtonElement;
const status = document.getElementById("status")!;
allow.addEventListener("click", async () => {
  allow.disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    stream.getTracks().forEach(track => track.stop());
    await chrome.runtime.sendMessage({ kind: "microphone_permission_result", request_id: request,
      granted: true }).catch(() => {});
    window.close();
  } catch {
    status.textContent = "Microphone access was denied or unavailable. Check Chrome and system microphone permissions, then try again.";
    allow.disabled = false;
  }
});
document.getElementById("cancel")!.addEventListener("click", () => window.close());
