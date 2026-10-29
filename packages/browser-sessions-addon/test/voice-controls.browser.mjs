import test from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { build } from "esbuild";
import { chromium } from "playwright";
const compiled = await build({ entryPoints: ["web/voice-controls.ts"], bundle: true, write: false,
  format: "iife", globalName: "VoiceWidgetFixture", platform: "browser", target: "chrome125",
  define: {"import.meta.url": "window.location.href"} });
const worklet = await build({entryPoints:["web/capture-worklet.ts"], bundle:true, write:false,
  format:"esm", platform:"browser", target:"chrome125"});

async function voiceFixture(run) {
  const server = createServer((request, response) => {
    if (request.url === "/capture-worklet.js") {
      response.setHeader("Content-Type", "text/javascript"); response.end(worklet.outputFiles[0].text); return;
    }
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

test("losing window focus during microphone acquisition stops a late stream and permits retry", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      const acquire = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      let first = true;
      navigator.mediaDevices.getUserMedia = async constraints => {
        if (first) {
          first = false;
          await new Promise(resolve => { window.resumeAcquisition = resolve; });
        }
        const stream = await acquire(constraints); window.microphone = stream; return stream;
      };
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), window.transport);
    });
    const enable = page.getByRole("button", {name:"Enable microphone",exact:true});
    await enable.click();
    await page.waitForFunction(() => typeof window.resumeAcquisition === "function");
    await page.evaluate(() => { window.dispatchEvent(new Event("blur")); window.resumeAcquisition(); });
    await page.waitForFunction(() => window.microphone && !document.querySelector("[data-voice-talk]").disabled);
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "ended");
    assert.equal(await enable.isVisible(), true);
    assert.equal(await page.getByRole("button", {name:"Turn off microphone",exact:true}).isVisible(), false);
    await enable.click();
    await page.getByRole("button", {name:"Hold Talk",exact:true}).waitFor();
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "live");
    await page.evaluate(() => window.widget.dispose());
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
            const samples = new Int16Array(48000);
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


test("an enabled idle microphone stays connected when window focus changes", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      const acquire = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      window.openings = 0;
      navigator.mediaDevices.getUserMedia = async options => {
        window.openings++; return window.microphone = await acquire(options);
      };
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), window.transport);
    });
    await page.getByRole("button", {name:"Enable microphone",exact:true}).click();
    const talk = page.getByRole("button", {name:"Hold Talk",exact:true}); await talk.waitFor();
    await page.evaluate(() => window.dispatchEvent(new FocusEvent("blur")));
    await page.waitForTimeout(250);
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "live");
    assert.equal(await talk.isEnabled(), true);
    assert.equal(await page.evaluate(() => window.openings), 1);
    await page.getByRole("button", {name:"Turn off microphone",exact:true}).click();
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "ended");
  });
});

test("hands-free ignores silence, records complete phrases, and stops when turned off", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      window.phrases = []; window.sent = [];
      window.inputContext = new AudioContext();
      const oscillator = window.inputContext.createOscillator(); oscillator.frequency.value = 220;
      window.level = window.inputContext.createGain(); window.level.gain.value = 0;
      const destination = window.inputContext.createMediaStreamDestination();
      oscillator.connect(window.level); window.level.connect(destination); oscillator.start();
      navigator.mediaDevices.getUserMedia = async () => destination.stream;
      window.microphone = destination.stream;
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), {
        ...window.transport,
        transcribe: async audio => {
          const bytes = await audio.arrayBuffer(); const wav = new DataView(bytes);
          window.phrases.push({type:audio.type, size:bytes.byteLength, rate:wav.getUint32(24,true)});
          if (window.phrases.length === 1) await new Promise(resolve => { window.releaseTranscription = resolve; });
          return {text:"Hands-free phrase " + window.phrases.length};
        },
        send: async text => { window.sent.push(text); },
        speech: async () => new Response(new ReadableStream({
          start(controller) { controller.enqueue(new Uint8Array(96000)); },
          cancel() { window.playbackCancelled = true; },
        }), {headers:{"Content-Type":"audio/pcm"}}),
      });
    });
    await page.getByRole("checkbox", {name:"Hands-free",exact:true}).check();
    await page.getByRole("button", {name:"Enable microphone",exact:true}).click();
    await page.waitForFunction(() => document.querySelector("[data-voice-status]").textContent === "Listening.");
    await page.waitForTimeout(500);
    assert.equal(await page.evaluate(() => window.phrases.length), 0);
    for (let i = 1; i <= 2; i++) {
      await page.evaluate(async () => { await window.inputContext.resume(); window.level.gain.value = 0.2; });
      await page.waitForTimeout(450);
      await page.evaluate(() => { window.level.gain.value = 0; });
      if (i === 1) await page.waitForFunction(() => typeof window.releaseTranscription === "function");
      else await page.waitForTimeout(1200);
    }
    assert.equal(await page.evaluate(() => window.phrases.length), 1, "Only one transcription may run at a time");
    await page.evaluate(() => window.releaseTranscription());
    await page.waitForFunction(() => window.sent.length === 2);
    assert.deepEqual(await page.evaluate(() => window.sent), ["Hands-free phrase 1", "Hands-free phrase 2"]);
    const phrases = await page.evaluate(() => window.phrases);
    assert.equal(phrases.length, 2);
    assert.ok(phrases.every(phrase => phrase.type === "audio/wav" && phrase.rate === 16000 && phrase.size > 16000));
    await page.getByRole("checkbox", {name:"Speak replies",exact:true}).check();
    await page.evaluate(() => window.widget.messages([{id:"interrupt-me",seq:1,reply_to:"q",actor:"agent:assistant"}]));
    await page.waitForFunction(() => document.querySelector("[data-voice-status]").textContent === "Speaking.");
    await page.evaluate(() => { window.level.gain.value = 0.2; });
    await page.waitForFunction(() => window.playbackCancelled);
    await page.waitForTimeout(350);
    await page.evaluate(() => { window.level.gain.value = 0; });
    await page.waitForFunction(() => window.sent.length === 3);
    await page.getByRole("button", {name:"Turn off microphone",exact:true}).click();
    assert.equal(await page.evaluate(() => window.microphone.getAudioTracks()[0].readyState), "ended");
    await page.evaluate(() => { window.level.gain.value = 0.2; });
    await page.waitForTimeout(1100);
    assert.equal(await page.evaluate(() => window.sent.length), 3);
    await page.evaluate(() => { window.widget.dispose(); return window.inputContext.close(); });
  });
});

