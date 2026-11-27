/* ShortsClipper UI — vanilla JS, no build step. */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  job: null,
  presets: [],
  pollTimer: null,
  selected: new Set(),
  preset: "graphic",
  musicFile: "",
  currentClipId: "",
  currentClip: null,
  previewTimer: null,
  previewIndex: 0,
  previewWord: 0,
  lastCandidateSig: "",
  lastResultSig: "",
  autoSelected: false,
  health: null,
  yt: null,
  warnedNoLLM: false,
  warnedNoFFmpeg: false,
  warnedMock: false,
};

const SAMPLE_LINES = [
  ["Nobody", "tells", "you", "this"],
  ["Why", "does", "this", "work?"],
  ["I", "was", "wrong", "about", "money"],
  ["Stop", "doing", "this", "today"],
];

const PRESET_FALLBACK = [
  { id: "graphic", label: "Graphic Pill", description: "Bold uppercase words in a rounded dark bubble — the speech-bubble look. Recommended default." },
  { id: "cinema", label: "Cinema Label", description: "Bold uppercase lower-left label, documentary title-card look." },
  { id: "karaoke", label: "Bold Karaoke", description: "Big bold words with a bright word-by-word highlight and a punchy pop-in." },
  { id: "bold_pop", label: "Bold Pop", description: "Chunky bold sans, white with a dark outline, yellow spoken word, quick pop-in." },
  { id: "minimal", label: "Minimal", description: "Smaller, lighter captions with a soft outline. Calm and editorial." },
];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

const fmtTime = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
};

const scoreClass = (value) => (value >= 32 ? "high" : value >= 24 ? "mid" : "low");

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let payload = null;
    try { payload = await response.json(); } catch { /* non-JSON error */ }
    const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
    error.hint = payload?.error?.hint || "";
    error.code = payload?.error?.code || "";
    throw error;
  }
  return response.json();
}

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toasts").append(node);
  setTimeout(() => node.remove(), 5200);
}

function showError(error) {
  toast(error.message || "Something went wrong", "error");
  if (error.hint) toast(error.hint, "error");
}

/* ------------------------------------------------------------------ settings */

function bindOutput(id, outputId, format) {
  const input = $(id);
  const output = $(outputId);
  const update = () => { output.textContent = format(input.value); };
  input.addEventListener("input", update);
  update();
}

function collectSettings() {
  return {
    clip_count: Number($("#clipCount").value),
    min_length: Number($("#minLen").value),
    max_length: Number($("#maxLen").value),
    aspect: $("#aspect").value,
    captions_enabled: $("#captionsEnabled").checked,
    caption_preset: state.preset,
    caption_font: $("#captionFont").value.trim(),
    caption_size: Number($("#captionSize").value),
    caption_color: $("#captionColor").value.toUpperCase(),
    highlight_color: $("#highlightColor").value.toUpperCase(),
    caption_position: Number($("#captionPosition").value),
    caption_max_words: Number($("#captionMaxWords").value),
    uppercase: $("#uppercase").checked,
    translate_to: $("#translateTo").value,
    bilingual: $("#bilingual").checked,
    silence_removal: $("#silenceRemoval").checked,
    remove_fillers: $("#removeFillers").checked,
    pause_threshold: 0.4,
    zoom_cuts: $("#zoomCuts").checked,
    zoom_interval: Number($("#zoomInterval").value),
    zoom_amount: Number($("#zoomAmount").value) / 100,
    reframe: $("#reframe").value,
    color_grade: $("#colorGrade").value,
    broll: $("#broll").checked,
    broll_max: Number($("#brollMax").value),
    key_point: $("#keyPoint").checked,
    outro_enabled: $("#outroEnabled").checked,
    outro_text: $("#outroText").value.trim(),
    loop_ending: $("#loopEnding").checked,
    ab_variants: $("#abVariants").checked,
    denoise: $("#denoise").checked,
    loudnorm: $("#loudnorm").checked,
    music_file: state.musicFile,
    music_volume: Number($("#musicVolume").value) / 100,
  };
}

function validateSettings(settings) {
  if (!settings.outro_text && settings.outro_enabled) throw new Error("Add outro text or turn the CTA off.");
  if (settings.min_length > settings.max_length) throw new Error("Minimum length must be below maximum length.");
  if (settings.max_length - settings.min_length < 4) throw new Error("Leave at least a 4 second range between min and max.");
}

function setupPresets() {
  const grid = $("#presetGrid");
  grid.innerHTML = state.presets.map((preset) => `
    <button type="button" class="preset-card" data-preset="${esc(preset.id)}" role="radio" aria-checked="${preset.id === state.preset}">
      <b>${esc(preset.label)}</b>
      <span class="preset-sample">Nobody <i>tells</i><br/>you this</span>
    </button>`).join("");
  grid.addEventListener("click", (event) => {
    const card = event.target.closest(".preset-card");
    if (!card) return;
    state.preset = card.dataset.preset;
    $$(".preset-card", grid).forEach((node) => node.setAttribute("aria-checked", String(node === card)));
    updatePreview();
  });
}

