const REMOTE_API_BASE = (window.APP_CONFIG && window.APP_CONFIG.apiBaseUrl) || "/api";
const LOCAL_COMPANION_ORIGIN = (window.APP_CONFIG && window.APP_CONFIG.localCompanionOrigin) || "http://127.0.0.1:8787";
const LOCAL_COMPANION_ORIGINS = Array.from(
  new Set(
    [
      LOCAL_COMPANION_ORIGIN,
      LOCAL_COMPANION_ORIGIN.includes("127.0.0.1") ? LOCAL_COMPANION_ORIGIN.replace("127.0.0.1", "localhost") : "",
      LOCAL_COMPANION_ORIGIN.includes("localhost") ? LOCAL_COMPANION_ORIGIN.replace("localhost", "127.0.0.1") : "",
      "http://127.0.0.1:8787",
      "http://localhost:8787",
    ].filter(Boolean)
  )
);

const NODE_ORDER = ["Planner", "Researcher", "Reader", "Writer", "Reviewer", "Editor", "Translator"];
const MANAGER_LANGUAGE_FALLBACK = "English";
const MODEL_STORAGE_KEY = "researchCompanion.selectedGeminiModel";
const DEFAULT_DATABASE_FILTERS = Object.freeze({
  scopus: true,
  core: true,
  semantic_scholar: true,
  openalex: true,
  arxiv: true,
});
const DATABASE_LABELS = Object.freeze({
  scopus: "Scopus",
  core: "CORE",
  semantic_scholar: "Semantic Scholar",
  openalex: "OpenAlex",
  arxiv: "arXiv",
});

function loadPersistedModel() {
  try {
    return window.localStorage.getItem(MODEL_STORAGE_KEY) || "";
  } catch (_error) {
    return "";
  }
}

function persistSelectedModel(model) {
  try {
    if (model) {
      window.localStorage.setItem(MODEL_STORAGE_KEY, model);
    } else {
      window.localStorage.removeItem(MODEL_STORAGE_KEY);
    }
  } catch (_error) {
    // Ignore storage failures.
  }
}

const state = {
  jobId: null,
  pollTimer: null,
  lastSnapshot: null,
  localProbeTimer: null,
  localCompanionOrigin: "",
  llmAuthMode: "local_companion_required",
  connectedAccountEmail: "",
  apiBase: REMOTE_API_BASE,
  usingLocalCompanion: false,
  quotaAvailable: false,
  lastQuotaKey: "",
  quotaRefreshRecoveringUntil: 0,
  localCompanionConnectedAt: 0,
  selectedModel: loadPersistedModel(),
  availableModels: [],
  defaultModel: "",
  modelsLoaded: false,
  localProbeDebug: {
    status: "idle",
    origin: "",
    checkedAt: "",
    attempts: [],
    note: "",
  },
  chatActivity: "",
  conversationAttachmentIds: [],
  drawerOpen: false,
  filtersOpen: false,
  messages: [
    {
      role: "assistant",
      content:
        "Describe your task in natural language, then attach manuscript drafts, papers, images, PDFs, Word files, or Excel sheets. The workflow will adapt its nodes to revise or write from those materials.",
    },
  ],
  attachments: [],
  searchFilters: {
    deepReview: false,
    databases: { ...DEFAULT_DATABASE_FILTERS },
    publishYearPreset: "any",
    publishYearMin: "",
    publishYearMax: String(new Date().getFullYear()),
  },
  jobNotificationStatus: "",
  lastRenderKey: {
    nodeStatuses: "",
    logs: "",
    manager: "",
    outline: "",
    papers: "",
    review: "",
    draft: "",
    final: "",
    badge: "",
    chat: "",
    attachments: "",
  },
};

