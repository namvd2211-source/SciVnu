const state = {
  lastSeq: -1,
  lastRunning: false,
};

const elements = {
  statusText: document.getElementById("statusText"),
  detailText: document.getElementById("detailText"),
  statusBadge: document.getElementById("statusBadge"),
  statusDot: document.getElementById("statusDot"),
  runtimeIdentity: document.getElementById("runtimeIdentity"),
  oauthSource: document.getElementById("oauthSource"),
  runtimePhase: document.getElementById("runtimePhase"),
  authPhase: document.getElementById("authPhase"),
  startupStage: document.getElementById("startupStage"),
  eventList: document.getElementById("eventList"),
  overviewTabButton: document.getElementById("overviewTabButton"),
  stateTabButton: document.getElementById("stateTabButton"),
  updateTabButton: document.getElementById("updateTabButton"),
  authTabButton: document.getElementById("authTabButton"),
  logTabButton: document.getElementById("logTabButton"),
  overviewTab: document.getElementById("overviewTab"),
  stateTab: document.getElementById("stateTab"),
  updateTab: document.getElementById("updateTab"),
  authTab: document.getElementById("authTab"),
  logTab: document.getElementById("logTab"),
  logOutput: document.getElementById("logOutput"),
  logWrap: document.getElementById("logWrap"),
  logCounter: document.getElementById("logCounter"),
  startButton: document.getElementById("startButton"),
  stopButton: document.getElementById("stopButton"),
  oauthButton: document.getElementById("oauthButton"),
  openWebButton: document.getElementById("openWebButton"),
  currentVersion: document.getElementById("currentVersion"),
  latestVersion: document.getElementById("latestVersion"),
  releaseChannel: document.getElementById("releaseChannel"),
  updateBadge: document.getElementById("updateBadge"),
  updateMessage: document.getElementById("updateMessage"),
  updateMeta: document.getElementById("updateMeta"),
  checkUpdateButton: document.getElementById("checkUpdateButton"),
  installUpdateButton: document.getElementById("installUpdateButton"),
  releaseNotesButton: document.getElementById("releaseNotesButton"),
  clearButton: document.getElementById("clearButton"),
  copyButton: document.getElementById("copyButton"),
  authDir: document.getElementById("authDir"),
  authSummary: document.getElementById("authSummary"),
  authList: document.getElementById("authList"),
  refreshAuthButton: document.getElementById("refreshAuthButton"),
  openAuthFolderButton: document.getElementById("openAuthFolderButton"),
};

function switchTab(target) {
  const tabs = [
    { button: elements.overviewTabButton, panel: elements.overviewTab, key: "overview" },
    { button: elements.stateTabButton, panel: elements.stateTab, key: "state" },
    { button: elements.updateTabButton, panel: elements.updateTab, key: "update" },
    { button: elements.authTabButton, panel: elements.authTab, key: "auth" },
    { button: elements.logTabButton, panel: elements.logTab, key: "log" },
  ];
  tabs.forEach((item) => {
    const active = item.key === target;
    item.button.classList.toggle("active", active);
    item.panel.classList.toggle("active", active);
  });
}

function nearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 42;
}

function setBusy(button, busy) {
  button.disabled = busy;
}

function setToast(message) {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 2200);
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return map[char] || char;
  });
}