function updatePreview() {
  const preset = state.presets.find((item) => item.id === state.preset) || PRESET_FALLBACK[0];
  const caption = $("#captionPreview");
  const frame = $("#phoneFrame");
  const color = $("#captionColor").value;
  const highlight = $("#highlightColor").value;
  const size = Number($("#captionSize").value);
  const position = Number($("#captionPosition").value);

  frame.style.setProperty("--cap-color", color);
  frame.style.setProperty("--cap-highlight", highlight);
  frame.style.setProperty("--cap-pos", String(Math.max(16, position)));
  caption.dataset.preset = state.preset;
  caption.style.fontSize = `${Math.max(11, (frame.clientHeight || 480) * size / 1920)}px`;
  caption.style.setProperty("--cap-color", color);
  caption.style.setProperty("--cap-highlight", highlight);
  caption.style.bottom = `${Math.max(16, position)}%`;

  $("#previewPresetName").textContent = preset.label;
  $("#previewPresetDesc").textContent = preset.description;
  $("#swatchText").style.background = color;
  $("#swatchHighlight").style.background = highlight;
  $("#swatchBox").style.background = state.preset === "karaoke" ? "rgba(0,0,0,.64)" : "transparent";
  $("#swatchBox").style.borderColor = state.preset === "karaoke" ? "rgba(255,255,255,.18)" : "rgba(255,255,255,.06)";
  renderPreviewCaption(true);
}

function renderPreviewCaption(reset = false) {
  const caption = $("#captionPreview");
  if (reset) {
    state.previewIndex = 0;
    state.previewWord = 0;
  }
  const words = SAMPLE_LINES[state.previewIndex % SAMPLE_LINES.length];
  const active = state.previewWord % words.length;
  caption.innerHTML = words
    .map((word, index) => `<span class="w ${index === active ? "active" : ""}">${esc(word)}</span>`)
    .join(" ");
  caption.classList.remove("pop");
  void caption.offsetWidth;
  caption.classList.add("pop");
}

function startPreviewLoop() {
  clearInterval(state.previewTimer);
  state.previewTimer = setInterval(() => {
    const words = SAMPLE_LINES[state.previewIndex % SAMPLE_LINES.length];
    state.previewWord += 1;
    if (state.previewWord >= words.length) {
      state.previewWord = 0;
      state.previewIndex += 1;
    }
    renderPreviewCaption();
  }, 620);
}

function resetSettings() {
  $("#clipForm").reset();
  state.preset = "graphic";
  state.musicFile = "";
  $("#musicCurrent").textContent = "No track selected";
  $$(".preset-card").forEach((node) => node.setAttribute("aria-checked", String(node.dataset.preset === state.preset)));
  ["#clipCount", "#minLen", "#maxLen", "#captionSize", "#captionPosition", "#zoomInterval", "#zoomAmount", "#musicVolume"].forEach((selector) => {
    $(selector).dispatchEvent(new Event("input"));
  });
  updatePreview();
  toast("Settings reset to the recommended defaults.");
}

/* ------------------------------------------------------------------ health */

async function loadHealth() {
  try {
    const health = await api("/api/health");
    state.health = health;
    const parts = [
      health.ffmpeg ? "ffmpeg ready" : "ffmpeg missing",
      `LLM: ${health.llm.provider}${health.llm.configured ? "" : " (heuristic)"}`,
      health.pexels ? "Pexels ready" : "B-roll off",
    ];
    $("#healthText").textContent = parts.join(" · ");
    $("#healthDot").className = `status-dot ${health.ffmpeg ? (health.llm.configured ? "ok" : "warn") : "warn"}`;
    $("#footerRetention").textContent = `${health.cleanup_hours}h`;
    if (!health.ffmpeg && !state.warnedNoFFmpeg) {
      state.warnedNoFFmpeg = true;
      toast("ffmpeg is missing. Install it or set FFMPEG_BIN in .env.", "error");
    }
    if (!health.llm.configured && !state.warnedNoLLM) {
      state.warnedNoLLM = true;
      toast("No LLM key found — using the built-in heuristic scorer. Clips are still scored and everything works.", "");
    }
    if (health.transcribe_backend === "mock" && !state.warnedMock) {
      state.warnedMock = true;
      toast("Mock transcription is on: captions are sample text, not your audio. Turn it off for real word-level sync.", "error");
    }
  } catch (error) {
    $("#healthText").textContent = "backend offline";
    $("#healthDot").className = "status-dot warn";
    showError(error);
  }
}