const elements = {
  runButton: document.getElementById("run-button"),
  stopButton: document.getElementById("stop-job"),
  copyLogButton: document.getElementById("copy-log"),
  clearButton: document.getElementById("clear-job"),
  executionLog: document.getElementById("execution-log"),
  statusCards: document.getElementById("status-cards"),
  manager: document.getElementById("manager-output"),
  outline: document.getElementById("outline-output"),
  papers: document.getElementById("papers-output"),
  review: document.getElementById("review-output"),
  draft: document.getElementById("draft-output"),
  final: document.getElementById("final-output"),
  jobBadge: document.getElementById("job-badge"),
  downloadMarkdown: document.getElementById("download-markdown"),
  downloadDocx: document.getElementById("download-docx"),
  llmSourceBadge: document.getElementById("llm-source-badge"),
  quotaAccount: document.getElementById("quota-account"),
  quotaTier: document.getElementById("quota-tier"),
  quotaCredits: document.getElementById("quota-credits"),
  modelSelect: document.getElementById("model-select"),
  quotaBars: document.getElementById("quota-bars"),
  companionDebug: document.getElementById("companion-debug"),
  companionDebugTitle: document.getElementById("companion-debug-title"),
  companionDebugBody: document.getElementById("companion-debug-body"),
  retryCompanionDetect: document.getElementById("retry-companion-detect"),
  chatPrompt: document.getElementById("chat-prompt"),
  chatThread: document.getElementById("chat-thread"),
  attachmentButton: document.getElementById("attach-button"),
  attachmentInput: document.getElementById("attachment-input"),
  attachmentList: document.getElementById("attachment-list"),
  filterSummary: document.getElementById("filter-summary"),
  filterButton: document.getElementById("filter-button"),
  filtersBackdrop: document.getElementById("filters-backdrop"),
  filtersSheet: document.getElementById("filters-sheet"),
  filtersClose: document.getElementById("filters-close"),
  filtersApply: document.getElementById("filters-apply"),
  filtersReset: document.getElementById("filters-reset"),
  filterDeepReview: document.getElementById("filter-deep-review"),
  filterDbScopus: document.getElementById("filter-db-scopus"),
  filterDbCore: document.getElementById("filter-db-core"),
  filterDbSemanticScholar: document.getElementById("filter-db-semantic-scholar"),
  filterDbOpenalex: document.getElementById("filter-db-openalex"),
  filterDbArxiv: document.getElementById("filter-db-arxiv"),
  filterDbCount: document.getElementById("filter-db-count"),
  filterYearMin: document.getElementById("filter-year-min"),
  filterYearMax: document.getElementById("filter-year-max"),
  drawerToggle: document.getElementById("drawer-toggle"),
  drawerClose: document.getElementById("drawer-close"),
  drawerBackdrop: document.getElementById("drawer-backdrop"),
  outputDrawer: document.getElementById("output-drawer"),
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function apiUrl(path) {
  return `${state.apiBase}${path}`;
}

function renderLlmSourceBadge() {
  if (!elements.llmSourceBadge) {
    return;
  }
  const usingCliProxy = state.llmAuthMode === "cli_proxy_oauth";
  const needsReauth = state.llmAuthMode === "cli_proxy_reauth_required";
  const email = String(state.connectedAccountEmail || "").trim();
  elements.llmSourceBadge.textContent = usingCliProxy
    ? email
      ? `LLM: User OAuth · ${email}`
      : "LLM: User OAuth"
    : needsReauth
      ? "LLM: Reconnect OAuth"
      : "LLM: Waiting for Local Companion";
  elements.llmSourceBadge.className = `llm-source-badge ${usingCliProxy ? "cli-proxy" : needsReauth ? "danger" : "vertex"}`;
}

function renderModelSelect(enabled = state.modelsLoaded) {
  if (!elements.modelSelect) return;
  const selected = state.selectedModel || "";
  const models = Array.from(new Set([...(state.availableModels || [])]));
  if (selected && !models.includes(selected)) {
    models.unshift(selected);
  }
  const defaultLabel = state.defaultModel ? `Default (${state.defaultModel})` : "Default model";
  elements.modelSelect.innerHTML = [
    `<option value="">${escapeHtml(defaultLabel)}</option>`,
    ...models.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`),
  ].join("");
  elements.modelSelect.value = selected;
  elements.modelSelect.disabled = !enabled && models.length === 0;
}

async function refreshModels() {
  if (!state.usingLocalCompanion) {
    state.availableModels = [];
    state.defaultModel = "";
    state.modelsLoaded = false;
    renderModelSelect(false);
    return;
  }
  try {
    const payload = await fetchJson(apiUrl("/models"));
    state.availableModels = Array.isArray(payload.models) ? payload.models.map(String).filter(Boolean) : [];
    state.defaultModel = String(payload.default_model || "");
    state.modelsLoaded = Boolean(payload.available);
    renderModelSelect(state.modelsLoaded);
  } catch (_error) {
    state.availableModels = [];
    state.modelsLoaded = false;
    renderModelSelect(false);
  }
}

function quotaWarmupActive() {
  return Boolean(
    state.usingLocalCompanion &&
      state.localCompanionConnectedAt &&
      Date.now() - state.localCompanionConnectedAt < 45000
  );
}

function isTransientQuotaWarmupReason(message) {
  const lowered = String(message || "").toLowerCase();
  return [
    "401",
    "unauthenticated",
    "invalid authentication credentials",
    "expected oauth 2 access token",
    "sign in again in researchcompanion.exe",
    "local gemini auth token is missing or expired",
    "no local gemini auth file",
  ].some((token) => lowered.includes(token));
}

function renderQuotaPending(message) {
  state.quotaAvailable = false;
  state.connectedAccountEmail = "";
  state.lastQuotaKey = `pending:${String(message || "")}`;
  renderLlmSourceBadge();
  if (elements.quotaAccount) {
    elements.quotaAccount.textContent = message || "Local companion is starting. Waiting for Gemini session...";
    elements.quotaAccount.hidden = false;
    elements.quotaAccount.removeAttribute("title");
  }
  if (elements.quotaTier) {
    elements.quotaTier.hidden = true;
    elements.quotaTier.textContent = "";
    elements.quotaTier.className = "quota-pill";
  }
  if (elements.quotaCredits) {
    elements.quotaCredits.hidden = true;
    elements.quotaCredits.textContent = "";
    elements.quotaCredits.className = "quota-pill";
  }
  if (elements.quotaBars) {
    elements.quotaBars.className = "quota-bars empty-state";
    elements.quotaBars.textContent = "Waiting for Gemini quota...";
  }
}

function shouldGracefullyRecoverQuotaAuth() {
  const now = Date.now();
  if (state.quotaRefreshRecoveringUntil && now < state.quotaRefreshRecoveringUntil) {
    return true;
  }
  if (state.quotaAvailable || state.connectedAccountEmail) {
    state.quotaRefreshRecoveringUntil = now + 90000;
    return true;
  }
  return false;
}

function truncateInlineText(value, limit = 180) {
  const text = String(value || "").trim();
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}...`;
}

function probeErrorLabel(kind) {
  switch (String(kind || "")) {
    case "timeout":
      return "Timed out";
    case "network_unreachable":
      return "Loopback unreachable";
    case "cors_or_private_network":
      return "Browser blocked loopback";
    case "http_error":
      return "Unexpected HTTP status";
    case "invalid_json":
      return "Invalid JSON";
    case "payload_invalid":
      return "Invalid health payload";
    default:
      return "Probe failed";
  }
}

function probeErrorHint(attempt) {
  const kind = String((attempt && attempt.error_kind) || "");
  switch (kind) {
    case "timeout":
      return "The local API did not answer quickly enough. The companion may still be starting or the machine may be overloaded.";
    case "network_unreachable":
      return "The browser could not reach this loopback origin at all. Check whether ResearchCompanion.exe is running and listening on port 8787.";
    case "cors_or_private_network":
      return "The browser reached loopback but blocked readable access. This usually means an outdated companion build or browser private-network policy mismatch.";
    case "http_error":
      return "The local API answered, but not with the expected 200 OK health response.";
    case "invalid_json":
      return "The endpoint responded, but not with valid JSON.";
    case "payload_invalid":
      return "The endpoint returned JSON, but it was not the expected companion health payload.";
    default:
      return "The companion probe did not complete successfully.";
  }
}

function stripLogTimestamp(line) {
  return String(line || "").replace(/^\[[^\]]+\]\s*/, "");
}

function groupedProbeAttempts(attempts) {
  const byOrigin = new Map();
  for (const attempt of Array.isArray(attempts) ? attempts : []) {
    const origin = String((attempt && attempt.origin) || "Unknown origin");
    byOrigin.set(origin, attempt);
  }
  const ordered = [];
  for (const origin of [...LOCAL_COMPANION_ORIGINS, "Unknown origin"]) {
    if (byOrigin.has(origin)) {
      ordered.push(byOrigin.get(origin));
      byOrigin.delete(origin);
    }
  }
  for (const attempt of byOrigin.values()) {
    ordered.push(attempt);
  }
  return ordered;
}

function renderLocalProbeDebug() {
  if (!elements.companionDebug || !elements.companionDebugBody || !elements.companionDebugTitle) {
    return;
  }
  const debug = state.localProbeDebug || {};
  const attempts = Array.isArray(debug.attempts) ? debug.attempts : [];
  const shouldShow = debug.status === "failed" && attempts.length > 0;
  if (!shouldShow) {
    elements.companionDebug.hidden = true;
    elements.companionDebugTitle.textContent = "Companion detection debug";
    elements.companionDebugBody.innerHTML = "";
    return;
  }

  elements.companionDebug.hidden = false;
  elements.companionDebugTitle.textContent = "Companion detection failed";
  const noteChips = [];
  if (debug.note) {
    noteChips.push(`<span class="companion-debug-chip companion-debug-chip-note">${escapeHtml(debug.note)}</span>`);
  }
  if (debug.checkedAt) {
    noteChips.push(
      `<span class="companion-debug-chip companion-debug-chip-note">${escapeHtml(
        `Last probe ${new Date(debug.checkedAt).toLocaleString()}`
      )}</span>`
    );
  }

  const orderedAttempts = groupedProbeAttempts(attempts);
  const failedCount = orderedAttempts.filter((attempt) => !Boolean(attempt && attempt.ok)).length;
  const successCount = orderedAttempts.length - failedCount;
  const attemptMarkup = orderedAttempts
    .map((attempt) => {
      const ok = Boolean(attempt && attempt.ok);
      const durationMs = attempt && typeof attempt.duration_ms === "number" && Number.isFinite(attempt.duration_ms)
        ? `${Math.round(attempt.duration_ms)} ms`
        : "";
      const chips = [
        `<span class="companion-debug-chip companion-debug-chip-origin">${escapeHtml((attempt && attempt.origin) || "Unknown origin")}</span>`,
        `<span class="companion-debug-badge ${ok ? "success" : "error"}">${escapeHtml(
          ok ? "Connected" : probeErrorLabel(attempt && attempt.error_kind)
        )}</span>`,
      ];
      if (attempt && attempt.stage) {
        chips.push(
          `<span class="companion-debug-chip companion-debug-chip-meta">${escapeHtml(`Stage ${attempt.stage}`)}</span>`
        );
      }
      if (attempt && typeof attempt.status === "number") {
        chips.push(
          `<span class="companion-debug-chip companion-debug-chip-meta">${escapeHtml(`HTTP ${attempt.status}`)}</span>`
        );
      }
      if (durationMs) {
        chips.push(`<span class="companion-debug-chip companion-debug-chip-meta">${escapeHtml(durationMs)}</span>`);
      }
      const detailText = !ok
        ? truncateInlineText((attempt && (attempt.error || attempt.detail)) || probeErrorHint(attempt), 140)
        : "";
      return `
        <div class="companion-debug-origin-item">
          <div class="companion-debug-chip-row">
            ${chips.join("")}
          </div>
          ${detailText ? `<div class="companion-debug-detail">${escapeHtml(detailText)}</div>` : ""}
        </div>
      `;
    })
    .join("");

  const summaryChips = [
    `<span class="companion-debug-chip companion-debug-chip-summary">${escapeHtml(`${orderedAttempts.length} origin(s)`)}</span>`,
    failedCount
      ? `<span class="companion-debug-chip companion-debug-chip-summary error">${escapeHtml(`${failedCount} failed`)}</span>`
      : "",
    successCount
      ? `<span class="companion-debug-chip companion-debug-chip-summary success">${escapeHtml(`${successCount} reachable`)}</span>`
      : "",
  ]
    .filter(Boolean)
    .join("");

  elements.companionDebugBody.innerHTML = `
    ${noteChips.length ? `<div class="companion-debug-chip-row companion-debug-chip-row-notes">${noteChips.join("")}</div>` : ""}
    <article class="companion-debug-attempt companion-debug-attempt-group">
      <div class="companion-debug-row">
        <div class="companion-debug-origin">Loopback probe summary</div>
        <span class="companion-debug-badge ${successCount > 0 ? "success" : "error"}">${escapeHtml(
          successCount > 0 ? "Partially reachable" : "Probe failed"
        )}</span>
      </div>
      <div class="companion-debug-chip-row companion-debug-chip-row-summary">${summaryChips}</div>
      <div class="companion-debug-origin-list">${attemptMarkup}</div>
    </article>
  `;
}

function setJobBadge(label, tone = "idle") {
  elements.jobBadge.textContent = label;
  elements.jobBadge.dataset.tone = tone;
}

function setStopButtonEnabled(enabled) {
  if (elements.stopButton) {
    elements.stopButton.disabled = !enabled;
  }
}

function setDrawerOpen(open) {
  state.drawerOpen = Boolean(open);
  if (elements.outputDrawer) {
    elements.outputDrawer.classList.toggle("open", state.drawerOpen);
    elements.outputDrawer.setAttribute("aria-hidden", String(!state.drawerOpen));
  }
  if (elements.drawerBackdrop) {
    elements.drawerBackdrop.classList.toggle("open", state.drawerOpen);
    elements.drawerBackdrop.hidden = !state.drawerOpen;
  }
  if (elements.drawerToggle) {
    elements.drawerToggle.textContent = state.drawerOpen ? "Hide results" : "Results";
    elements.drawerToggle.setAttribute("aria-expanded", String(state.drawerOpen));
  }
}

function currentYear() {
  return new Date().getFullYear();
}

function normalizeYearInput(value) {
  const digits = String(value || "").replace(/[^\d]/g, "").slice(0, 4);
  if (!digits) {
    return "";
  }
  const numeric = Number(digits);
  if (!Number.isFinite(numeric)) {
    return "";
  }
  return String(Math.min(2100, Math.max(1900, numeric)));
}

function selectedDatabases() {
  return Object.entries(state.searchFilters.databases)
    .filter(([, enabled]) => Boolean(enabled))
    .map(([key]) => key);
}

function yearRangeLabel() {
  const minYear = normalizeYearInput(state.searchFilters.publishYearMin);
  const maxYear = normalizeYearInput(state.searchFilters.publishYearMax);
  if (!minYear && !maxYear) {
    return "";
  }
  if (minYear && maxYear) {
    return `${minYear}-${maxYear}`;
  }
  if (minYear) {
    return `${minYear}+`;
  }
  return `<=${maxYear}`;
}

function hasActiveYearFilter() {
  return (
    state.searchFilters.publishYearPreset !== "any" ||
    Boolean(normalizeYearInput(state.searchFilters.publishYearMin)) ||
    normalizeYearInput(state.searchFilters.publishYearMax) !== String(currentYear())
  );
}

function activeFilterCount() {
  let count = 0;
  if (state.searchFilters.deepReview) {
    count += 1;
  }
  if (selectedDatabases().length !== Object.keys(DEFAULT_DATABASE_FILTERS).length) {
    count += 1;
  }
  if (hasActiveYearFilter()) {
    count += 1;
  }
  return count;
}

function setFiltersOpen(open) {
  state.filtersOpen = Boolean(open);
  if (elements.filtersSheet) {
    elements.filtersSheet.classList.toggle("open", state.filtersOpen);
    elements.filtersSheet.setAttribute("aria-hidden", String(!state.filtersOpen));
  }
  if (elements.filtersBackdrop) {
    elements.filtersBackdrop.classList.toggle("open", state.filtersOpen);
    elements.filtersBackdrop.hidden = !state.filtersOpen;
  }
  if (elements.filterButton) {
    elements.filterButton.classList.toggle("active", state.filtersOpen || activeFilterCount() > 0);
    elements.filterButton.setAttribute("aria-expanded", String(state.filtersOpen));
  }
}

function renderFilterButtonAndSummary() {
  const selected = selectedDatabases();
  const parts = [];
  if (state.searchFilters.deepReview) {
    parts.push("Deep review");
  }
  if (selected.length !== Object.keys(DEFAULT_DATABASE_FILTERS).length) {
    parts.push(`Sources: ${selected.map((item) => DATABASE_LABELS[item] || item).join(", ")}`);
  }
  const yearLabel = yearRangeLabel();
  if (hasActiveYearFilter() && yearLabel) {
    parts.push(`Years: ${yearLabel}`);
  }

  const count = activeFilterCount();
  if (elements.filterButton) {
    elements.filterButton.textContent = count > 0 ? `Filters (${count})` : "Filters";
    elements.filterButton.classList.toggle("active", state.filtersOpen || count > 0);
  }
  if (elements.filterSummary) {
    if (!parts.length) {
      elements.filterSummary.hidden = true;
      elements.filterSummary.innerHTML = "";
    } else {
      elements.filterSummary.hidden = false;
      elements.filterSummary.innerHTML = parts.map((part) => `<span class="filter-pill">${escapeHtml(part)}</span>`).join("");
    }
  }
  if (elements.filterDbCount) {
    elements.filterDbCount.textContent = `${selected.length}/${Object.keys(DEFAULT_DATABASE_FILTERS).length}`;
  }
}

function renderFilterControls() {
  if (elements.filterDeepReview) {
    elements.filterDeepReview.checked = Boolean(state.searchFilters.deepReview);
  }
  if (elements.filterDbScopus) {
    elements.filterDbScopus.checked = Boolean(state.searchFilters.databases.scopus);
  }
  if (elements.filterDbCore) {
    elements.filterDbCore.checked = Boolean(state.searchFilters.databases.core);
  }
  if (elements.filterDbSemanticScholar) {
    elements.filterDbSemanticScholar.checked = Boolean(state.searchFilters.databases.semantic_scholar);
  }
  if (elements.filterDbOpenalex) {
    elements.filterDbOpenalex.checked = Boolean(state.searchFilters.databases.openalex);
  }
  if (elements.filterDbArxiv) {
    elements.filterDbArxiv.checked = Boolean(state.searchFilters.databases.arxiv);
  }
  if (elements.filterYearMin) {
    elements.filterYearMin.value = normalizeYearInput(state.searchFilters.publishYearMin);
  }
  if (elements.filterYearMax) {
    elements.filterYearMax.value = normalizeYearInput(state.searchFilters.publishYearMax) || String(currentYear());
  }
  document.querySelectorAll("[data-year-preset]").forEach((button) => {
    button.classList.toggle("active", button.getAttribute("data-year-preset") === state.searchFilters.publishYearPreset);
  });
  renderFilterButtonAndSummary();
}

function resetSearchFilters() {
  state.searchFilters = {
    deepReview: false,
    databases: { ...DEFAULT_DATABASE_FILTERS },
    publishYearPreset: "any",
    publishYearMin: "",
    publishYearMax: String(currentYear()),
  };
  renderFilterControls();
}

function applyYearPreset(preset) {
  const year = currentYear();
  state.searchFilters.publishYearPreset = preset;
  if (preset === "last2") {
    state.searchFilters.publishYearMin = String(year - 1);
    state.searchFilters.publishYearMax = String(year);
  } else if (preset === "last3") {
    state.searchFilters.publishYearMin = String(year - 2);
    state.searchFilters.publishYearMax = String(year);
  } else if (preset === "last5") {
    state.searchFilters.publishYearMin = String(year - 4);
    state.searchFilters.publishYearMax = String(year);
  } else {
    state.searchFilters.publishYearMin = "";
    state.searchFilters.publishYearMax = String(year);
  }
  renderFilterControls();
}

function syncFiltersFromInputs() {
  state.searchFilters.deepReview = Boolean(elements.filterDeepReview && elements.filterDeepReview.checked);
  state.searchFilters.databases = {
    scopus: Boolean(elements.filterDbScopus && elements.filterDbScopus.checked),
    core: Boolean(elements.filterDbCore && elements.filterDbCore.checked),
    semantic_scholar: Boolean(elements.filterDbSemanticScholar && elements.filterDbSemanticScholar.checked),
    openalex: Boolean(elements.filterDbOpenalex && elements.filterDbOpenalex.checked),
    arxiv: Boolean(elements.filterDbArxiv && elements.filterDbArxiv.checked),
  };
  if (!selectedDatabases().length) {
    state.searchFilters.databases.openalex = true;
    if (elements.filterDbOpenalex) {
      elements.filterDbOpenalex.checked = true;
    }
  }
  state.searchFilters.publishYearMin = normalizeYearInput(elements.filterYearMin && elements.filterYearMin.value);
  state.searchFilters.publishYearMax = normalizeYearInput(elements.filterYearMax && elements.filterYearMax.value) || String(currentYear());
  if (
    state.searchFilters.publishYearPreset !== "any" &&
    !["last2", "last3", "last5"].includes(state.searchFilters.publishYearPreset)
  ) {
    state.searchFilters.publishYearPreset = "custom";
  }
  renderFilterControls();
}

function buildSearchFilterInstruction() {
  const selected = selectedDatabases();
  const lines = [
    "Workflow search filters:",
    "- Respect these UI filters unless the user's prompt explicitly overrides them.",
  ];
  lines.push(
    state.searchFilters.deepReview
      ? "- Deep review: ON. Prioritize fuller reading and full-text retrieval when possible."
      : "- Deep review: OFF."
  );
  lines.push(`- Allowed literature sources: ${selected.map((item) => DATABASE_LABELS[item] || item).join(", ")}.`);
  const minYear = normalizeYearInput(state.searchFilters.publishYearMin);
  const maxYear = normalizeYearInput(state.searchFilters.publishYearMax);
  if (hasActiveYearFilter()) {
    lines.push(`- Prefer sources published between ${minYear || "any"} and ${maxYear || "any"}.`);
  }
  return lines.join("\n");
}

function serializedSearchFilters() {
  return {
    deep_review: Boolean(state.searchFilters.deepReview),
    databases: {
      scopus: Boolean(state.searchFilters.databases.scopus),
      core: Boolean(state.searchFilters.databases.core),
      semantic_scholar: Boolean(state.searchFilters.databases.semantic_scholar),
      openalex: Boolean(state.searchFilters.databases.openalex),
      arxiv: Boolean(state.searchFilters.databases.arxiv),
    },
    publish_year_preset: state.searchFilters.publishYearPreset,
    publish_year_min: normalizeYearInput(state.searchFilters.publishYearMin),
    publish_year_max: normalizeYearInput(state.searchFilters.publishYearMax) || String(currentYear()),
  };
}

function workflowMessagesWithFilters() {
  const visibleMessages = state.messages.slice(-18).map((item) => ({ role: item.role, content: item.content }));
  return [{ role: "system", content: buildSearchFilterInstruction() }, ...visibleMessages];
}

function workflowHistoryWithFilters() {
  const history = buildWorkflowChatHistory();
  return [{ role: "system", content: buildSearchFilterInstruction() }, ...history];
}

function pushChatMessage(role, content) {
  const text = String(content || "").trim();
  if (!text) {
    return;
  }
  state.messages.push({ role, content: text });
  renderChatThread();
}

function currentAttachmentIds() {
  return Array.from(
    new Set([
      ...state.conversationAttachmentIds,
      ...state.attachments.map((item) => String(item.id || "").trim()).filter(Boolean),
    ])
  );
}

function pushAttachmentMessage(role, attachments) {
  const items = Array.isArray(attachments)
    ? attachments
        .map((item) => ({
          id: String(item.id || "").trim(),
          filename: String(item.filename || "Attachment").trim(),
          file_type: String(item.file_type || "").trim(),
        }))
        .filter((item) => item.filename)
    : [];
  if (!items.length) {
    return;
  }
  const summary = items.map((item) => item.filename).join(", ");
  state.messages.push({
    role,
    kind: "attachments",
    content: `Attached file${items.length === 1 ? "" : "s"}: ${summary}`,
    attachments: items,
  });
  renderChatThread();
}

function setChatActivity(mode = "") {
  state.chatActivity = String(mode || "").trim();
  renderChatThread();
}

function nextFrame() {
  return new Promise((resolve) => window.requestAnimationFrame(resolve));
}

function attachmentTypeFromFile(file) {
  const name = String(file && file.name ? file.name : "").toLowerCase();
  const mime = String(file && file.type ? file.type : "").toLowerCase();
  if (mime.startsWith("image/")) {
    return "image";
  }
  if (name.endsWith(".pdf")) {
    return "pdf";
  }
  if (name.endsWith(".docx") || name.endsWith(".doc")) {
    return "word";
  }
  if (name.endsWith(".xlsx") || name.endsWith(".xls") || name.endsWith(".xlsm") || name.endsWith(".csv") || name.endsWith(".tsv")) {
    return "spreadsheet";
  }
  return "document";
}

function attachmentIcon(item) {
  const type = String(item.file_type || "").toLowerCase();
  if (type === "image") {
    return "IMG";
  }
  if (type === "pdf") {
    return "PDF";
  }
  if (type === "word") {
    return "DOC";
  }
  if (type === "spreadsheet") {
    return "XLS";
  }
  return "FILE";
}

function compactAttachmentName(filename, maxVisibleLength = 15) {
  const raw = String(filename || "").trim();
  if (!raw) {
    return "Attachment";
  }
  const lastDot = raw.lastIndexOf(".");
  if (lastDot <= 0 || lastDot === raw.length - 1) {
    return raw.length <= maxVisibleLength ? raw : `${raw.slice(0, Math.max(4, maxVisibleLength - 3))}...`;
  }
  const base = raw.slice(0, lastDot);
  const ext = raw.slice(lastDot);
  if (raw.length <= maxVisibleLength) {
    return raw;
  }
  const keep = Math.max(4, maxVisibleLength - ext.length - 3);
  return `${base.slice(0, keep)}...${ext}`;
}

function updateAttachment(localId, patch) {
  let changed = false;
  state.attachments = state.attachments.map((item) => {
    if (item.local_id !== localId) {
      return item;
    }
    changed = true;
    return { ...item, ...patch };
  });
  if (changed) {
    renderAttachments();
  }
  return changed;
}

function commitComposerAttachmentsToChat() {
  const readyAttachments = state.attachments.filter((item) => item.status === "ready" && String(item.id || "").trim());
  if (!readyAttachments.length) {
    return;
  }
  state.conversationAttachmentIds = Array.from(
    new Set([...state.conversationAttachmentIds, ...readyAttachments.map((item) => String(item.id || "").trim()).filter(Boolean)])
  );
  pushAttachmentMessage("user", readyAttachments);
  state.attachments = state.attachments.filter((item) => !(item.status === "ready" && String(item.id || "").trim()));
  renderAttachments();
}

function renderChatMessage(message) {
  if (message && message.kind === "attachments" && Array.isArray(message.attachments) && message.attachments.length) {
    const attachmentMarkup = message.attachments
      .map(
        (item) => `
          <span class="chat-attachment-chip">
            <span class="chat-attachment-chip-icon">${escapeHtml(attachmentIcon(item))}</span>
            <span class="chat-attachment-chip-label" title="${escapeHtml(item.filename || "Attachment")}">${escapeHtml(
              compactAttachmentName(item.filename || "Attachment")
            )}</span>
          </span>
        `
      )
      .join("");
    return `
      <div class="chat-message ${escapeHtml(message.role)}">
        <div class="chat-attachment-list">${attachmentMarkup}</div>
      </div>
    `;
  }
  return `
    <div class="chat-message ${escapeHtml(message.role)}">
      <div class="chat-bubble">${escapeHtml(message.content)}</div>
    </div>
  `;
}

function renderChatThread() {
  const chatKey = JSON.stringify({
    messages: state.messages,
    activity: state.chatActivity,
  });
  if (state.lastRenderKey.chat === chatKey) {
    return;
  }
  const bubbles = state.messages.map((message) => renderChatMessage(message)).join("");
  const chatActivity = state.chatActivity;
  const activityLabel =
    chatActivity === "starting_workflow" ? "Starting workflow" : chatActivity === "thinking" ? "Thinking" : "";
  const thinkingBubble = activityLabel
    ? `
        <div class="chat-message assistant thinking ${escapeHtml(chatActivity)}">
          <div class="chat-bubble chat-bubble-thinking">
            <span class="thinking-label">${escapeHtml(activityLabel)}</span>
            <span class="thinking-dots" aria-hidden="true"><span></span><span></span><span></span></span>
          </div>
        </div>
      `
    : "";
  elements.chatThread.innerHTML = bubbles + thinkingBubble;
  elements.chatThread.scrollTop = elements.chatThread.scrollHeight;
  state.lastRenderKey.chat = chatKey;
}

function removeAttachment(id) {
  state.attachments = state.attachments.filter((item) => item.id !== id && item.local_id !== id);
  renderAttachments();
}

function renderAttachments() {
  const attachmentsKey = JSON.stringify(state.attachments);
  if (state.lastRenderKey.attachments === attachmentsKey) {
    return;
  }
  if (!state.attachments.length) {
    elements.attachmentList.className = "attachment-list empty-state";
    elements.attachmentList.textContent = "No files attached.";
    state.lastRenderKey.attachments = attachmentsKey;
    return;
  }
  elements.attachmentList.className = "attachment-list";
  elements.attachmentList.innerHTML = state.attachments
    .map(
      (item) => `
        <div class="attachment-chip">
          <span class="attachment-chip-icon ${item.status === "uploading" ? "uploading" : item.status === "error" ? "error" : ""}">${escapeHtml(attachmentIcon(item))}</span>
          <div class="attachment-chip-text">
            <span class="attachment-chip-title">${escapeHtml(item.filename || "Attachment")}</span>
            <span class="attachment-chip-meta">${escapeHtml(
              item.status === "uploading"
                ? "Uploading..."
                : item.status === "error"
                  ? "Upload failed"
                  : item.file_type || "file"
            )}</span>
          </div>
          <button type="button" class="attachment-chip-remove" data-remove-attachment="${escapeHtml(item.id || item.local_id || "")}">x</button>
        </div>
      `
    )
    .join("");
  elements.attachmentList.querySelectorAll("[data-remove-attachment]").forEach((button) => {
    button.addEventListener("click", () => removeAttachment(button.getAttribute("data-remove-attachment") || ""));
  });
  state.lastRenderKey.attachments = attachmentsKey;
}

function buildWorkflowChatHistory() {
  return state.messages
    .filter((item) => item.role === "user" && String(item.content || "").trim())
    .slice(-12)
    .map((item) => ({ role: item.role, content: item.content }));
}

function renderStatusCards(statuses = {}) {
  elements.statusCards.innerHTML = NODE_ORDER.map((node) => {
    const raw = statuses[node] || "Pending";
    const status = raw.toLowerCase();
    const normalized = status === "done" ? "done" : status === "error" ? "error" : status === "processing" ? "processing" : "pending";
    return `
      <div class="status-card ${normalized}">
        <h3>${escapeHtml(node)}</h3>
        <span class="status-indicator ${normalized}" aria-label="${escapeHtml(raw)}"></span>
      </div>
    `;
  }).join("");
}

function renderList(container, items, itemRenderer, emptyText) {
  if (!items || items.length === 0) {
    container.className = "stack-list empty-state";
    container.innerHTML = escapeHtml(emptyText);
    return;
  }
  container.className = "stack-list";
  container.innerHTML = items.map(itemRenderer).join("");
}

function noteTerminalJobStatus(snapshot) {
  const status = String(snapshot.status || "");
  if (!["completed", "error", "cancelled"].includes(status)) {
    return;
  }
  const signature = `${snapshot.id || state.jobId}|${status}|${snapshot.actual_word_count || 0}`;
  if (state.jobNotificationStatus === signature) {
    return;
  }
  state.jobNotificationStatus = signature;
  if (status === "completed") {
    pushChatMessage("assistant", `Workflow completed. The final manuscript is ready with about ${snapshot.actual_word_count || 0} words. Open Results to inspect the outline, review, draft, and final manuscript.`);
    setDrawerOpen(true);
  } else if (status === "cancelled") {
    pushChatMessage("assistant", "Workflow stopped. You can refine your prompt or attachments and send another request.");
  } else if (status === "error") {
    pushChatMessage("assistant", `Workflow failed. ${snapshot.error || "Check the execution log for details."}`);
    setDrawerOpen(true);
  }
}

function plannerHasStarted(snapshot) {
  const plannerStatus = String(snapshot?.node_statuses?.Planner || "").trim().toLowerCase();
  if (plannerStatus && plannerStatus !== "pending") {
    return true;
  }
  const logs = Array.isArray(snapshot?.logs) ? snapshot.logs : [];
  return logs.some((line) => String(line || "").toLowerCase().includes("planner started:"));
}

function renderSnapshot(snapshot) {
  if (state.chatActivity === "starting_workflow" && plannerHasStarted(snapshot)) {
    setChatActivity("");
  }
  state.lastSnapshot = snapshot;

  const nodeStatusesKey = JSON.stringify(snapshot.node_statuses || {});
  if (state.lastRenderKey.nodeStatuses !== nodeStatusesKey) {
    renderStatusCards(snapshot.node_statuses);
    state.lastRenderKey.nodeStatuses = nodeStatusesKey;
  }

  const logsText =
    snapshot.logs && snapshot.logs.length
      ? snapshot.logs.map((line) => stripLogTimestamp(line)).join("\n")
      : "Waiting for a run...";
  if (state.lastRenderKey.logs !== logsText) {
    const isNearBottom =
      elements.executionLog.scrollHeight - elements.executionLog.scrollTop - elements.executionLog.clientHeight < 48;
    elements.executionLog.textContent = logsText;
    if (isNearBottom || state.lastRenderKey.logs === "") {
      elements.executionLog.scrollTop = elements.executionLog.scrollHeight;
    }
    state.lastRenderKey.logs = logsText;
  }

  const managerText = snapshot.manager_output || "Manager routing will appear here.";
  if (state.lastRenderKey.manager !== managerText) {
    elements.manager.textContent = managerText;
    state.lastRenderKey.manager = managerText;
  }

  const outlineText = snapshot.outline || "Outline will appear here.";
  if (state.lastRenderKey.outline !== outlineText) {
    elements.outline.textContent = outlineText;
    state.lastRenderKey.outline = outlineText;
  }

  const draftText = snapshot.draft || "Draft will stream here.";
  if (state.lastRenderKey.draft !== draftText) {
    elements.draft.textContent = draftText;
    state.lastRenderKey.draft = draftText;
  }

  const reviewText = snapshot.review_feedback || "Reviewer feedback will appear here.";
  if (state.lastRenderKey.review !== reviewText) {
    elements.review.textContent = reviewText;
    state.lastRenderKey.review = reviewText;
  }

  const finalText = snapshot.final_markdown || "Final manuscript will appear here.";
  if (state.lastRenderKey.final !== finalText) {
    elements.final.textContent = finalText;
    state.lastRenderKey.final = finalText;
  }

  const papersKey = JSON.stringify(snapshot.papers || []);
  if (state.lastRenderKey.papers !== papersKey) {
    renderList(
      elements.papers,
      snapshot.papers,
      (paper) => {
        const doi = String(paper.doi || "").trim();
        const doiUrl = doi ? `https://doi.org/${doi}` : "";
        return `
          <article class="data-card">
            <h4>${escapeHtml(paper.title || "Untitled")}</h4>
            <p class="meta-row">${escapeHtml(paper.authors || "Unknown author")} (${escapeHtml(paper.year || "")})</p>
            <p class="meta-row">
              Source: ${escapeHtml(paper.source_db || "Unknown")}
              ${doiUrl ? `| <a href="${escapeHtml(doiUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(doiUrl)}</a>` : ""}
            </p>
          </article>
        `;
      },
      "No papers yet."
    );
    state.lastRenderKey.papers = papersKey;
  }

  const actualWords = snapshot.actual_word_count || 0;
  const badgeKey = `${snapshot.status}|${actualWords}|${Boolean(snapshot.final_markdown)}`;
  if (snapshot.status === "completed") {
    setJobBadge(`Completed - ${actualWords} words`, "done");
    elements.downloadMarkdown.disabled = !snapshot.final_markdown;
    elements.downloadDocx.disabled = !snapshot.final_markdown;
    setStopButtonEnabled(false);
    elements.runButton.disabled = false;
  } else if (snapshot.status === "error") {
    setJobBadge("Failed", "error");
    elements.downloadMarkdown.disabled = !snapshot.final_markdown;
    elements.downloadDocx.disabled = !snapshot.final_markdown;
    setStopButtonEnabled(false);
    elements.runButton.disabled = false;
  } else if (snapshot.status === "cancelled") {
    setJobBadge("Stopped", "error");
    elements.downloadMarkdown.disabled = !snapshot.final_markdown;
    elements.downloadDocx.disabled = !snapshot.final_markdown;
    setStopButtonEnabled(false);
    elements.runButton.disabled = false;
  } else if (snapshot.status === "cancelling") {
    setJobBadge("Stopping", "processing");
    elements.downloadMarkdown.disabled = true;
    elements.downloadDocx.disabled = true;
    setStopButtonEnabled(false);
  } else if (snapshot.status === "running") {
    setJobBadge("Running", "processing");
    elements.downloadMarkdown.disabled = true;
    elements.downloadDocx.disabled = true;
    setStopButtonEnabled(true);
  } else {
    setJobBadge("Queued", "idle");
    elements.downloadMarkdown.disabled = true;
    elements.downloadDocx.disabled = true;
    setStopButtonEnabled(Boolean(state.jobId));
  }
  state.lastRenderKey.badge = badgeKey;
  noteTerminalJobStatus(snapshot);
}

async function fetchJson(url, options = {}) {
  const isFormData = options.body instanceof FormData;
  const headers = isFormData ? { ...(options.headers || {}) } : { "Content-Type": "application/json", ...(options.headers || {}) };
  const response = await fetch(url, {
    headers,
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const body = await response.text();
  if (contentType.includes("text/html") || body.trim().startsWith("<!DOCTYPE") || body.trim().startsWith("<html")) {
    throw new Error("Backend API is not connected. The API returned HTML instead of JSON for /api.");
  }
  if (!response.ok) {
    let detail = body;
    try {
      const payload = JSON.parse(body);
      if (payload && typeof payload.detail === "string") detail = payload.detail;
    } catch (_error) {
      // Keep the raw response body.
    }
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return JSON.parse(body);
}

function formatCompactNumber(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "";
  }
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(value) >= 1000 ? "compact" : "standard",
    maximumFractionDigits: value >= 100 ? 0 : 1,
  }).format(value);
}

function formatQuotaReset(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) {
    return text;
  }
  return parsed.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function quotaFillTone(fraction) {
  if (typeof fraction !== "number" || !Number.isFinite(fraction)) {
    return "mid";
  }
  if (fraction <= 0.2) {
    return "low";
  }
  if (fraction <= 0.55) {
    return "mid";
  }
  return "high";
}

function clearQuotaRender(message) {
  state.quotaAvailable = false;
  state.connectedAccountEmail = "";
  state.quotaRefreshRecoveringUntil = 0;
  state.lastQuotaKey = String(message || "");
  renderLlmSourceBadge();
  if (elements.quotaAccount) {
    elements.quotaAccount.textContent = message || "Start ResearchCompanion.exe to load account quota.";
    elements.quotaAccount.hidden = !elements.quotaAccount.textContent;
    elements.quotaAccount.removeAttribute("title");
  }
  if (elements.quotaTier) {
    elements.quotaTier.hidden = true;
    elements.quotaTier.textContent = "";
    elements.quotaTier.className = "quota-pill";
  }
  if (elements.quotaCredits) {
    elements.quotaCredits.hidden = true;
    elements.quotaCredits.textContent = "";
    elements.quotaCredits.className = "quota-pill";
  }
  if (elements.quotaBars) {
    elements.quotaBars.className = "quota-bars empty-state";
    elements.quotaBars.textContent = "Quota bars will appear here.";
  }
}

function renderQuota(payload) {
  if (!payload || !payload.available) {
    clearQuotaRender((payload && payload.reason) || "Quota is unavailable right now.");
    return;
  }

  state.llmAuthMode = "cli_proxy_oauth";
  state.quotaAvailable = true;
  state.quotaRefreshRecoveringUntil = 0;
  state.lastQuotaKey = JSON.stringify(payload);

  const email = String(payload.email || "").trim();
  const projectId = String(payload.project_id || "").trim();
  state.connectedAccountEmail = email;
  renderLlmSourceBadge();
  const tierLabel = String(payload.tier_label || "").trim();
  const creditBalance = typeof payload.credit_balance === "number" ? payload.credit_balance : null;
  const buckets = Array.isArray(payload.buckets) ? payload.buckets : [];

  if (elements.quotaAccount) {
    elements.quotaAccount.hidden = true;
    elements.quotaAccount.textContent = "";
    elements.quotaAccount.removeAttribute("title");
  }

  if (elements.quotaTier) {
    if (tierLabel) {
      elements.quotaTier.hidden = false;
      elements.quotaTier.textContent = tierLabel;
      elements.quotaTier.className = `quota-pill ${payload.premium_tier ? "premium" : ""}`.trim();
    } else {
      elements.quotaTier.hidden = true;
      elements.quotaTier.textContent = "";
      elements.quotaTier.className = "quota-pill";
    }
  }

  if (elements.quotaCredits) {
    if (creditBalance !== null && Number.isFinite(creditBalance)) {
      elements.quotaCredits.hidden = false;
      elements.quotaCredits.textContent = `Credits: ${formatCompactNumber(creditBalance)}`;
      elements.quotaCredits.className = "quota-pill";
    } else if (tierLabel) {
      elements.quotaCredits.hidden = false;
      elements.quotaCredits.textContent = tierLabel.toLowerCase() === "free" ? "Credits: none" : "Credits: n/a";
      elements.quotaCredits.className = "quota-pill";
    } else {
      elements.quotaCredits.hidden = true;
      elements.quotaCredits.textContent = "";
      elements.quotaCredits.className = "quota-pill";
    }
  }

  if (!elements.quotaBars) {
    return;
  }

  if (!buckets.length) {
    elements.quotaBars.className = "quota-bars empty-state";
    elements.quotaBars.textContent = "Quota details are not available for this account yet.";
    return;
  }

  elements.quotaBars.className = "quota-bars";
  elements.quotaBars.innerHTML = buckets
    .map((bucket) => {
      const label = String(bucket.label || bucket.id || "Quota bucket");
      const fraction = typeof bucket.remaining_fraction === "number" ? bucket.remaining_fraction : null;
      const percent = fraction === null ? "N/A" : `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
      const amount = typeof bucket.remaining_amount === "number" ? formatCompactNumber(bucket.remaining_amount) : "";
      const tokenType = String(bucket.token_type || "").trim();
      const resetText = formatQuotaReset(bucket.reset_time);
      const fillWidth = fraction === null ? 0 : Math.max(4, Math.min(100, Math.round(fraction * 100)));
      const tone = quotaFillTone(fraction === null ? undefined : fraction);
      return `
        <article class="quota-bucket-card">
          <div class="quota-bucket-head">
            <strong>${escapeHtml(label)}</strong>
            <span class="quota-bucket-percent">${escapeHtml(percent)}</span>
          </div>
          <div class="quota-track">
            <span class="quota-fill ${tone}" style="width:${fillWidth}%"></span>
          </div>
          <div class="quota-bucket-foot">
            <span class="quota-bucket-meta">${escapeHtml(amount ? `${amount} ${tokenType || "tokens"} left` : tokenType || "")}</span>
            <span class="quota-bucket-meta">${escapeHtml(resetText ? `Reset: ${resetText}` : "")}</span>
          </div>
        </article>
      `;
    })
    .join("");
}

async function loadQuota() {
  if (!state.usingLocalCompanion) {
    state.localCompanionConnectedAt = 0;
    clearQuotaRender("Start ResearchCompanion.exe to load account quota.");
    return;
  }
  try {
    const payload = await fetchJson(apiUrl("/quota"));
    if ((!payload || !payload.available) && quotaWarmupActive() && isTransientQuotaWarmupReason(payload && payload.reason)) {
      renderQuotaPending("Local companion is starting. Waiting for Gemini session...");
      return;
    }
    if ((!payload || !payload.available) && isTransientQuotaWarmupReason(payload && payload.reason)) {
      if (shouldGracefullyRecoverQuotaAuth()) {
        renderQuotaPending("Refreshing Gemini session...");
        return;
      }
      state.llmAuthMode = "cli_proxy_reauth_required";
    }
    renderQuota(payload);
  } catch (error) {
    const message = error && error.message ? error.message : String(error || "");
    if (quotaWarmupActive() && isTransientQuotaWarmupReason(message)) {
      renderQuotaPending("Local companion is starting. Waiting for Gemini session...");
      return;
    }
    if (isTransientQuotaWarmupReason(message)) {
      if (shouldGracefullyRecoverQuotaAuth()) {
        renderQuotaPending("Refreshing Gemini session...");
        return;
      }
      state.llmAuthMode = "cli_proxy_reauth_required";
    }
    clearQuotaRender(error.message || "Quota is unavailable right now.");
  }
}

async function opaqueReachabilityProbe(origin) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 1200);
  try {
    await fetch(`${origin}/api/live?opaque_probe=1`, {
      method: "GET",
      mode: "no-cors",
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
    });
    return true;
  } catch (_error) {
    return false;
  } finally {
    window.clearTimeout(timeout);
  }
}

