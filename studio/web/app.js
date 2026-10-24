/* Articul Studio UI — vanilla JS, no build step.
 * Renders the adjustable parameter panel dynamically from the engine schema
 * returned by GET /api/engines, so engines (placeholder, Articul, …) can
 * change their parameter sets without touching this file.
 */

const $ = (sel, root = document) => root.querySelector(sel);

const state = {
  engines: [],
  enginesById: new Map(),
  projects: [],
  project: null,
  clips: new Map(), // sectionId -> { url, duration }
};

const debounce = (fn, ms) => {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${res.status} ${res.statusText}`);
  }
  return res;
}

/* ---------- rendering ---------- */

function renderEnginePicker() {
  const picker = $("#engine-picker");
  picker.innerHTML = "";
  for (const engine of state.engines) {
    const option = document.createElement("option");
    option.value = engine.id;
    option.textContent = engine.name;
    picker.appendChild(option);
  }
  picker.value = state.project.engine_id;
  const engine = state.enginesById.get(state.project.engine_id);
  $("#engine-note").textContent = engine ? engine.description : "";
}

function renderVoicePicker() {
  const engine = state.enginesById.get(state.project.engine_id);
  const picker = $("#voice-picker");
  picker.innerHTML = "";
  for (const voice of engine.voices) {
    const option = document.createElement("option");
    option.value = voice.id;
    option.textContent = voice.name + (voice.description ? ` — ${voice.description}` : "");
    picker.appendChild(option);
  }
  picker.value = state.project.voice_id;
}

function renderParams() {
  const engine = state.enginesById.get(state.project.engine_id);
  const wrap = $("#params");
  wrap.innerHTML = "";
  for (const spec of engine.params) {
    const value = state.project.settings[spec.key] ?? spec.default;
    if (spec.kind === "toggle") {
      const row = document.createElement("div");
      row.className = "param-toggle";
      row.innerHTML = `<span>${spec.label}</span>`;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = value >= 0.5;
      checkbox.addEventListener("change", () =>
        saveSettings({ [spec.key]: checkbox.checked ? 1 : 0 })
      );
      row.appendChild(checkbox);
      wrap.appendChild(row);
      continue;
    }
    const row = document.createElement("div");
    row.className = "param";
    const readout = Number(value).toFixed(2);
    row.innerHTML = `
      <div class="param-head"><span>${spec.label}</span>
      <span class="param-value">${readout}</span></div>`;
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = spec.min;
    slider.max = spec.max;
    slider.step = spec.step;
    slider.value = value;
    const valueLabel = $(".param-value", row);
    slider.addEventListener("input", () => {
      valueLabel.textContent = Number(slider.value).toFixed(2);
    });
    slider.addEventListener("change", () => saveSettings({ [spec.key]: Number(slider.value) }));
    row.appendChild(slider);
    wrap.appendChild(row);
  }
}

function sectionVoiceValue(section) {
  return section.voice_id ?? ""; // "" = inherit project voice
}

function fillVoiceOptions(select, includeInherit) {
  const engine = state.enginesById.get(state.project.engine_id);
  select.innerHTML = "";
  if (includeInherit) {
    const inherit = document.createElement("option");
    inherit.value = "";
    inherit.textContent = `theo dự án: ${engine.voices.find((v) => v.id === state.project.voice_id)?.name ?? "?"}`;
    select.appendChild(inherit);
  }
  for (const voice of engine.voices) {
    const option = document.createElement("option");
    option.value = voice.id;
    option.textContent = voice.name;
    select.appendChild(option);
  }
}

function renderSections() {
  const wrap = $("#sections");
  wrap.innerHTML = "";
  state.project.sections.forEach((section, index) => {
    const card = $("#section-tpl").content.firstElementChild.cloneNode(true);
    $(".section-index", card).textContent = String(index + 1).padStart(2, "0");
    $(".section-text", card).value = section.text;

    const voiceSelect = $(".section-voice", card);
    fillVoiceOptions(voiceSelect, true);
    voiceSelect.value = sectionVoiceValue(section);
    voiceSelect.addEventListener("change", () =>
      patchProject({ sections: withSection(section.id, { voice_id: voiceSelect.value || null }) })
    );

    $(".section-text", card).addEventListener(
      "input",
      debounce((event) => onSectionText(section.id, event.target.value), 500)
    );
    $(".section-text", card).addEventListener(
      "input",
      debounce(() => refreshPreview(card, $(".section-text", card).value), 250)
    );

    $(".section-generate", card).addEventListener("click", () =>
      generateSection(section.id, card)
    );
    $(".section-play", card).addEventListener("click", () => {
      const audio = $(".section-audio", card);
      if (!audio.src) {
        generateSection(section.id, card).then(() => $(".section-audio", card).play());
      } else {
        audio.play();
      }
    });
    $(".section-delete", card).addEventListener("click", () => {
      const rest = state.project.sections.filter((s) => s.id !== section.id);
      patchProject({ sections: rest });
    });

    const clip = state.clips.get(section.id);
    if (clip) attachClip(card, clip);
    refreshPreview(card, section.text);
    wrap.appendChild(card);
  });
}

function renderProjectPicker() {
  const picker = $("#project-picker");
  picker.innerHTML = "";
  for (const project of state.projects) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = project.title;
    picker.appendChild(option);
  }
  picker.value = state.project.id;
}

function renderAll() {
  $("#project-title").value = state.project.title;
  renderProjectPicker();
  renderEnginePicker();
  renderVoicePicker();
  renderParams();
  renderSections();
}

/* ---------- data actions ---------- */

async function loadEngines() {
  state.engines = await (await api("/api/engines")).json();
  state.enginesById = new Map(state.engines.map((e) => [e.id, e]));
}

async function loadProjects() {
  state.projects = await (await api("/api/projects")).json();
}

async function loadProject(id) {
  state.project = await (await api(`/api/projects/${id}`)).json();
  renderAll();
}

async function patchProject(fields) {
  state.project = await (
    await api(`/api/projects/${state.project.id}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    })
  ).json();
  renderAll();
}