async function loadYouTubeStatus() {
  try {
    state.yt = await api("/api/youtube/status");
    const node = $("#ytStatus");
    if (state.yt.configured && state.yt.authorized) {
      node.className = "yt-status ok";
      node.innerHTML = "YouTube connected. #Shorts is added to the description automatically.";
    } else if (state.yt.configured) {
      node.className = "yt-status warn";
      node.innerHTML = `OAuth is configured but no account is connected. <a href="#" data-yt-connect>Connect YouTube</a>`;
    } else {
      node.className = "yt-status warn";
      node.innerHTML = "YouTube upload is not configured. Add YT_CLIENT_SECRETS to .env to enable it.";
    }
  } catch {
    /* non-fatal */
  }
}

/* ------------------------------------------------------------------ job rendering */

function showProgress(job) {
  $("#progressPanel").hidden = false;
  $("#progressTitle").textContent = job.stage || job.status || "Working…";
  $("#progressPct").textContent = `${Math.round(job.progress || 0)}%`;
  $("#progressFill").style.width = `${Math.min(100, Math.max(0, job.progress || 0))}%`;
  $("#progressFill").style.opacity = job.status === "error" ? ".4" : "1";

  $("#sourceList").innerHTML = (job.sources || []).map((source) => {
    const badge = source.status === "ready" ? "ok" : source.status === "error" ? "err" : source.status === "queued" ? "" : "busy";
    const name = source.title || source.url || "Video";
    return `<div class="source-row">
      <span class="source-badge ${badge}"></span>
      <span class="source-name" title="${esc(source.url)}">${esc(name)}</span>
      <span class="source-stage">${esc(source.stage || source.status || "")}${source.status === "error" ? "" : source.progress ? ` · ${Math.round(source.progress)}%` : ""}</span>
    </div>`;
  }).join("");

  const logs = (job.logs || []).slice(-6);
  $("#logList").innerHTML = logs.map((line) => `<span class="log-line">${esc(line.msg)}</span>`).join("");

  const errorBox = $("#jobError");
  if (job.error) {
    errorBox.hidden = false;
    errorBox.innerHTML = `<b>${esc(job.error.message)}</b>${esc(job.error.hint || "")}`;
  } else {
    errorBox.hidden = true;
    errorBox.innerHTML = "";
  }
}

function candidateSignature(job) {
  return (job.clips || []).map((clip) => `${clip.id}:${clip.status}:${clip.score}:${clip.start}`).join("|");
}

function resultSignature(job) {
  return (job.clips || [])
    .filter((clip) => clip.status !== "candidate")
    .map((clip) => `${clip.id}:${clip.status}:${clip.file_urls?.video || ""}:${clip.variants?.length || 0}:${clip.youtube?.video_id || ""}:${clip.error?.message || ""}`)
    .join("|");
}

function updateSelectedCount() {
  const count = state.selected.size;
  $("#renderCount").textContent = `(${count})`;
  $("#renderBtn").disabled = count === 0;
}

function renderCandidates(job) {
  const candidates = (job.clips || []).filter((clip) => clip.status === "candidate" || clip.status === "error");
  const panel = $("#candidatesPanel");
  panel.hidden = candidates.length === 0;
  if (!candidates.length) return;
  if (!state.autoSelected) {
    candidates.slice(0, Math.min(3, candidates.length)).forEach((clip) => state.selected.add(clip.id));
    state.autoSelected = true;
  }
  const valid = new Set((job.clips || []).map((clip) => clip.id));
  [...state.selected].forEach((id) => { if (!valid.has(id)) state.selected.delete(id); });

  const signature = candidateSignature(job);
  if (signature === state.lastCandidateSig) {
    updateSelectedCount();
    return;
  }
  state.lastCandidateSig = signature;

  const selection = job.selection || {};
  const providerLabel = selection.provider === "mock" || selection.used_fallback
    ? "heuristic selector"
    : `${selection.provider}${selection.model ? ` · ${selection.model}` : ""}`;
  $("#candidateSubhead").textContent = `${candidates.length} candidate${candidates.length === 1 ? "" : "s"} · scored /40 by ${providerLabel} · pick the ones worth rendering`;

  $("#candidateGrid").innerHTML = candidates.map((clip, index) => {
    const scores = clip.scores || {};
    const total = Number(clip.score || 0);
    const checked = state.selected.has(clip.id);
    const warnings = (clip.warnings || []).map((warning) => `<span class="chip warn">${esc(warning)}</span>`).join("");
    const rubric = [
      ["hook", "Hook", scores.hook],
      ["clarity", "Clarity", scores.clarity],
      ["emotion", "Emotion", scores.emotion],
      ["payoff", "Payoff", scores.payoff],
    ].map(([, label, value]) => `
      <div class="rubric-row">
        <span>${label}</span>
        <div class="rubric-bar"><i style="width:${Math.max(0, Math.min(10, Number(value) || 0)) * 10}%"></i></div>
        <b>${Number(value || 0).toFixed(0)}</b>
      </div>`).join("");
    return `<label class="candidate-card ${checked ? "selected" : ""}" data-id="${esc(clip.id)}">
      <div class="candidate-top">
        <input class="check candidate-check" type="checkbox" ${checked ? "checked" : ""} aria-label="Select clip ${index + 1}" />
        <span class="rank">${String(index + 1).padStart(2, "0")}</span>
        <div style="min-width:0;display:flex;flex-direction:column;gap:6px">
          <div class="candidate-title">${esc(clip.hook_text || "Untitled moment")}</div>
          <div class="candidate-meta">
            <span class="chip type">${esc(clip.hook_type || "other")}</span>
            <span class="chip time">${fmtTime(clip.duration)}</span>
            <span class="chip">${fmtTime(clip.start)}–${fmtTime(clip.end)}</span>
            ${warnings}
          </div>
        </div>
        <div class="score-badge"><b>${total.toFixed(1)}</b><span>/ 40</span></div>
      </div>
      <p class="candidate-reason">${esc(clip.reason || "Selected by the scoring rubric.")}</p>
      <div class="rubric">${rubric}</div>
      ${clip.key_point ? `<div class="keypoint"><b>On-screen point:</b> ${esc(clip.key_point)}</div>` : ""}
    </label>`;
  }).join("");

  updateSelectedCount();
}

