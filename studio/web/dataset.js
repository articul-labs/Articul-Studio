/* Articul Data Studio — record/import/review UI for datasets.
 * Vanilla JS, no build step. Talks only to the HTTP contract in
 * studio/plans/2025-10-24-data-studio.md; audio playback goes through the
 * static mount of studio/data (see audioUrl).
 */

const $ = (sel, root = document) => root.querySelector(sel);

const state = {
  datasets: [],
  dataset: null, // GET /api/datasets/{id} doc (dataset fields + .utterances)
  tab: "record",
  pending: [], // {id, text, status: pending|recording|recorded|saving|saved, blob, url, error}
  pendingSeq: 0,
  review: [], // utterance docs of the loaded review queue
  reviewStatus: "recorded",
  speaker: "", // speaker applied to new recordings
  speakerExtras: [], // locally added speakers (contract has no speakers PATCH)
  recording: null, // {pendingId, recorder, chunks} while a take is live
};

const debounce = (fn, ms) => {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
};

async function api(path, options = {}) {
  // FormData must set its own multipart Content-Type — add JSON headers otherwise.
  const headers = options.body instanceof FormData
    ? undefined
    : { "Content-Type": "application/json" };
  const res = await fetch(path, { headers, ...options });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res;
}

/* List endpoints may return a bare array or wrap it ({items: [...]}). */
const asList = (data, key) => (Array.isArray(data) ? data : data?.[key]) ?? [];

/* The plan stores clips at studio/data/datasets/{id}/audio/{utt_id}.wav and
 * utterance.wav holds the path relative to the dataset dir; integration (T4)
 * mounts studio/data as /data. */
function audioUrl(utt) {
  return `/data/datasets/${state.dataset.id}/${utt.wav}`;
}

/* ---------- speakers ---------- */

function allSpeakers(extra = []) {
  return [...new Set([...(state.dataset?.speakers ?? []), ...state.speakerExtras, ...extra])];
}

function fillSpeakerOptions(select, value) {
  select.innerHTML = "";
  for (const name of allSpeakers(value ? [value] : [])) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  if (!select.options.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "(chưa có giọng đọc)";
    select.appendChild(option);
  }
  select.value = value ?? "";
}

function renderSpeakerBar() {
  const picker = $("#speaker-picker");
  fillSpeakerOptions(picker, state.speaker);
  state.speaker = picker.value;
}

/* ---------- rendering ---------- */

function renderDatasetPicker() {
  const picker = $("#dataset-picker");
  picker.innerHTML = "";
  for (const d of state.datasets) {
    const option = document.createElement("option");
    option.value = d.id;
    option.textContent = d.counts
      ? `${d.name} — ${d.counts.accepted ?? 0}✓ ${d.counts.recorded ?? 0}● ${d.counts.rejected ?? 0}✗`
      : d.name;
    picker.appendChild(option);
  }
  if (state.dataset) picker.value = state.dataset.id;
}

function renderStats() {
  const utts = state.dataset?.utterances ?? [];
  const count = (s) => utts.filter((u) => u.status === s).length;
  $("#dataset-stats").textContent = state.dataset
    ? utts.length
      ? `${utts.length} câu · ${count("accepted")}✓ ${count("recorded")}● ${count("rejected")}✗`
      : "Bộ trống — thêm câu bên dưới."
    : "Chưa có bộ dữ liệu — bấm “+ Bộ mới”.";
}

const PENDING_LABEL = {
  pending: "Chờ ghi",
  recording: "Đang ghi…",
  recorded: "Đã ghi",
  saving: "Đang lưu…",
  saved: "Đã lưu ✓",
};