async function probeLocalOrigin(origin) {
  const startedAt = typeof performance !== "undefined" && performance.now ? performance.now() : Date.now();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 1800);
  try {
    const response = await fetch(`${origin}/api/live`, {
      method: "GET",
      mode: "cors",
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch (_error) {
      return {
        ok: false,
        origin,
        stage: "parse",
        error_kind: "invalid_json",
        error: "Liveness endpoint returned non-JSON content.",
        detail: truncateInlineText(text || "Empty response body."),
        duration_ms: (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now()) - startedAt,
      };
    }
    if (!response.ok) {
      return {
        ok: false,
        origin,
        stage: "http",
        status: response.status,
        error_kind: "http_error",
        error: `Liveness check returned HTTP ${response.status}.`,
        detail: truncateInlineText(text || JSON.stringify(payload)),
        duration_ms: (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now()) - startedAt,
      };
    }
    if (!payload.ok || payload.service !== "research-companion" || payload.local_companion_mode === false) {
      return {
        ok: false,
        origin,
        stage: "payload",
        error_kind: "payload_invalid",
        error: "Liveness JSON did not identify a local Research Companion.",
        detail: truncateInlineText(JSON.stringify(payload)),
        duration_ms: (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now()) - startedAt,
      };
    }
    return {
      ok: true,
      origin,
      payload,
      stage: "live",
      duration_ms: (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now()) - startedAt,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error || "Unknown local probe error");
    let errorKind = "request_error";
    let stage = "request";
    if (error instanceof DOMException && error.name === "AbortError") {
      errorKind = "timeout";
      stage = "timeout";
    } else {
      const opaqueReachable = await opaqueReachabilityProbe(origin);
      errorKind = opaqueReachable ? "cors_or_private_network" : "network_unreachable";
    }
    return {
      ok: false,
      origin,
      stage,
      error_kind: errorKind,
      error: errorMessage,
      duration_ms: (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now()) - startedAt,
    };
  } finally {
    window.clearTimeout(timeout);
  }
}

async function refreshLocalCompanionHealth() {
  if (!state.usingLocalCompanion) return;
  try {
    const payload = await fetchJson(apiUrl("/health"));
    state.llmAuthMode = String((payload && payload.llm_auth_mode) || "local_companion_required");
    renderLlmSourceBadge();
  } catch (_error) {
    state.llmAuthMode = "local_companion_required";
    renderLlmSourceBadge();
  }
}

async function detectLocalCompanion() {
  const attempts = [];
  for (const origin of LOCAL_COMPANION_ORIGINS) {
    const result = await probeLocalOrigin(origin);
    attempts.push(result);
    if (result.ok) {
      const isFreshConnection = !state.usingLocalCompanion || state.localCompanionOrigin !== origin;
      state.apiBase = `${origin}/api`;
      state.localCompanionOrigin = origin;
      state.usingLocalCompanion = true;
      if (isFreshConnection || !state.localCompanionConnectedAt) {
        state.localCompanionConnectedAt = Date.now();
      }
      state.localProbeDebug = {
        status: "connected",
        origin,
        checkedAt: new Date().toISOString(),
        attempts,
        note: "",
      };
      renderLocalProbeDebug();
      renderLlmSourceBadge();
      return true;
    }
  }

  state.apiBase = REMOTE_API_BASE;
  state.localCompanionOrigin = "";
  state.usingLocalCompanion = false;
  state.localCompanionConnectedAt = 0;
  state.llmAuthMode = "local_companion_required";
  state.connectedAccountEmail = "";
  state.quotaAvailable = false;
  state.localProbeDebug = {
    status: "failed",
    origin: "",
    checkedAt: new Date().toISOString(),
    attempts,
    note:
      "The hosted app could not complete a readable liveness probe against the local companion. Check the per-origin result below.",
  };
  renderLocalProbeDebug();
  renderLlmSourceBadge();
  return false;
}

async function syncRuntimeMode(options = {}) {
  const { forceQuota = false } = options;
  const previousApiBase = state.apiBase;
  const previousAuthMode = state.llmAuthMode;
  const previousLocal = state.usingLocalCompanion;

  await detectLocalCompanion();
  if (state.usingLocalCompanion) {
    await refreshLocalCompanionHealth();
    await refreshModels();
  } else {
    await refreshModels();
  }

  const runtimeChanged =
    previousApiBase !== state.apiBase || previousAuthMode !== state.llmAuthMode || previousLocal !== state.usingLocalCompanion;

  if (runtimeChanged || forceQuota || state.usingLocalCompanion) {
    await loadQuota();
  }
}

function startLocalCompanionWatcher() {
  if (state.localProbeTimer) {
    clearInterval(state.localProbeTimer);
  }
  state.localProbeTimer = setInterval(() => {
    syncRuntimeMode({ forceQuota: true }).catch(() => {});
  }, 2500);

  window.addEventListener("focus", () => {
    syncRuntimeMode({ forceQuota: true }).catch(() => {});
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      syncRuntimeMode({ forceQuota: true }).catch(() => {});
    }
  });
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollJob() {
  if (!state.jobId) {
    return;
  }

  try {
    const snapshot = await fetchJson(apiUrl(`/jobs/${state.jobId}`));
    renderSnapshot(snapshot);
    if (snapshot.status === "completed" || snapshot.status === "error" || snapshot.status === "cancelled") {
      stopPolling();
      elements.runButton.disabled = false;
      setStopButtonEnabled(false);
    }
  } catch (error) {
    stopPolling();
    setChatActivity("");
    elements.runButton.disabled = false;
    setStopButtonEnabled(false);
    elements.executionLog.textContent += `\nPolling failed: ${error.message}`;
  }
}

async function stopWorkflow() {
  if (!state.jobId) {
    return;
  }
  setStopButtonEnabled(false);
  try {
    await fetchJson(apiUrl(`/jobs/${state.jobId}/stop`), { method: "POST" });
    elements.executionLog.textContent += "\nStop requested...";
  } catch (error) {
    setStopButtonEnabled(true);
    elements.executionLog.textContent += `\nStop failed: ${error.message}`;
  }
}

async function copyExecutionLog() {
  const text = String(elements.executionLog.textContent || "").trim();
  if (!text) {
    return;
  }
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.focus();
      helper.select();
      document.execCommand("copy");
      document.body.removeChild(helper);
    }
    const original = elements.copyLogButton.dataset.label || elements.copyLogButton.textContent || "Copy log";
    elements.copyLogButton.dataset.label = original;
    elements.copyLogButton.textContent = "Copied";
    window.setTimeout(() => {
      elements.copyLogButton.textContent = original;
    }, 1200);
  } catch (_error) {
    const original = elements.copyLogButton.dataset.label || elements.copyLogButton.textContent || "Copy log";
    elements.copyLogButton.dataset.label = original;
    elements.copyLogButton.textContent = "Copy failed";
    window.setTimeout(() => {
      elements.copyLogButton.textContent = original;
    }, 1500);
  }
}