test("PCM waits through a short fragment and preserves contiguous scheduled audio", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      window.scheduled = [];
      const start = AudioBufferSourceNode.prototype.start;
      AudioBufferSourceNode.prototype.start = function (at) {
        window.scheduled.push({at, duration:this.buffer.duration}); return start.call(this, at);
      };
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), {
        ...window.transport,
        speech: async () => new Response(new ReadableStream({start(controller) {
          controller.enqueue(new Uint8Array(24000));
          window.resumeSpeech = () => {
            controller.enqueue(new Uint8Array(72000));
            window.appendSpeech = () => { controller.enqueue(new Uint8Array(48000)); controller.close(); };
          };
        }}), {headers:{"Content-Type":"audio/pcm"}}),
      });
    });
    await page.getByRole("checkbox", {name:"Speak replies",exact:true}).check();
    await page.evaluate(() => window.widget.messages([{id:"buffered",seq:1,reply_to:"q",actor:"agent:assistant"}]));
    await page.waitForFunction(() => typeof window.resumeSpeech === "function");
    await page.waitForTimeout(700);
    assert.deepEqual(await page.evaluate(() => window.scheduled), [], "A tiny prefix must not play before a provider pause");
    await page.evaluate(() => window.resumeSpeech());
    await page.waitForFunction(() => window.scheduled.length === 1);
    await page.evaluate(() => window.appendSpeech());
    await page.waitForFunction(() => window.scheduled.length === 2);
    const [first, second] = await page.evaluate(() => window.scheduled);
    assert.equal(second.at, first.at + first.duration, "Adjacent received audio must be scheduled without a gap");
    await page.getByRole("button", {name:"Stop audio",exact:true}).click();
  });
});


test("stopping speech while its initial buffer fills cancels without playing a fragment", async () => {
  await voiceFixture(async page => {
    await page.evaluate(() => {
      window.cancelled = false; window.played = 0;
      const start = AudioBufferSourceNode.prototype.start;
      AudioBufferSourceNode.prototype.start = function (...args) { window.played++; return start.apply(this,args); };
      window.widget = new VoiceWidgetFixture.VoiceControls(document.querySelector("#voice"), {
        ...window.transport,
        speech: async () => new Response(new ReadableStream({
          start(controller) { controller.enqueue(new Uint8Array(2400)); },
          cancel() { window.cancelled = true; },
        }), {headers:{"Content-Type":"audio/pcm"}}),
      });
    });
    await page.getByRole("checkbox", {name:"Speak replies",exact:true}).check();
    await page.evaluate(() => window.widget.messages([{id:"cancel-buffer",seq:1,reply_to:"q",actor:"agent:assistant"}]));
    await page.getByRole("button", {name:"Stop audio",exact:true}).click();
    await page.waitForFunction(() => window.cancelled);
    assert.equal(await page.evaluate(() => window.played), 0);
  });
});