function renderPending() {
  const wrap = $("#pending-list");
  wrap.innerHTML = "";
  for (const item of state.pending) {
    const card = $("#pending-tpl").content.firstElementChild.cloneNode(true);
    $(".pending-index", card).textContent = String(item.id).padStart(2, "0");
    $(".pending-text", card).textContent = item.text;
    $(".pending-state", card).textContent = PENDING_LABEL[item.status];
    card.dataset.status = item.status;

    const recordBtn = $(".pending-record", card);
    const retakeBtn = $(".pending-retake", card);
    const saveBtn = $(".pending-save", card);
    const audio = $(".pending-audio", card);
    if (item.status === "recording") {
      recordBtn.textContent = "■ Dừng";
    } else if (item.url) {
      audio.src = item.url;
      recordBtn.hidden = true;
      retakeBtn.hidden = false;
      saveBtn.hidden = false;
    }
    // Only one take may be live at a time.
    const busy = Boolean(state.recording) && item.status !== "recording";
    recordBtn.disabled = busy;
    retakeBtn.disabled = busy;
    saveBtn.disabled = item.status !== "recorded";
    if (item.error) {
      const err = $(".pending-error", card);
      err.textContent = item.error;
      err.hidden = false;
    }

    recordBtn.addEventListener("click", () => toggleRecord(item.id));
    retakeBtn.addEventListener("click", () => startRecord(item.id));
    saveBtn.addEventListener("click", () => saveItem(item.id));
    wrap.appendChild(card);
  }
}

function renderReview() {
  const wrap = $("#review-list");
  wrap.innerHTML = "";
  if (!state.review.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = state.dataset
      ? "Không có câu nào ở trạng thái này."
      : "Chưa có bộ dữ liệu — bấm “+ Bộ mới”.";
    wrap.appendChild(p);
    return;
  }
  state.review.forEach((utt, index) => {
    const row = $("#review-row-tpl").content.firstElementChild.cloneNode(true);
    row.dataset.uttId = utt.utt_id;
    $(".row-index", row).textContent = String(index + 1).padStart(2, "0");
    $(".row-audio", row).src = audioUrl(utt);
    updateBadge(row, utt.status);

    const transcript = $(".row-transcript", row);
    transcript.value = utt.transcript;
    transcript.addEventListener(
      "input",
      debounce(() => patchUtt(utt.utt_id, { transcript: transcript.value }), 500)
    );

    const speaker = $(".row-speaker", row);
    fillSpeakerOptions(speaker, utt.speaker);
    speaker.addEventListener("change", () => patchUtt(utt.utt_id, { speaker: speaker.value }));

    $(".row-accept", row).addEventListener("click", () => setUttStatus(utt.utt_id, "accepted"));
    $(".row-reject", row).addEventListener("click", () => setUttStatus(utt.utt_id, "rejected"));
    wrap.appendChild(row);
  });
}

function updateBadge(row, status) {
  const badge = $(".status-badge", row);
  badge.textContent = { recorded: "Chờ duyệt", accepted: "Chấp nhận", rejected: "Loại bỏ" }[status] ?? status;
  badge.dataset.status = status;
}

function renderAll() {
  renderDatasetPicker();
  renderSpeakerBar();
  renderStats();
  renderPending();
}

/* ---------- data actions ---------- */

function showPageError(error) {
  const el = $("#page-error");
  el.textContent = error ? `Không tải được dữ liệu: ${error.message}` : "";
  el.hidden = !error;
}

async function loadDatasets() {
  state.datasets = asList(await (await api("/api/datasets")).json(), "datasets");
}

async function loadDataset(id) {
  state.dataset = await (await api(`/api/datasets/${id}`)).json();
  state.speaker = state.speaker || state.dataset.speakers?.[0] || "";
  renderAll();
}

const refreshDataset = () => loadDataset(state.dataset.id);

function switchTab(tab) {
  if (state.tab === tab) return;
  stopActiveRecording();
  state.tab = tab;
  $("#tab-record").classList.toggle("active", tab === "record");
  $("#tab-review").classList.toggle("active", tab === "review");
  $("#tab-record").setAttribute("aria-selected", String(tab === "record"));
  $("#tab-review").setAttribute("aria-selected", String(tab === "review"));
  $("#view-record").hidden = tab !== "record";
  $("#view-review").hidden = tab !== "review";
  if (tab === "review") {
    loadReview().catch(showPageError);
  } else {
    refreshDataset().catch(showPageError);
  }
}