async function uploadSelectedFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) {
    return;
  }
  const tempItems = files.map((file, index) => ({
    id: "",
    local_id: `local-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`,
    filename: file.name || "Attachment",
    mime_type: file.type || "",
    file_type: attachmentTypeFromFile(file),
    size_bytes: Number(file.size || 0),
    status: "uploading",
  }));
  state.attachments = [...state.attachments, ...tempItems];
  renderAttachments();
  elements.attachmentButton.disabled = true;
  const previousLabel = elements.attachmentButton.textContent;
  elements.attachmentButton.textContent = "...";
  try {
    await syncRuntimeMode({ forceQuota: true });
    const uploadResults = await Promise.allSettled(
      files.map(async (file, index) => {
        const formData = new FormData();
        formData.append("files", file);
        const payload = await fetchJson(apiUrl("/uploads"), {
          method: "POST",
          body: formData,
        });
        const uploaded = Array.isArray(payload.items) ? payload.items[0] : null;
        const localId = tempItems[index].local_id;
        if (!uploaded) {
          updateAttachment(localId, { status: "error" });
          return { ok: false };
        }
        updateAttachment(localId, {
          ...uploaded,
          local_id: localId,
          status: "ready",
        });
        return { ok: true };
      })
    );
    const successCount = uploadResults.filter((result) => result.status === "fulfilled" && result.value && result.value.ok).length;
    const failureCount = uploadResults.length - successCount;
    if (successCount) {
      pushChatMessage("assistant", `Attached ${successCount} file(s). They will be treated as offline context for the workflow.`);
    }
    if (failureCount) {
      pushChatMessage("assistant", `${failureCount} attachment upload(s) failed. Remove them and try again if needed.`);
    }
  } catch (error) {
    tempItems.forEach((item) => updateAttachment(item.local_id, { status: "error" }));
    pushChatMessage("assistant", `Attachment upload failed. ${error.message}`);
  } finally {
    elements.attachmentButton.disabled = false;
    elements.attachmentButton.textContent = previousLabel || "+";
    elements.attachmentInput.value = "";
  }
}