function variantHtml(variant) {
  if (!variant?.file_urls?.video) return "";
  return `<div class="variant-row"><span>${esc(variant.label || "Hook variant")} · starts ${fmtTime(variant.start)}</span><a href="${esc(variant.file_urls.video)}" download>Download</a></div>`;
}

function renderResults(job) {
  const clips = (job.clips || []).filter((clip) => clip.status !== "candidate");
  const panel = $("#resultsPanel");
  panel.hidden = clips.length === 0;
  if (!clips.length) return;
  const signature = resultSignature(job);
  if (signature === state.lastResultSig) return;
  state.lastResultSig = signature;

  const done = clips.filter((clip) => clip.status === "done").length;
  $("#resultsSubhead").textContent = `${done} of ${(job.clips || []).length} rendered · ${job.settings?.aspect || "9:16"} · captions burned in`;
  $("#zipBtn").disabled = done === 0;

  $("#resultsGrid").innerHTML = clips.map((clip, index) => {
    if (clip.status === "rendering") {
      return `<article class="result-card" data-id="${esc(clip.id)}">
        <div class="result-progress">
          <div class="result-sub" style="margin-bottom:8px"><span>Clip ${index + 1}</span><span>${esc(clip.stage || "Rendering…")}</span></div>
          <div class="progress-track" style="margin:0"><div class="progress-fill" style="width:${Math.max(4, clip.progress || 0)}%"></div></div>
          <p style="margin-top:8px">${Math.round(clip.progress || 0)}%</p>
        </div>
      </article>`;
    }
    if (clip.status === "error") {
      return `<article class="result-card" data-id="${esc(clip.id)}">
        <div class="result-error">
          <b>Clip ${index + 1} failed</b>
          <p style="margin-top:4px">${esc(clip.error?.message || "Render failed.")}</p>
          <p style="margin-top:4px;color:#9a9aab">${esc(clip.error?.hint || "")}</p>
          <button class="action-btn" data-action="retry" data-id="${esc(clip.id)}" style="margin-top:10px">Retry render</button>
        </div>
      </article>`;
    }
    const urls = clip.file_urls || {};
    const variants = (clip.variants || []).map(variantHtml).join("");
    const credits = (clip.credits || []).map((asset) => `<a href="${esc(asset.source_url || "#")}" target="_blank" rel="noopener">${esc(asset.author || "Pexels")}</a>`).join(", ");
    return `<article class="result-card" data-id="${esc(clip.id)}">
      <div class="result-video-wrap">
        <video src="${esc(urls.video || "")}" poster="${esc(urls.thumb || "")}" controls playsinline preload="metadata"></video>
        <div class="result-overlay">
          <span class="pill score">${Number(clip.score || 0).toFixed(1)} / 40</span>
          <span class="pill">${fmtTime(clip.rendered_duration || clip.duration)}</span>
        </div>
      </div>
      <div class="result-body">
        <div class="result-title">${esc(clip.hook_text || "Untitled clip")}</div>
        <div class="result-sub">
          <span>${esc(clip.source_title || "Source")}</span>
          <span>${esc(clip.hook_type || "")}</span>
          <span>${esc((clip.language || "").toUpperCase())}</span>
        </div>
        <div class="result-actions">
          <a class="action-btn primary" href="${esc(clip.download_url || urls.video || "")}" download>Download MP4</a>
          <button class="action-btn" data-action="metadata" data-id="${esc(clip.id)}">Write metadata</button>
          <button class="action-btn" data-action="resync" data-id="${esc(clip.id)}">Sync to audio</button>
          <button class="action-btn" data-action="captions" data-id="${esc(clip.id)}">Edit captions</button>
          <button class="action-btn" data-action="translate" data-id="${esc(clip.id)}">Translate</button>
          <button class="action-btn" data-action="youtube" data-id="${esc(clip.id)}">Upload</button>
          <button class="action-btn" data-action="retry" data-id="${esc(clip.id)}">Re-render</button>
          ${urls.srt ? `<a class="action-btn" href="${esc(urls.srt)}" download>SRT</a>` : ""}
        </div>
        ${variants ? `<div class="result-variants"><span class="credits-note">A/B hook variants</span>${variants}</div>` : ""}
        ${credits ? `<div class="credits-note">B-roll: Pexels / ${credits}</div>` : ""}
        ${(clip.warnings || []).length ? `<div class="credits-note">${esc(clip.warnings.join(" "))}</div>` : ""}
        ${clip.youtube?.url ? `<div class="credits-note">Uploaded: <a href="${esc(clip.youtube.url)}" target="_blank" rel="noopener">${esc(clip.youtube.url)}</a></div>` : ""}
      </div>
    </article>`;
  }).join("");
}

