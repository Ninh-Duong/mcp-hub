// Dashboard bootstrap: language, polling refresh loop, Hub controls, and the optional activity UI.
import { api } from "./api.js";
import { $, notify } from "./dom.js";
import { applyStaticTranslations, getLanguage, preferredLanguage, setLanguage, t } from "./i18n.js";
import { renderJobs, rerenderJobs, selectedJob, selectJob } from "./jobs.js";
import { setRefreshHandler, shared } from "./state.js";
import { renderClients, renderStatus, setHealth } from "./status.js";
import { activityUi, syncActivityUi } from "./ui_extension.js";

const REFRESH_MS = 10000;
let refreshTimer = null;

// Polls only while the tab is visible; a hidden tab costs the Hub nothing.
function scheduleRefresh(delay) {
  window.clearTimeout(refreshTimer);
  refreshTimer = document.hidden ? null : window.setTimeout(refresh, delay);
}

async function renderActivityUi(status) {
  const ui = await syncActivityUi(status);
  if (!ui) {
    shared.latestStatus = status;
    return;
  }
  ui.stream.applyStatus(status);
  renderClients(status.clients || []);
  await ui.activity.refreshDailyActivity();
  if (!document.hidden) ui.stream.ensureActivityStream();
}

async function refresh() {
  try {
    const [status, jobData] = await Promise.all([
      api("/dashboard/api/status"),
      api("/dashboard/api/jobs?limit=100"),
    ]);
    renderStatus(status);
    renderJobs(jobData.jobs || []);
    await renderActivityUi(status);
    const jobId = selectedJob();
    // The poll re-renders the open job only when it changed, so an open evidence view stays put.
    if (jobId && (jobData.jobs || []).some((job) => job.job_id === jobId)) await selectJob(jobId, { force: false });
    scheduleRefresh(REFRESH_MS);
  } catch (error) {
    setHealth(false);
    notify(error.message);
    scheduleRefresh(REFRESH_MS);
  }
}

// A hidden tab closes the live stream and stops polling; showing it again resumes both.
function handleVisibilityChange() {
  const ui = activityUi();
  if (document.hidden) {
    window.clearTimeout(refreshTimer);
    ui?.stream.disconnectActivityStream();
    ui?.barn.pauseWorldAnimation();
  } else {
    void refresh();
    ui?.barn.resumeWorldAnimation();
  }
}

async function restartHub() {
  await api("/dashboard/api/control/restart", { method: "POST" });
  notify(t("controls.restartSent"));
}

async function stopHub() {
  if (!window.confirm(t("controls.stopConfirm"))) return;
  await api("/dashboard/api/control/stop", { method: "POST" });
  notify(t("controls.stopSent"));
}

// Re-translates static markup and re-renders every dynamic panel from cached data.
function rerenderAll() {
  applyStaticTranslations(document);
  const ui = activityUi();
  if (shared.latestStatus?.agents) renderStatus(shared.latestStatus);
  if (ui && shared.latestStatus) {
    ui.barn.renderBuffaloes(shared.latestStatus);
    renderClients(shared.latestStatus.clients || []);
    ui.activity.rerenderDailyActivity();
  }
  void rerenderJobs();
}

async function changeLanguage(lang) {
  try {
    await setLanguage(lang);
  } catch (error) {
    notify(error.message);
  }
  $("language").value = getLanguage();
  rerenderAll();
}

function bindControls() {
  $("refresh").addEventListener("click", refresh);
  $("restart").addEventListener("click", restartHub);
  $("stop").addEventListener("click", stopHub);
  $("language").addEventListener("change", (event) => { void changeLanguage(event.target.value); });
  document.addEventListener("visibilitychange", handleVisibilityChange);
}

async function main() {
  setRefreshHandler(refresh);
  await changeLanguage(preferredLanguage());
  bindControls();
  void refresh();
}

void main();