async function startWorkflow() {
  const prompt = String(elements.chatPrompt.value || "").trim();
  if (!prompt && !state.attachments.length) {
    elements.chatPrompt.focus();
    return;
  }
  if (state.attachments.some((item) => item.status === "uploading")) {
    pushChatMessage("assistant", "Wait for attachment uploads to finish before starting the workflow.");
    return;
  }

  if (state.jobId && state.lastSnapshot && ["queued", "running", "cancelling"].includes(String(state.lastSnapshot.status || ""))) {
    pushChatMessage("assistant", "A workflow is already running. Stop it first if you want to start a new one.");
    return;
  }

  if (prompt) {
    pushChatMessage("user", prompt);
  }
  commitComposerAttachmentsToChat();
  elements.chatPrompt.value = "";
  elements.runButton.disabled = true;
  setStopButtonEnabled(false);
  setChatActivity("thinking");
  await nextFrame();

  await syncRuntimeMode({ forceQuota: true });
  if (!state.usingLocalCompanion) {
    setChatActivity("");
    elements.runButton.disabled = false;
    pushChatMessage("assistant", "Start ResearchCompanion.exe first, then send your request again.");
    return;
  }
  if (!state.quotaAvailable) {
    setChatActivity("");
    elements.runButton.disabled = false;
    pushChatMessage(
      "assistant",
      state.llmAuthMode === "cli_proxy_reauth_required"
        ? "Gemini OAuth in ResearchCompanion.exe has expired or become invalid. Reconnect OAuth there, then send your request again."
        : quotaWarmupActive()
          ? "Local companion is still loading Gemini session and quota. Wait a few seconds, then send your request again."
          : "Connect Gemini in ResearchCompanion.exe first, then send your request again."
    );
    return;
  }

  const payload = {
    messages: workflowMessagesWithFilters(),
    language: MANAGER_LANGUAGE_FALLBACK,
    model: state.selectedModel || null,
    attachment_ids: currentAttachmentIds(),
    search_filters: serializedSearchFilters(),
  };

  try {
    const response = await fetchJson(apiUrl("/chat-turn"), {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (response.assistant_reply) {
      pushChatMessage("assistant", response.assistant_reply);
    }
    if (response.job_id) {
      setChatActivity("starting_workflow");
      await nextFrame();
      state.jobId = response.job_id;
      elements.downloadMarkdown.disabled = true;
      elements.executionLog.textContent = "Submitting workflow...";
      renderStatusCards({});
      setJobBadge("Queued", "idle");
      state.jobNotificationStatus = "";
      setStopButtonEnabled(true);
      stopPolling();
      await pollJob();
      state.pollTimer = setInterval(pollJob, 1500);
    } else {
      setChatActivity("");
      elements.runButton.disabled = false;
      setStopButtonEnabled(false);
    }
  } catch (error) {
    const message = String(error && error.message ? error.message : error || "");
    const isChatTurnMissing = message.includes("Not Found") || message.includes("\"detail\":\"Not Found\"");
    if (isChatTurnMissing) {
      pushChatMessage(
        "assistant",
        "The local companion is running an older backend without Gemini chat-turn support. Restart or update ResearchCompanion.exe before launching a workflow."
      );
      setChatActivity("");
      elements.runButton.disabled = false;
      setStopButtonEnabled(false);
      return;
    }
    setChatActivity("");
    elements.runButton.disabled = false;
    setStopButtonEnabled(false);
    pushChatMessage("assistant", `Chat failed. ${message}`);
  }
}

function clearCurrentJob() {
  stopPolling();
  state.jobId = null;
  state.lastSnapshot = null;
  state.chatActivity = "";
  state.conversationAttachmentIds = [];
  state.jobNotificationStatus = "";
  state.lastRenderKey = {
    nodeStatuses: "",
    logs: "",
    manager: "",
    outline: "",
    papers: "",
    review: "",
    draft: "",
    final: "",
    badge: "",
    chat: "",
    attachments: "",
  };
  elements.runButton.disabled = false;
  setStopButtonEnabled(false);
  elements.downloadMarkdown.disabled = true;
  elements.downloadDocx.disabled = true;
  elements.executionLog.textContent = "Waiting for a run...";
  elements.manager.textContent = "Manager routing will appear here.";
  elements.outline.textContent = "Outline will appear here.";
  elements.draft.textContent = "Draft will stream here.";
  elements.final.textContent = "Final manuscript will appear here.";
  elements.review.textContent = "Reviewer feedback will appear here.";
  elements.papers.className = "stack-list empty-state";
  elements.papers.textContent = "No papers yet.";
  renderStatusCards({});
  renderChatThread();
  renderAttachments();
  setJobBadge("Idle", "idle");
}

function downloadMarkdown() {
  const content = state.lastSnapshot && state.lastSnapshot.final_markdown;
  if (!content) {
    return;
  }
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "draft.md";
  link.click();
  URL.revokeObjectURL(url);
}

async function downloadDocx() {
  const markdown = state.lastSnapshot && state.lastSnapshot.final_markdown;
  if (!state.jobId || !markdown) {
    return;
  }
  let response = await fetch(apiUrl(`/jobs/${state.jobId}/docx`));
  if (response.status === 404 && state.usingLocalCompanion) {
    response = await fetch(`${REMOTE_API_BASE}/render/docx`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        markdown,
        filename: `research-workflow-${String(state.jobId).slice(0, 8)}.docx`,
      }),
    });
  }
  if (!response.ok) {
    const message = await response.text();
    pushChatMessage("assistant", `Word export failed. ${message || response.statusText}`);
    return;
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `research-workflow-${String(state.jobId).slice(0, 8)}.docx`;
  link.click();
  URL.revokeObjectURL(url);
}