function renderAuthFiles(snapshot) {
  const authDir = snapshot.auth_dir || "";
  const files = Array.isArray(snapshot.auth_files) ? snapshot.auth_files : [];
  const authSummary = snapshot.auth_summary || {};
  elements.authDir.textContent = authDir || "No auth directory configured.";
  elements.authSummary.innerHTML = `
    <span class="auth-summary-chip">Total ${Number(authSummary.total || 0)}</span>
    <span class="auth-summary-chip">File ${Number(authSummary.file_backed || 0)}</span>
    <span class="auth-summary-chip">Runtime ${Number(authSummary.runtime_only || 0)}</span>
    <span class="auth-summary-chip">Disabled ${Number(authSummary.disabled || 0)}</span>
    <span class="auth-summary-chip">Expired ${Number(authSummary.expired || 0)}</span>
  `;

  if (!files.length) {
    elements.authList.innerHTML = `
      <div class="auth-empty">
        No Gemini OAuth file has been saved yet. Run <strong>Google OAuth</strong> and complete login in the browser.
      </div>
    `;
    return;
  }

  elements.authList.innerHTML = files
    .map((file) => {
      const runtimeOnly = Boolean(file.runtime_only);
      const disabled = Boolean(file.disabled);
      const statusClass = file.expired ? "expired" : disabled ? "disabled" : runtimeOnly ? "runtime" : "valid";
      const statusText = file.expired ? "Expired" : disabled ? "Disabled" : runtimeOnly ? "Runtime" : "Ready";
      const refreshTokenText = file.has_refresh_token ? "Refresh token saved" : "No refresh token";
      const updatedText = runtimeOnly ? `Loaded in ${file.source || "runtime"}` : file.modified || file.size_kb || "";
      const baseFilename = file.base_filename || file.filename || "";
      const toggleLabel = disabled ? "Enable" : "Disable";
      const actionButtons = runtimeOnly
        ? `<span class="auth-runtime-note">Runtime-only entry</span>`
        : `
            <div class="auth-item-actions">
              <button class="ghost-button tiny-button auth-toggle-button" data-filename="${escapeHtml(baseFilename)}" data-disabled="${disabled ? "0" : "1"}">${toggleLabel}</button>
              <button class="ghost-button tiny-button auth-delete-button" data-filename="${escapeHtml(baseFilename)}">Delete</button>
            </div>
          `;
      return `
        <article class="auth-item">
          <div class="auth-item-top">
            <div class="auth-item-copy">
              <h3 title="${escapeHtml(file.email)}">${escapeHtml(file.email || "unknown")}</h3>
              <p title="${escapeHtml(file.filename)}">${escapeHtml(file.filename || "")}</p>
            </div>
            <span class="auth-status ${statusClass}">${statusText}</span>
          </div>
          <dl class="auth-meta">
            <div>
              <dt>Project</dt>
              <dd>${escapeHtml(file.project_id || "unknown")}</dd>
            </div>
            <div>
              <dt>Expiry</dt>
              <dd>${escapeHtml(file.expiry || "unknown")}</dd>
            </div>
            <div>
              <dt>Token</dt>
              <dd>${escapeHtml(refreshTokenText)}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>${escapeHtml(updatedText)}</dd>
            </div>
          </dl>
          <div class="auth-item-bottom">
            <p class="auth-path" title="${escapeHtml(file.path || "")}">${escapeHtml(file.path || "")}</p>
            ${actionButtons}
          </div>
        </article>
      `;
    })
    .join("");

  elements.authList.querySelectorAll(".auth-delete-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const filename = button.getAttribute("data-filename") || "";
      if (!filename) return;
      await callApi("delete_auth_file", button, filename);
      await refresh();
    });
  });

  elements.authList.querySelectorAll(".auth-toggle-button").forEach((button) => {
    button.addEventListener("click", async () => {
      const filename = button.getAttribute("data-filename") || "";
      const disabled = button.getAttribute("data-disabled") === "1";
      if (!filename) return;
      await callApi("set_auth_file_disabled", button, filename, disabled);
      await refresh();
    });
  });
}

function renderEvents(snapshot) {
  const stage = snapshot.startup_stage || "idle";
  const events = Array.isArray(snapshot.events) ? snapshot.events : [];
  elements.startupStage.textContent = stage;
  if (!events.length) {
    elements.eventList.innerHTML = `<div class="event-empty">No runtime events yet.</div>`;
    return;
  }
  elements.eventList.innerHTML = events
    .slice(-4)
    .reverse()
    .map(
      (item) => `
        <article class="event-item">
          <div class="event-item-top">
            <span class="event-kind">${escapeHtml(item.kind || "event")}</span>
            <span class="event-time">${escapeHtml(item.time || "")}</span>
          </div>
          <p>${escapeHtml(item.message || "")}</p>
        </article>
      `,
    )
    .join("");
}

function renderUpdate(snapshot) {
  const currentVersion = snapshot.app_version || "-";
  const latestVersion = snapshot.update_latest_version || currentVersion || "-";
  const releaseChannel = snapshot.release_channel || "stable";
  const updateStatus = snapshot.update_status || "idle";
  const checkedAt = snapshot.update_checked_at || "";
  const publishedAt = snapshot.update_published_at || "";
  const configured = Boolean(snapshot.update_configured);
  const available = Boolean(snapshot.update_available);

  elements.currentVersion.textContent = currentVersion;
  elements.latestVersion.textContent = latestVersion;
  elements.releaseChannel.textContent = releaseChannel;
  elements.updateMessage.textContent =
    snapshot.update_message || "Update status will appear here.";

  const metaParts = [];
  if (checkedAt) metaParts.push(`Checked ${checkedAt}`);
  if (publishedAt) metaParts.push(`Released ${publishedAt}`);
  if (!metaParts.length) metaParts.push(configured ? "Ready to check GitHub Releases." : "GitHub Releases are not configured yet.");
  elements.updateMeta.textContent = metaParts.join(" · ");

  let badgeText = "Idle";
  if (updateStatus === "checking") badgeText = "Checking";
  else if (updateStatus === "available") badgeText = "Update";
  else if (updateStatus === "up_to_date") badgeText = "Latest";
  else if (updateStatus === "stopping_proxy") badgeText = "Stopping";
  else if (updateStatus === "downloading") badgeText = "Loading";
  else if (updateStatus === "launching_installer") badgeText = "Launching";
  else if (updateStatus === "closing_for_update") badgeText = "Closing";
  else if (updateStatus === "installer_ready") badgeText = "Ready";
  else if (updateStatus === "error") badgeText = "Error";
  else if (updateStatus === "not_configured") badgeText = "Setup";
  elements.updateBadge.textContent = badgeText;
  elements.updateBadge.className = `update-badge ${updateStatus}`;

  const updateBusy = ["checking", "stopping_proxy", "downloading", "launching_installer", "closing_for_update", "installer_ready"].includes(updateStatus);
  elements.checkUpdateButton.disabled = updateStatus === "checking" || updateBusy;
  elements.installUpdateButton.disabled = !available || !snapshot.update_download_url || updateBusy;
  elements.releaseNotesButton.disabled = false;
}