const saveSettingsDebounced = debounce((settings) => patchProject({ settings }), 400);
const saveSettings = (settings) => saveSettingsDebounced(settings);

function withSection(sectionId, fields) {
  return state.project.sections.map((s) =>
    s.id === sectionId ? { ...s, ...fields } : s
  );
}

function onSectionText(sectionId, text) {
  patchProject({ sections: withSection(sectionId, { text }) });
}

async function refreshPreview(card, text) {
  const preview = $(".section-preview", card);
  if (!text.trim()) {
    preview.classList.remove("has");
    return;
  }
  const { normalized } = await (
    await api(`/api/normalize?text=${encodeURIComponent(text)}`)
  ).json();
  if (normalized !== text.trim()) {
    preview.innerHTML = `<b>Đọc là:</b> ${escapeHtml(normalized)}`;
    preview.classList.add("has");
  } else {
    preview.classList.remove("has");
  }
}

function attachClip(card, clip) {
  const audio = $(".section-audio", card);
  audio.src = clip.url;
  $(".section-duration", card).textContent = `${clip.duration.toFixed(1)}s`;
}

async function generateSection(sectionId, card) {
  const button = $(".section-generate", card);
  button.disabled = true;
  button.textContent = "…";
  try {
    const res = await api(
      `/api/projects/${state.project.id}/sections/${sectionId}/synthesize`,
      { method: "POST" }
    );
    const blob = await res.blob();
    const duration = Number(res.headers.get("X-Duration-Seconds") || "0");
    const old = state.clips.get(sectionId);
    if (old) URL.revokeObjectURL(old.url);
    const clip = { url: URL.createObjectURL(blob), duration };
    state.clips.set(sectionId, clip);
    attachClip(card, clip);
  } catch (error) {
    alert(`Lỗi đọc đoạn: ${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "⚡ Đọc";
  }
}

async function playAll() {
  const cards = [...document.querySelectorAll(".section-card")];
  const audioQueue = [];
  for (const card of cards) {
    const text = $(".section-text", card).value.trim();
    if (!text) continue;
    if (!$(".section-audio", card).src) {
      await generateSection(currentSectionId(card), card);
    }
    audioQueue.push($(".section-audio", card));
  }
  for (let i = 0; i < audioQueue.length; i++) {
    audioQueue[i].pause();
    audioQueue[i].currentTime = 0;
  }
  let index = 0;
  const playNext = () => {
    if (index >= audioQueue.length) return;
    const audio = audioQueue[index++];
    audio.removeEventListener("ended", playNext);
    audio.addEventListener("ended", playNext);
    audio.play();
  };
  playNext();
}

function currentSectionId(card) {
  const index = [...document.querySelectorAll(".section-card")].indexOf(card);
  return state.project.sections[index].id;
}

/* ---------- wire-up ---------- */

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

$("#project-title").addEventListener(
  "input",
  debounce((event) => patchProject({ title: event.target.value }), 600)
);

$("#project-picker").addEventListener("change", (event) => loadProject(event.target.value));

$("#engine-picker").addEventListener("change", (event) =>
  patchProject({ engine_id: event.target.value })
);

$("#voice-picker").addEventListener("change", (event) =>
  patchProject({ voice_id: event.target.value })
);

$("#btn-apply-params").addEventListener("click", () => {
  const engine = state.enginesById.get(state.project.engine_id);
  patchProject({ settings: Object.fromEntries(engine.params.map((p) => [p.key, p.default])) });
});

$("#btn-new").addEventListener("click", async () => {
  const engine = state.engines[0];
  const project = await (
    await api("/api/projects", {
      method: "POST",
      body: JSON.stringify({
        title: "Dự án mới",
        engine_id: engine.id,
        voice_id: engine.voices[0].id,
      }),
    })
  ).json();
  await loadProjects();
  await loadProject(project.id);
});

$("#btn-add-section").addEventListener("click", () =>
  patchProject({
    sections: [...state.project.sections, { text: "", voice_id: null, settings: null }],
  })
);

$("#btn-play-all").addEventListener("click", playAll);

$("#btn-export").addEventListener("click", () => {
  window.open(`/api/projects/${state.project.id}/export`, "_blank");
});

(async function init() {
  await loadEngines();
  await loadProjects();
  if (state.projects.length) {
    await loadProject(state.projects[0].id);
  } else {
    $("#btn-new").click();
  }
})();
