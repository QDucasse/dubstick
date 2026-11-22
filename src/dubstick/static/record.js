// Record a track through the phone's microphone while the record plays, or upload a file,
// and send it for analysis.

const MAX_SECONDS = 60;
const MIN_SECONDS = 10;

// Voice processing is built for calls and smears music: turn all of it off.
const MIC = { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } };

// Safari records MP4/AAC, Chrome and Firefox WebM/Opus; the server decodes both.
const FORMATS = [
  ["audio/webm;codecs=opus", ".webm"],
  ["audio/mp4", ".m4a"],
  ["audio/ogg;codecs=opus", ".ogg"],
];

let session = null;
let uploading = false;

function result(html) {
  const target = document.getElementById("track-result");
  if (target) target.innerHTML = html;
}

function button(trackId) {
  return document.querySelector(`[data-record="${trackId}"]`);
}

function clock(seconds) {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

async function upload(trackId, blob, filename) {
  uploading = true;
  result('<div class="flash">Analysing…</div>');
  const form = new FormData();
  form.append("audio", blob, filename);
  try {
    const response = await fetch(`/tracks/${trackId}/recording`, { method: "POST", body: form });
    result(await response.text());
  } catch (error) {
    result(`<div class="flash error">Upload failed: ${error.message}</div>`);
  } finally {
    uploading = false;
    // Live refreshes were held back meanwhile; catch up now.
    htmx.trigger(document.body, "record-changed");
  }
}

async function start(trackId) {
  const [mimeType, extension] = FORMATS.find(([type]) => MediaRecorder.isTypeSupported(type)) ?? ["", ".audio"];
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia(MIC);
  } catch (error) {
    result(`<div class="flash error">Microphone unavailable: ${error.message}</div>`);
    return;
  }

  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks = [];
  const startedAt = Date.now();
  session = { trackId, recorder, stream };

  recorder.addEventListener("dataavailable", (event) => chunks.push(event.data));
  recorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop());
    clearInterval(session.timer);
    session = null;

    const seconds = (Date.now() - startedAt) / 1000;
    const label = button(trackId);
    if (label) label.textContent = "🎙 Record";
    if (seconds < MIN_SECONDS) {
      result(`<div class="flash error">Too short: record at least ${MIN_SECONDS} seconds.</div>`);
      return;
    }
    upload(trackId, new Blob(chunks, { type: recorder.mimeType }), `recording${extension}`);
  });

  recorder.start();
  session.timer = setInterval(() => {
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    const label = button(trackId);
    if (label) label.textContent = `■ Stop ${clock(seconds)}`;
    if (seconds >= MAX_SECONDS) recorder.stop();
  }, 250);
  result(`<div class="flash">Recording… tap ■ Stop after about 30 seconds.</div>`);
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-record]");
  if (!target || uploading) return;

  const trackId = target.dataset.record;
  if (session && session.trackId === trackId) {
    session.recorder.stop();
  } else if (!session) {
    start(trackId);
  }
});

document.addEventListener("change", (event) => {
  const input = event.target.closest("[data-upload]");
  if (!input || !input.files.length || session || uploading) return;
  const file = input.files[0];
  upload(input.dataset.upload, file, file.name);
  input.value = "";
});

// Swapping the page mid-recording would take the Stop button away.
document.body.addEventListener("htmx:beforeSwap", (event) => {
  if ((session || uploading) && event.detail.target.id === "record-body") {
    event.detail.shouldSwap = false;
  }
});
