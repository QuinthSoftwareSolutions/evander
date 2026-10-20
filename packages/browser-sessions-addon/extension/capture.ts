// A tab-specific video stream keeps background tabs shareable without capturing
// whichever tab happens to be visible. This document never receives host tokens.
declare const chrome: any;
let stream: MediaStream | undefined;
let video: HTMLVideoElement | undefined;
let owner: string | undefined;
let revision = 0;
let idle: ReturnType<typeof setTimeout> | undefined;
function renew() { clearTimeout(idle); idle = setTimeout(stop, 30000); }
function stop() {
  revision++; clearTimeout(idle);
  stream?.getTracks().forEach(track => track.stop()); stream = undefined;
  if (video) { video.pause(); video.srcObject = null; video = undefined; }
  owner = undefined;
}
chrome.runtime.onMessage.addListener((message: any, sender: any, respond: any) => {
  if (message.target !== "tab-capture" || sender.id !== chrome.runtime.id || (sender.url && sender.url !== chrome.runtime.getURL("background.js"))) return false;
  void (async () => {
    if (message.kind === "stop") { stop(); return {}; }
    if (message.kind === "start") {
      stop(); const current = revision;
      const media = await navigator.mediaDevices.getUserMedia({ audio: false, video: {
        mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: message.streamId, maxFrameRate: 5 },
      } as MediaTrackConstraints });
      if (revision !== current) { media.getTracks().forEach(track => track.stop()); throw new Error("disconnected"); }
      stream = media; owner = message.owner; renew();
      video = document.createElement("video"); video.muted = true; video.srcObject = media;
      const playback = video.play();
      const deadline = Date.now() + 6000;
      while (revision === current && (video!.readyState < 2 || !video!.videoWidth)) {
        if (Date.now() > deadline) { stop(); throw new Error("capture_unavailable"); }
        await new Promise(resolve => setTimeout(resolve, 40));
      }
      await playback;
      if (revision !== current) throw new Error("disconnected");
      return {};
    }
    if (message.kind === "frame") {
      if (owner !== message.owner || !video || !stream?.active || video.readyState < 2) throw new Error("capture_unavailable");
      renew();
      const currentVideo = video, current = revision;
      // Wait for a changed frame after an action; static pages need not repaint.
      await new Promise<void>(resolve => {
        const callback = currentVideo.requestVideoFrameCallback(() => { clearTimeout(timer); resolve(); });
        const timer = setTimeout(() => { currentVideo.cancelVideoFrameCallback(callback); resolve(); }, 250);
      });
      if (current !== revision || video !== currentVideo) throw new Error("disconnected");
      const { clientWidth: width, clientHeight: height } = message.viewport;
      if (!(width > 0 && height > 0)) throw new Error("capture_unavailable");
      // Chrome may letterbox tab video. Map its content back to the CSS viewport
      // so screenshots and element/pointer coordinates describe the same page.
      const ratio = width / height;
      const sourceWidth = Math.min(video.videoWidth, video.videoHeight * ratio);
      const sourceHeight = Math.min(video.videoHeight, video.videoWidth / ratio);
      const canvas = document.createElement("canvas");
      const scale = Math.min(1, 1920 / width, 1080 / height);
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      canvas.getContext("2d")!.drawImage(video, (video.videoWidth - sourceWidth) / 2,
        (video.videoHeight - sourceHeight) / 2, sourceWidth, sourceHeight, 0, 0, canvas.width, canvas.height);
      return { data: canvas.toDataURL("image/jpeg", 0.65).split(",")[1] };
    }
    throw new Error("unsupported_action");
  })().then(result => respond({ ok: true, ...result }), error => respond({ ok: false, error: error.message }));
  return true;
});
window.addEventListener("pagehide", stop);