function initTabs() {
  const buttons = document.querySelectorAll(".tab-button");
  const panels = document.querySelectorAll(".tab-panel");

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.tab;
      buttons.forEach((item) => item.classList.remove("active"));
      panels.forEach((panel) => panel.classList.remove("active"));
      button.classList.add("active");
      document.getElementById(`tab-${target}`).classList.add("active");
    });
  });
}

function initComposer() {
  elements.chatPrompt.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      startWorkflow();
    }
  });

  elements.attachmentButton.addEventListener("click", () => elements.attachmentInput.click());
  elements.attachmentInput.addEventListener("change", () => uploadSelectedFiles(elements.attachmentInput.files));
  elements.filterButton.addEventListener("click", () => {
    renderFilterControls();
    setFiltersOpen(!state.filtersOpen);
  });
}

function initFilters() {
  renderFilterControls();

  elements.filtersClose.addEventListener("click", () => setFiltersOpen(false));
  elements.filtersBackdrop.addEventListener("click", () => setFiltersOpen(false));
  elements.filtersApply.addEventListener("click", () => {
    syncFiltersFromInputs();
    setFiltersOpen(false);
  });
  elements.filtersReset.addEventListener("click", () => resetSearchFilters());

  [elements.filterDeepReview, elements.filterDbScopus, elements.filterDbCore, elements.filterDbSemanticScholar, elements.filterDbOpenalex, elements.filterDbArxiv].forEach((element) => {
    element.addEventListener("change", () => syncFiltersFromInputs());
  });

  [elements.filterYearMin, elements.filterYearMax].forEach((element) => {
    element.addEventListener("input", () => {
      state.searchFilters.publishYearPreset = "custom";
      syncFiltersFromInputs();
    });
  });

  document.querySelectorAll("[data-year-preset]").forEach((button) => {
    button.addEventListener("click", () => applyYearPreset(button.getAttribute("data-year-preset") || "any"));
  });
}