function render(snapshot) {
  elements.statusText.textContent = snapshot.status || "Stopped";
  elements.detailText.textContent = snapshot.detail || "";
  elements.runtimeIdentity.textContent = snapshot.runtime_identity || "user_oauth";
  elements.oauthSource.textContent = snapshot.oauth_source || "unknown";
  elements.runtimePhase.textContent = snapshot.runtime_phase || "stopped";
  elements.authPhase.textContent = snapshot.auth_phase || "idle";

  const running = Boolean(snapshot.running);
  elements.statusBadge.textContent = running ? "ON" : "OFF";
  elements.statusBadge.classList.toggle("on", running);
  elements.statusDot.classList.toggle("on", running);

  elements.startButton.disabled = running;
  elements.stopButton.disabled = !running;
  renderAuthFiles(snapshot);
  renderEvents(snapshot);
  renderUpdate(snapshot);

  const seq = Number(snapshot.log_seq || 0);
  const logs = Array.isArray(snapshot.logs) ? snapshot.logs : [];
  if (seq !== state.lastSeq) {
    const shouldStick = nearBottom(elements.logWrap);
    elements.logOutput.textContent = logs.join("\n");
    elements.logCounter.textContent = `${logs.length} ${logs.length === 1 ? "line" : "lines"}`;
    if (shouldStick || state.lastSeq < 0) {
      elements.logWrap.scrollTop = elements.logWrap.scrollHeight;
    }
    state.lastSeq = seq;
  }

  state.lastRunning = running;
}

async function callApi(method, button = null, ...args) {
  if (!window.pywebview?.api?.[method]) {
    setToast("Desktop bridge is not ready yet.");
    return null;
  }
  if (button) setBusy(button, true);
  try {
    return await window.pywebview.api[method](...args);
  } catch (error) {
    setToast(String(error && error.message ? error.message : error || "Action failed."));
    return null;
  } finally {
    if (button) setBusy(button, false);
  }
}

async function refresh() {
  const snapshot = await callApi("get_state");
  if (snapshot) render(snapshot);
}

async function copyLog() {
  const text = elements.logOutput.textContent || "";
  try {
    await navigator.clipboard.writeText(text);
    setToast("Log copied.");
  } catch (_error) {
    setToast("Clipboard write failed.");
  }
}

function bind() {
  elements.overviewTabButton.addEventListener("click", () => switchTab("overview"));
  elements.stateTabButton.addEventListener("click", () => switchTab("state"));
  elements.updateTabButton.addEventListener("click", () => switchTab("update"));
  elements.authTabButton.addEventListener("click", () => switchTab("auth"));
  elements.logTabButton.addEventListener("click", () => switchTab("log"));
  elements.startButton.addEventListener("click", async () => {
    await callApi("start_backend", elements.startButton);
    await refresh();
  });
  elements.stopButton.addEventListener("click", async () => {
    await callApi("stop_backend", elements.stopButton);
    await refresh();
  });
  elements.oauthButton.addEventListener("click", async () => {
    await callApi("start_google_oauth", elements.oauthButton);
    await refresh();
  });
  elements.openWebButton.addEventListener("click", () => callApi("open_web_app"));
  elements.checkUpdateButton.addEventListener("click", async () => {
    await callApi("check_for_updates", elements.checkUpdateButton);
    await refresh();
  });
  elements.installUpdateButton.addEventListener("click", async () => {
    if (!window.confirm("Stop the local proxy, launch the installer, and close Research Companion?")) return;
    const result = await callApi("install_update", elements.installUpdateButton);
    if (result?.ok) {
      setToast("Installer launched. Research Companion will close.");
    }
    await refresh();
  });
  elements.releaseNotesButton.addEventListener("click", async () => {
    const result = await callApi("open_release_notes", elements.releaseNotesButton);
    if (result && result.ok === false && result.message) {
      setToast(result.message);
    }
  });
  elements.clearButton.addEventListener("click", async () => {
    await callApi("clear_log", elements.clearButton);
    await refresh();
  });
  elements.copyButton.addEventListener("click", copyLog);
  elements.refreshAuthButton.addEventListener("click", async () => {
    await callApi("refresh_auth_files", elements.refreshAuthButton);
    await refresh();
  });
  elements.openAuthFolderButton.addEventListener("click", () => callApi("open_auth_folder"));
}

function startPolling() {
  refresh();
  window.setInterval(refresh, 700);
}

window.addEventListener("pywebviewready", () => {
  bind();
  startPolling();
});