function renderJob(job) {
  state.job = job;
  showProgress(job);
  renderCandidates(job);
  renderResults(job);
  if (job.status === "ready") {
    $("#progressTitle").textContent = "Ready to review";
  } else if (job.status === "done") {
    $("#progressTitle").textContent = "All clips ready";
  } else if (job.status === "error") {
    $("#progressTitle").textContent = "Job failed";
  }
}

/* ------------------------------------------------------------------ polling */

function startPolling(jobId) {
  clearTimeout(state.pollTimer);
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      renderJob(job);
      if (["queued", "processing", "rendering"].includes(job.status)) {
        state.pollTimer = setTimeout(tick, 1600);
      } else if (job.status === "ready") {
        $("#progressPct").textContent = "100%";
      }
    } catch (error) {
      showError(error);
    }
  };
  tick();
}

/* ------------------------------------------------------------------ analyze / render */

async function analyze(event) {
  event.preventDefault();
  const urls = $("#urls").value.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  if (!urls.length) {
    toast("Paste at least one YouTube URL.", "error");
    return;
  }
  let settings;
  try {
    settings = collectSettings();
    validateSettings(settings);
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const button = $("#analyzeBtn");
  button.disabled = true;
  button.querySelector(".btn-label").textContent = "Starting…";
  try {
    const job = await api("/api/analyze", { method: "POST", body: JSON.stringify({ urls, settings }) });
    state.job = job;
    state.selected.clear();
    state.autoSelected = false;
    state.lastCandidateSig = "";
    state.lastResultSig = "";
    $("#candidatesPanel").hidden = true;
    $("#resultsPanel").hidden = true;
    renderJob(job);
    startPolling(job.id);
    $("#progressPanel").scrollIntoView({ behavior: "smooth", block: "center" });
    toast(`Queued ${urls.length} video${urls.length === 1 ? "" : "s"}.`);
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.querySelector(".btn-label").textContent = "Find my best clips";
  }
}

async function renderSelected(ids = null) {
  if (!state.job) return;
  const clipIds = ids || [...state.selected];
  if (!clipIds.length) {
    toast("Select at least one clip to render.", "error");
    return;
  }
  let settings;
  try {
    settings = collectSettings();
    validateSettings(settings);
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const button = $("#renderBtn");
  button.disabled = true;
  try {
    const job = await api(`/api/jobs/${state.job.id}/render`, {
      method: "POST",
      body: JSON.stringify({ clip_ids: clipIds, settings }),
    });
    renderJob(job);
    startPolling(job.id);
    $("#resultsPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    toast(`Rendering ${clipIds.length} clip${clipIds.length === 1 ? "" : "s"}…`);
  } catch (error) {
    showError(error);
  } finally {
    updateSelectedCount();
  }
}

/* ------------------------------------------------------------------ caption editor */

async function openCaptionEditor(clipId) {
  if (!state.job) return;
  const clip = (state.job.clips || []).find((item) => item.id === clipId);
  if (!clip) return;
  try {
    const data = await api(`/api/jobs/${state.job.id}/clips/${clipId}/captions`);
    state.currentClipId = clipId;
    state.currentClip = clip;
    const beats = data.beats || [];
    $("#captionLang").textContent = `${data.language ? data.language.toUpperCase() : "AUTO"} · ${beats.length} beats · times are clip-relative`;
    $("#captionEditor").innerHTML = beats.length
      ? beats.map((beat) => `<div class="caption-row">
          <span>${fmtTime(beat.start)}</span>
          <input type="text" data-beat="${esc(beat.id)}" value="${esc(beat.text)}" maxlength="240" aria-label="Caption at ${fmtTime(beat.start)}" />
        </div>`).join("")
      : `<p class="fine-print">No caption beats were found for this clip.</p>`;
    $("#captionDialog").showModal();
  } catch (error) {
    showError(error);
  }
}

async function saveCaptions() {
  if (!state.job || !state.currentClipId) return;
  const lines = $$("#captionEditor input[data-beat]").map((input) => ({ id: input.dataset.beat, text: input.value }));
  if (!lines.length) return;
  const button = $("#saveCaptions");
  button.disabled = true;
  button.textContent = "Saving…";
  try {
    const settings = collectSettings();
    const job = await api(`/api/jobs/${state.job.id}/clips/${state.currentClipId}/captions`, {
      method: "POST",
      body: JSON.stringify({ lines, settings }),
    });
    $("#captionDialog").close();
    renderJob(job);
    startPolling(job.id);
    toast("Captions updated — re-burning now.");
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = "Save & re-render";
  }
}

async function translateClip(clipId) {
  if (!state.job) return;
  const clip = (state.job.clips || []).find((item) => item.id === clipId);
  const source = (clip?.language || "en").toLowerCase();
  const suggestion = source.startsWith("th") ? "en" : "th";
  const language = window.prompt("Translate captions to which language code? (en, th, es, ja, ko, zh, vi, id, pt-BR)", suggestion);
  if (!language) return;
  try {
    const job = await api(`/api/jobs/${state.job.id}/clips/${clipId}/translate`, {
      method: "POST",
      body: JSON.stringify({ language: language.trim(), settings: collectSettings() }),
    });
    renderJob(job);
    startPolling(job.id);
    toast(`Translating captions to ${language.trim()}…`);
  } catch (error) {
    showError(error);
  }
}

/* ------------------------------------------------------------------ sync */

async function resyncCaptions(clipId) {
  if (!state.job) return;
  try {
    const job = await api(`/api/jobs/${state.job.id}/clips/${clipId}/resync`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    renderJob(job);
    startPolling(job.id);
    toast("Re-timing captions to the speech in your audio...", "ok");
  } catch (error) {
    showError(error);
  }
}

/* ------------------------------------------------------------------ metadata */

async function generateMetadata(clipId) {
  if (!state.job) return;
  try {
    const data = await api(`/api/jobs/${state.job.id}/clips/${clipId}/metadata`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.currentClipId = clipId;
    $("#ytTitle").value = data.title || "";
    $("#ytTitleCount").textContent = `${$("#ytTitle").value.length}/100`;
    const tags = (data.hashtags || []).join(" ");
    $("#ytDescription").value = `${data.description || ""}\n\n${tags}`.replace(/\n{3,}/g, "\n\n").trim();
    $("#ytMetadata").hidden = false;
    $("#ytMetadata").innerHTML = `<b>${data.source === "ai" ? "AI-written" : "Generated"} metadata</b> · Pinned comment: ${esc(data.pinned_comment || "")} <button type="button" class="link-btn" id="copyPinned">Copy comment</button>`;
    const copy = $("#copyPinned");
    if (copy) copy.addEventListener("click", () => {
      navigator.clipboard?.writeText(data.pinned_comment || "");
      toast("Pinned comment copied.");
    });
    loadYouTubeStatus();
    $("#youtubeDialog").showModal();
    toast("Metadata ready — check it before publishing.", "ok");
  } catch (error) {
    showError(error);
  }
}

/* ------------------------------------------------------------------ YouTube */

function openYouTube(clipId) {
  if (!state.job) return;
  const clip = (state.job.clips || []).find((item) => item.id === clipId);
  if (!clip) return;
  state.currentClipId = clipId;
  const title = (clip.hook_text || "New short").slice(0, 100);
  const credits = (clip.credits || []).map((asset) => `${asset.credit} (${asset.source_url})`).join("\n");
  $("#ytTitle").value = title;
  $("#ytDescription").value = [clip.reason || "", credits ? `\nB-roll credits:\n${credits}` : "", "\n#Shorts"].join("\n").trim();
  $("#ytTitleCount").textContent = `${title.length}/100`;
  $("#ytResult").textContent = "";
  loadYouTubeStatus();
  $("#youtubeDialog").showModal();
}

async function uploadYouTube() {
  if (!state.job || !state.currentClipId) return;
  const title = $("#ytTitle").value.trim();
  if (!title) {
    toast("Add a title before uploading.", "error");
    return;
  }
  if (!$("#ytRights").checked) {
    toast("Confirm you own this video or have permission to publish it.", "error");
    return;
  }
  const schedule = $("#ytSchedule").value;
  const publishAt = schedule ? new Date(schedule).toISOString() : "";
  const button = $("#ytUploadBtn");
  button.disabled = true;
  button.textContent = "Uploading…";
  try {
    const result = await api("/api/youtube/upload", {
      method: "POST",
      body: JSON.stringify({
        clip_id: state.currentClipId,
        title,
        description: $("#ytDescription").value,
        privacy: $("#ytPrivacy").value,
        publish_at: publishAt,
        rights_confirmed: true,
      }),
    });
    $("#ytResult").innerHTML = `Uploaded (${esc(result.privacy)}): <a href="${esc(result.url)}" target="_blank" rel="noopener">${esc(result.url)}</a>`;
    toast("Uploaded to YouTube.", "ok");
    if (state.job) {
      state.job = await api(`/api/jobs/${state.job.id}`);
      state.lastResultSig = "";
      renderJob(state.job);
    }
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
    button.textContent = "Upload";
  }
}

/* ------------------------------------------------------------------ zip / cleanup / history */

async function downloadZip() {
  if (!state.job) return;
  try {
    const response = await fetch(`/api/jobs/${state.job.id}/zip`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch { /* ignore */ }
      throw new Error(payload?.error?.message || "ZIP export failed.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `shortclipper_${state.job.id.slice(-6)}.zip`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    toast("ZIP downloaded.", "ok");
  } catch (error) {
    showError(error);
  }
}

async function runCleanup() {
  try {
    const result = await api("/api/cleanup", { method: "POST", body: JSON.stringify({}) });
    toast(`Cleanup done: ${result.files} file(s), ${result.dirs} folder(s), ${result.jobs} job(s) removed.`, "ok");
  } catch (error) {
    showError(error);
  }
}

async function toggleHistory(force) {
  const panel = $("#historyPanel");
  const show = force ?? panel.hidden;
  if (!show) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  try {
    const data = await api("/api/jobs");
    $("#historyList").innerHTML = (data.jobs || []).length
      ? data.jobs.map((job) => `<button class="history-item" data-job="${esc(job.id)}">
          ${job.thumb ? `<img class="history-thumb" src="${esc(job.thumb)}" alt="" />` : `<span class="history-thumb"></span>`}
          <span class="history-main">
            <b>${esc(job.sources?.[0]?.title || job.sources?.[0]?.url || job.id)}</b>
            <span>${job.clip_count} clips · ${job.rendered_count} rendered · ${esc(job.stage || job.status)}</span>
          </span>
          <span class="history-status ${esc(job.status)}">${esc(job.status)}</span>
        </button>`).join("")
      : `<p class="fine-print">No jobs yet.</p>`;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    showError(error);
  }
}

/* ------------------------------------------------------------------ events */

function bindEvents() {
  $("#clipForm").addEventListener("submit", analyze);
  $("#resetSettings").addEventListener("click", resetSettings);
  $("#renderBtn").addEventListener("click", () => renderSelected());
  $("#zipBtn").addEventListener("click", downloadZip);
  $("#cleanupBtn").addEventListener("click", runCleanup);
  $("#selectAll").addEventListener("click", () => {
    const candidates = (state.job?.clips || []).filter((clip) => clip.status === "candidate" || clip.status === "error");
    const allSelected = candidates.every((clip) => state.selected.has(clip.id));
    candidates.forEach((clip) => (allSelected ? state.selected.delete(clip.id) : state.selected.add(clip.id)));
    $$("#candidateGrid .candidate-card").forEach((card) => {
      const checked = state.selected.has(card.dataset.id);
      card.classList.toggle("selected", checked);
      const box = $(".candidate-check", card);
      if (box) box.checked = checked;
    });
    updateSelectedCount();
  });
  $("#candidateGrid").addEventListener("change", (event) => {
    const card = event.target.closest(".candidate-card");
    if (!card) return;
    if (event.target.checked) state.selected.add(card.dataset.id);
    else state.selected.delete(card.dataset.id);
    card.classList.toggle("selected", event.target.checked);
    updateSelectedCount();
  });
  $("#resultsGrid").addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]");
    if (!action) return;
    const clipId = action.dataset.id;
    if (action.dataset.action === "metadata") generateMetadata(clipId);
    if (action.dataset.action === "resync") resyncCaptions(clipId);
    if (action.dataset.action === "captions") openCaptionEditor(clipId);
    if (action.dataset.action === "translate") translateClip(clipId);
    if (action.dataset.action === "youtube") openYouTube(clipId);
    if (action.dataset.action === "retry") renderSelected([clipId]);
  });
  $("#saveCaptions").addEventListener("click", (event) => {
    event.preventDefault();
    saveCaptions();
  });
  $("#captionDialog").addEventListener("close", () => {
    state.currentClipId = "";
    state.currentClip = null;
  });
  $("#ytUploadBtn").addEventListener("click", (event) => {
    event.preventDefault();
    uploadYouTube();
  });
  $("#ytTitle").addEventListener("input", () => {
    $("#ytTitleCount").textContent = `${$("#ytTitle").value.length}/100`;
  });
  $("#youtubeDialog").addEventListener("click", async (event) => {
    if (!event.target.closest("[data-yt-connect]")) return;
    event.preventDefault();
    try {
      const data = await api("/api/youtube/auth");
      window.open(data.url, "_blank", "noopener");
      toast("Approve access in the new tab, then reopen this dialog.");
    } catch (error) {
      showError(error);
    }
  });
  $("#musicInput").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    $("#musicCurrent").textContent = "Uploading…";
    try {
      const data = await api("/api/music", { method: "POST", body: form });
      state.musicFile = data.name;
      $("#musicCurrent").textContent = `${data.name} · click to remove`;
      toast("Music uploaded. It stays off unless this file is selected.", "ok");
    } catch (error) {
      $("#musicCurrent").textContent = "No track selected";
      showError(error);
    } finally {
      event.target.value = "";
    }
  });
  $("#musicCurrent").addEventListener("click", () => {
    if (!state.musicFile) return;
    state.musicFile = "";
    $("#musicCurrent").textContent = "No track selected";
  });
  $("#recentToggle").addEventListener("click", () => toggleHistory());
  $("#closeHistory").addEventListener("click", () => toggleHistory(false));
  $("#historyList").addEventListener("click", (event) => {
    const item = event.target.closest("[data-job]");
    if (!item) return;
    toggleHistory(false);
    state.selected.clear();
    state.autoSelected = false;
    state.lastCandidateSig = "";
    state.lastResultSig = "";
    $("#candidatesPanel").hidden = true;
    $("#resultsPanel").hidden = true;
    startPolling(item.dataset.job);
    $("#progressPanel").scrollIntoView({ behavior: "smooth", block: "center" });
  });

  ["#captionColor", "#highlightColor", "#captionSize", "#captionPosition"].forEach((selector) => {
    $(selector).addEventListener("input", () => {
      if (selector === "#captionColor") $("#captionColorHex").textContent = $("#captionColor").value.toUpperCase();
      if (selector === "#highlightColor") $("#highlightColorHex").textContent = $("#highlightColor").value.toUpperCase();
      updatePreview();
    });
  });
  ["#clipCount", "#minLen", "#maxLen"].forEach((selector) => {
    $(selector).addEventListener("input", () => {
      if (selector === "#minLen" && Number($("#minLen").value) > Number($("#maxLen").value)) {
        $("#maxLen").value = $("#minLen").value;
        $("#maxLen").dispatchEvent(new Event("input"));
      }
      if (selector === "#maxLen" && Number($("#maxLen").value) < Number($("#minLen").value)) {
        $("#minLen").value = $("#maxLen").value;
        $("#minLen").dispatchEvent(new Event("input"));
      }
    });
  });
  $("#aspect").addEventListener("change", updatePreview);
  window.addEventListener("resize", updatePreview);
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  bindOutput("#clipCount", "#clipCountOut", (value) => value);
  bindOutput("#minLen", "#minLenOut", (value) => `${value}s`);
  bindOutput("#maxLen", "#maxLenOut", (value) => `${value}s`);
  bindOutput("#captionSize", "#captionSizeOut", (value) => value);
  bindOutput("#captionPosition", "#captionPositionOut", (value) => `${value}%`);
  bindOutput("#zoomInterval", "#zoomIntervalOut", (value) => `${value}s`);
  bindOutput("#zoomAmount", "#zoomAmountOut", (value) => `${value}%`);
  bindOutput("#musicVolume", "#musicVolumeOut", (value) => `${value}%`);
  $("#captionColorHex").textContent = $("#captionColor").value.toUpperCase();
  $("#highlightColorHex").textContent = $("#highlightColor").value.toUpperCase();

  try {
    const data = await api("/api/presets");
    state.presets = data.captions || PRESET_FALLBACK;
  } catch {
    state.presets = PRESET_FALLBACK;
  }
  setupPresets();
  bindEvents();
  updatePreview();
  startPreviewLoop();
  await loadHealth();
  await loadYouTubeStatus();
  setInterval(loadHealth, 60000);
}

document.addEventListener("DOMContentLoaded", boot);