function init() {
  renderStatusCards({});
  renderLlmSourceBadge();
  clearQuotaRender("Start ResearchCompanion.exe to load account quota.");
  renderLocalProbeDebug();
  renderChatThread();
  renderAttachments();
  setDrawerOpen(false);
  initTabs();
  initComposer();
  initFilters();
  renderModelSelect(false);
  if (elements.modelSelect) {
    elements.modelSelect.addEventListener("change", () => {
      state.selectedModel = elements.modelSelect.value || "";
      persistSelectedModel(state.selectedModel);
      renderModelSelect(state.modelsLoaded);
    });
  }
  startLocalCompanionWatcher();

  syncRuntimeMode({ forceQuota: true }).catch(() => {
    clearQuotaRender("Start ResearchCompanion.exe to load account quota.");
  });

  elements.drawerToggle.addEventListener("click", () => setDrawerOpen(!state.drawerOpen));
  elements.drawerClose.addEventListener("click", () => setDrawerOpen(false));
  elements.drawerBackdrop.addEventListener("click", () => setDrawerOpen(false));
  if (elements.retryCompanionDetect) {
    elements.retryCompanionDetect.addEventListener("click", () => {
      syncRuntimeMode({ forceQuota: true }).catch(() => {
        renderLocalProbeDebug();
      });
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (state.filtersOpen) {
        setFiltersOpen(false);
      } else if (state.drawerOpen) {
        setDrawerOpen(false);
      }
    }
  });
  elements.runButton.addEventListener("click", startWorkflow);
  elements.stopButton.addEventListener("click", stopWorkflow);
  elements.copyLogButton.addEventListener("click", copyExecutionLog);
  elements.clearButton.addEventListener("click", clearCurrentJob);
  elements.downloadMarkdown.addEventListener("click", downloadMarkdown);
  elements.downloadDocx.addEventListener("click", downloadDocx);
}

init();