async function loadReview() {
  if (!state.dataset) {
    state.review = [];
    renderReview();
    return;
  }
  const data = await (
    await api(`/api/datasets/${state.dataset.id}/review?status=${state.reviewStatus}`)
  ).json();
  state.review = asList(data, "items");
  renderReview();
}

async function patchUtt(uttId, fields) {
  try {
    await api(`/api/datasets/${state.dataset.id}/utterances/${uttId}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    });
    return true;
  } catch (error) {
    alert(`Không lưu được thay đổi: ${error.message}`);
    return false;
  }
}

async function setUttStatus(uttId, status) {
  if (!(await patchUtt(uttId, { status }))) return;
  const utt = state.review.find((u) => u.utt_id === uttId);
  if (utt) utt.status = status;
  // The queue shows one status at a time — decided rows leave the list.
  state.review = state.review.filter((u) => u.status === state.reviewStatus);
  renderReview();
}

/* ---------- recording ---------- */

function stopActiveRecording() {
  if (state.recording) state.recording.recorder.stop(); // onstop does the cleanup
}

function toggleRecord(pendingId) {
  if (state.recording?.pendingId === pendingId) {
    state.recording.recorder.stop();
  } else {
    startRecord(pendingId);
  }
}

async function startRecord(pendingId) {
  const item = state.pending.find((p) => p.id === pendingId);
  if (!item || state.recording) return;
  item.error = null;
  if (!window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) {
    item.error = "Trình duyệt không hỗ trợ ghi âm (cần HTTPS hoặc localhost).";
    renderPending();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    // Mic denial must surface in the page, never only in the console.
    item.error = error.name === "NotAllowedError"
      ? "Không truy cập được micro: quyền bị từ chối — hãy cấp quyền micro cho trang rồi thử lại."
      : `Không truy cập được micro: ${error.message}`;
    renderPending();
    return;
  }
  let recorder;
  try {
    recorder = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" })
      : new MediaRecorder(stream);
  } catch (error) {
    stream.getTracks().forEach((t) => t.stop());
    item.error = `Không khởi tạo được bộ ghi âm: ${error.message}`;
    renderPending();
    return;
  }
  const chunks = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };
  recorder.onerror = () => {
    stream.getTracks().forEach((t) => t.stop());
    state.recording = null;
    item.status = item.blob ? "recorded" : "pending";
    item.error = "Lỗi trong khi ghi âm — hãy thử lại.";
    renderPending();
  };
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    state.recording = null;
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    if (!blob.size) {
      item.status = item.blob ? "recorded" : "pending";
      item.error = "Không ghi được dữ liệu âm thanh — hãy thử lại.";
    } else {
      if (item.url) URL.revokeObjectURL(item.url);
      item.blob = blob;
      item.url = URL.createObjectURL(blob);
      item.status = "recorded";
    }
    renderPending();
  };
  if (item.url) {
    URL.revokeObjectURL(item.url);
    item.url = null;
    item.blob = null;
  }
  item.status = "recording";
  state.recording = { pendingId, recorder, chunks };
  recorder.start();
  renderPending();
}

function fileName(item) {
  const type = item.blob.type;
  const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
  return `cau-${String(item.id).padStart(2, "0")}.${ext}`;
}

async function saveItem(pendingId) {
  const item = state.pending.find((p) => p.id === pendingId);
  if (!item?.blob || state.recording) return;
  if (!state.speaker) {
    item.error = "Hãy chọn hoặc thêm giọng đọc trước khi lưu.";
    renderPending();
    return;
  }
  item.status = "saving";
  item.error = null;
  renderPending();
  try {
    const form = new FormData();
    form.append("file", item.blob, fileName(item));
    form.append("transcript", item.text);
    form.append("speaker", state.speaker);
    await api(`/api/datasets/${state.dataset.id}/utterances`, { method: "POST", body: form });
    item.status = "saved";
    renderPending();
    await refreshDataset().catch(showPageError);
  } catch (error) {
    item.status = "recorded";
    item.error = `Lưu thất bại: ${error.message}`;
    renderPending();
  }
}

/* ---------- wire-up ---------- */

$("#tab-record").addEventListener("click", () => switchTab("record"));
$("#tab-review").addEventListener("click", () => switchTab("review"));

$("#review-status").addEventListener("change", (event) => {
  state.reviewStatus = event.target.value;
  loadReview().catch(showPageError);
});

$("#dataset-picker").addEventListener("change", async (event) => {
  stopActiveRecording();
  state.speaker = "";
  try {
    await loadDataset(event.target.value);
  } catch (error) {
    showPageError(error);
  }
});

$("#btn-new-dataset").addEventListener("click", async () => {
  const name = window.prompt("Tên bộ dữ liệu:", "Bộ dữ liệu mới");
  if (!name || !name.trim()) return;
  try {
    const dataset = await (
      await api("/api/datasets", { method: "POST", body: JSON.stringify({ name: name.trim() }) })
    ).json();
    await loadDatasets();
    state.speaker = "";
    await loadDataset(dataset.id);
  } catch (error) {
    alert(`Không tạo được bộ dữ liệu: ${error.message}`);
  }
});

$("#btn-add-prompts").addEventListener("click", () => {
  const lines = $("#prompt-bulk").value.split("\n").map((l) => l.trim()).filter(Boolean);
  for (const text of lines) {
    state.pending.push({ id: ++state.pendingSeq, text, status: "pending", blob: null, url: null, error: null });
  }
  $("#prompt-bulk").value = "";
  renderPending();
});

const addSpeaker = () => {
  const name = $("#new-speaker").value.trim();
  if (!name) return;
  if (!allSpeakers().includes(name)) state.speakerExtras.push(name);
  state.speaker = name;
  $("#new-speaker").value = "";
  renderSpeakerBar();
};
$("#btn-add-speaker").addEventListener("click", addSpeaker);
$("#new-speaker").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addSpeaker();
  }
});

$("#btn-import").addEventListener("click", () => {
  if (!state.dataset) return alert("Hãy tạo hoặc chọn một bộ dữ liệu trước.");
  $("#import-file").click();
});

$("#import-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = ""; // allow re-selecting the same zip
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await api(`/api/datasets/${state.dataset.id}/import`, { method: "POST", body: form });
    await refreshDataset().catch(showPageError);
    if (state.tab === "review") await loadReview();
  } catch (error) {
    alert(`Nhập zip thất bại: ${error.message}`);
  }
});

$("#btn-export").addEventListener("click", () => {
  if (!state.dataset) return alert("Hãy tạo hoặc chọn một bộ dữ liệu trước.");
  window.open(`/api/datasets/${state.dataset.id}/export`, "_blank");
});

/* Keyboard shortcuts act on the focused review row; typing fields keep their
 * normal keys (Space must type a space, not play audio). */
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  if (
    target instanceof HTMLElement &&
    (target.tagName === "INPUT" ||
      target.tagName === "TEXTAREA" ||
      target.tagName === "SELECT" ||
      target.isContentEditable)
  ) {
    return;
  }
  if (state.tab !== "review") return;
  const row = target.closest?.(".review-row");
  if (!row) return;
  const uttId = row.dataset.uttId;
  if (event.key === " ") {
    event.preventDefault();
    const audio = $(".row-audio", row);
    if (audio.paused) audio.play();
    else audio.pause();
  } else if (event.key.toLowerCase() === "a") {
    setUttStatus(uttId, "accepted");
  } else if (event.key.toLowerCase() === "r") {
    setUttStatus(uttId, "rejected");
  }
});

(async function init() {
  try {
    await loadDatasets();
    if (state.datasets.length) await loadDataset(state.datasets[0].id);
  } catch (error) {
    showPageError(error);
  }
  renderAll();
})();
