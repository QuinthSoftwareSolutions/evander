import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright";
const compiled = await build({ entryPoints: ["web/voice-controls.ts"], bundle: true, write: false,
  format: "iife", globalName: "VoiceWidgetFixture", platform: "browser", target: "chrome125" });

async function voiceFixture(run) {
  const server = createServer((_request, response) => {
    response.setHeader("Content-Type", "text/html");
    response.end('<!doctype html><div id="voice"><button data-voice-talk disabled>Enable microphone</button><label><input type="checkbox" data-voice-replies disabled>Speak replies</label><button data-voice-stop disabled>Stop audio</button><span data-voice-status role="status"></span></div>');
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
    const page = await browser.newPage();
    await page.goto("http://127.0.0.1:" + server.address().port, { waitUntil: "domcontentloaded" });
    await page.addScriptTag({ content: compiled.outputFiles[0].text });
    await page.evaluate(() => {
      window.transport = {
        info: async () => ({enabled:true,microphone_available:true,max_recording_seconds:5,max_audio_bytes:100000}),
        transcribe: async () => ({text:"Synthetic message"}), speech: async () => new Blob(), send: async () => {},
      };
    });
    await run(page);
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
}

for (const pending of ["microphone setup", "message send"]) {
  test("late " + pending + " cannot disable a replacement voice controller", async () => {
    await voiceFixture(async page => {
      await page.evaluate(pending => {
        const transport = {...window.transport};
        transport[pending === "microphone setup" ? "prepareMicrophone" : "send"] = () => {
          window.pending = true;
          return new Promise(resolve => { window.releaseOld = resolve; });
        };
        window.previous = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), transport);
      }, pending);
      await page.getByRole("button", {name:"Enable microphone",exact:true}).click();
      if (pending === "message send") {
        const talk = page.getByRole("button", {name:"Hold Talk",exact:true});
        await talk.focus(); await page.keyboard.down("Space");
        await page.waitForFunction(() => /Recording\./.test(document.querySelector("[data-voice-status]").textContent));
        await page.waitForTimeout(150); await page.keyboard.up("Space");
      }
      await page.waitForFunction(() => window.pending);
      await page.evaluate(() => {
        window.previous.dispose();
        document.querySelector("[data-voice-talk]").textContent = "Enable microphone";
        window.current = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), window.transport);
      });
      const enable = page.getByRole("button", {name:"Enable microphone",exact:true});
      await page.waitForFunction(() => !document.querySelector("[data-voice-talk]").disabled);
      await page.evaluate(async () => { window.releaseOld(); await new Promise(resolve => setTimeout(resolve, 0)); });
      assert.equal(await enable.isEnabled(), true);
      await enable.click();
      await page.getByRole("button", {name:"Hold Talk",exact:true}).waitFor();
    });
  });
}

test("enabled microphone stays ready across recordings and can be turned off", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      window.openings = 0; window.recordings = [];
      navigator.mediaDevices.getUserMedia = async constraints => {
        window.openings++;
        const stream = await original(constraints); window.microphone = stream; return stream;
      };
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), {
        ...window.transport,
        transcribe: async audio => { window.recordings.push(audio.size); return {text:"Synthetic message"}; },
      });
    });
    await page.getByRole("button", {name:"Enable microphone",exact:true}).click();
    const talk = page.getByRole("button", {name:"Hold Talk",exact:true});
    await talk.waitFor();
    for (let clip = 0; clip < 2; clip++) {
      await talk.focus(); await page.keyboard.down("Space");
      await page.waitForFunction(() => /Recording\./.test(document.querySelector("[data-voice-status]").textContent));
      await page.waitForTimeout(200); await page.keyboard.up("Space");
      await page.waitForFunction(count => window.recordings.length === count && !document.querySelector("[data-voice-talk]").disabled, clip + 1);
    }
    assert.equal(await page.evaluate(() => window.openings), 1);
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "live");
    await page.getByRole("button", {name:"Turn off microphone",exact:true}).click();
    await page.getByRole("button", {name:"Enable microphone",exact:true}).waitFor();
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "ended");
  });
});

test("spoken PCM starts before generation completes and Stop cancels the stream", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      window.streamFinished = false; window.streamCancelled = false;
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), {
        ...window.transport,
        speech: async () => new Response(new ReadableStream({
          start(controller) {
            const samples = new Int16Array(4800);
            for (let i = 0; i < samples.length; i++) samples[i] = Math.round(Math.sin(i * 2 * Math.PI * 440 / 24000) * 1000);
            controller.enqueue(new Uint8Array(samples.buffer));
            window.finishSpeech = () => { window.streamFinished = true; controller.close(); };
          },
          cancel() { window.streamCancelled = true; },
        }), {headers:{"Content-Type":"audio/pcm"}}),
      });
    });
    await page.getByRole("checkbox", {name:"Speak replies"}).check();
    await page.evaluate(() => window.widget.messages([{id:"reply-one",seq:1,reply_to:"question-one",actor:"agent:assistant"}]));
    await page.waitForFunction(() => /Speaking\./.test(document.querySelector("[data-voice-status]").textContent));
    assert.equal(await page.evaluate(() => window.streamFinished), false);
    await page.getByRole("button", {name:"Stop audio",exact:true}).click();
    await page.waitForFunction(() => window.streamCancelled);
  });
});
