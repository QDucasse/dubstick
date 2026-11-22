import { readBarcodes } from "https://cdn.jsdelivr.net/npm/zxing-wasm@3.1.6/dist/es/reader/index.js";

const toggle = document.getElementById("camera-toggle");
const panel = document.getElementById("camera");
const video = document.getElementById("camera-video");
const status = document.getElementById("camera-status");
const barcodeInput = document.getElementById("barcode");
const form = document.getElementById("search-form");

// Record sleeves only carry retail codes (EAN-13, UPC-A and their short forms).
const READER_OPTIONS = { formats: ["EANUPC"], tryHarder: true, maxNumberOfSymbols: 1 };
const SCAN_INTERVAL_MS = 200;

let stream = null;
let timer = null;
let busy = false;
const canvas = document.createElement("canvas");
const context = canvas.getContext("2d", { willReadFrequently: true });

async function start() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
  } catch (error) {
    status.textContent = `Camera unavailable: ${error.message}`;
    panel.classList.add("on");
    return;
  }

  video.srcObject = stream;
  await video.play();
  panel.classList.add("on");
  toggle.textContent = "✕ Stop";
  status.textContent = "Point the camera at the barcode.";
  timer = setInterval(scanFrame, SCAN_INTERVAL_MS);
}

function stop() {
  clearInterval(timer);
  timer = null;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  panel.classList.remove("on");
  toggle.textContent = "📷 Scan";
}

async function scanFrame() {
  if (busy || !video.videoWidth) return;
  busy = true;
  try {
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    context.drawImage(video, 0, 0);
    const frame = context.getImageData(0, 0, canvas.width, canvas.height);

    const [result] = await readBarcodes(frame, READER_OPTIONS);
    if (result?.isValid && result.text) {
      found(result.text);
    }
  } catch (error) {
    // The WASM module loads on the first frame; a failure there is worth showing.
    status.textContent = `Scanner error: ${error.message}`;
  } finally {
    busy = false;
  }
}

function found(code) {
  stop();
  navigator.vibrate?.(80);
  barcodeInput.value = code;
  htmx.trigger(form, "submit");
}

toggle.addEventListener("click", () => (stream ? stop() : start()));

// After a pick, clear the inputs for the next record; the pick list is replaced by the confirmation.
document.body.addEventListener("queued", () => form.reset());
