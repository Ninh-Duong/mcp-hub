// Daily activity panel: summary metrics, per-child ranking, paged call history, and filters.
// Part of the optional activity UI; app.js imports it only when the Hub runs with -Ui.
import { api } from "./api.js";
import { $, element, mutedNote, notify } from "./dom.js";
import { formatTime, t } from "./i18n.js";
import { shared } from "./state.js";

const ACTIVITY_PAGE_SIZE = 20;
// Finished calls arrive in bursts; one report request per burst keeps the Hub's store idle.
const DAILY_REFRESH_DEBOUNCE_MS = 3000;
const FILTERS = [["server_id", "activity-child"], ["client", "activity-client"], ["tool", "activity-tool"]];
let activityOffset = 0;
let dailyRefreshTimer = null;
let lastReport = null;

function localDateValue(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function formatDuration(milliseconds) {
  const value = Number(milliseconds || 0);
  if (value < 1000) return t("duration.ms", { value });
  if (value < 60000) return t("duration.seconds", { value: (value / 1000).toFixed(value < 10000 ? 1 : 0) });
  const minutes = Math.floor(value / 60000);
  const seconds = Math.round((value % 60000) / 1000);
  return t("duration.minutes", { minutes, seconds });
}

function allOption() {
  return new Option(t("common.all"), "");
}

function setSelectOptions(select, values, label, preservePrevious = true) {
  const selected = select.value;
  const previous = preservePrevious ? [...select.options].map((option) => option.value).filter(Boolean) : [];
  const merged = [...new Set([...previous, ...values])];
  select.replaceChildren(allOption(), ...merged.map((value) => new Option(value, value)));
  if (values.includes(selected)) select.value = selected;
  select.setAttribute("aria-label", label);
}

export function setChildFilterOptions(children) {
  setSelectOptions($("activity-child"), Object.keys(children || {}), t("activity.filter.childAria"), false);
}

function renderActivitySummary(report) {
  const items = [
    [t("activity.metric.calls"), report.summary?.total_calls || 0],
    [t("activity.metric.duration"), formatDuration(report.summary?.total_duration_ms)],
    [t("activity.metric.failures"), report.summary?.failures || 0],
    [t("activity.metric.interrupted"), report.summary?.interrupted || 0],
    [t("activity.metric.clients"), (report.by_client || []).length],
  ];
  const cards = items.map(([label, value]) => {
    const card = element("div", "activity-metric");
    card.append(element("strong", "", String(value)), element("span", "", label));
    return card;
  });
  $("activity-summary").replaceChildren(...cards);
}

function rankingRow(row, index, metric, maximum) {
  const item = element("div", "ranking-row");
  const title = element("div", "ranking-title");
  const value = metric === "calls" ? t("activity.ranking.calls", { count: row.calls }) : formatDuration(row.duration_ms);
  title.append(element("strong", "", `${index + 1}. ${row.server_id}`), element("span", "", value));
  const track = element("div", "ranking-track");
  const bar = element("span");
  bar.style.width = `${Math.max(4, ((metric === "calls" ? row.calls : row.duration_ms) / maximum) * 100)}%`;
  track.append(bar);
  const meta = element("small", "", t("activity.ranking.meta", {
    failures: row.failures || 0, duration: formatDuration(row.duration_ms),
  }));
  item.append(title, track, meta);
  return item;
}

function renderActivityRanking(report) {
  const list = $("activity-ranking");
  const rows = report.by_child || [];
  if (!rows.length) {
    list.replaceChildren(mutedNote("p", t("activity.ranking.empty")));
    return;
  }
  const metric = $("activity-sort").value;
  const maximum = Math.max(1, ...rows.map((row) => metric === "calls" ? row.calls : row.duration_ms));
  list.replaceChildren(...rows.map((row, index) => rankingRow(row, index, metric, maximum)));
}

function stateLabel(state) {
  const labels = {
    running: t("callState.running"),
    succeeded: t("callState.succeeded"),
    failed: t("callState.failed"),
    interrupted: t("callState.interrupted"),
  };
  return labels[state] || state;
}

function eventDetails(event) {
  const parts = [event.client || t("activity.event.unknownClient")];
  if (event.agent_role) parts.push(t("activity.event.subAgent", { role: event.agent_role }));
  if (event.duration_ms != null) parts.push(formatDuration(event.duration_ms));
  if (event.error_type) parts.push(event.error_type);
  return parts.join(" · ");
}

function activityEventItem(event) {
  const item = element("article", `activity-event ${event.state || ""}`);
  const top = element("div", "activity-event-top");
  top.append(
    element("strong", "", `${event.server_id} · ${event.tool}`),
    element("span", `state-${event.state || "unknown"}`, stateLabel(event.state)),
  );
  const time = element("time", "", formatTime(event.started_at, { seconds: true }));
  time.dateTime = event.started_at;
  item.append(top, time, element("span", "activity-event-details", eventDetails(event)));
  return item;
}

function renderActivityPagination(report) {
  const label = $("activity-page-label");
  delete label.dataset.i18n;
  label.textContent = t("activity.page", {
    page: Math.floor(activityOffset / ACTIVITY_PAGE_SIZE) + 1,
    date: report.date || "",
    timezone: report.timezone || t("activity.localTime"),
  });
  $("activity-prev").disabled = activityOffset === 0;
  $("activity-next").disabled = !report.has_more;
}

function renderActivityEvents(report) {
  const events = report.events || [];
  const items = events.length ? events.map(activityEventItem) : [mutedNote("p", t("activity.events.empty"))];
  $("activity-events").replaceChildren(...items);
  renderActivityPagination(report);
}

function renderDailyActivity(report) {
  lastReport = report;
  renderActivitySummary(report);
  renderActivityRanking(report);
  renderActivityEvents(report);
  setSelectOptions($("activity-client"), (report.by_client || []).map((item) => item.client), t("activity.filter.clientAria"));
  setSelectOptions($("activity-tool"), (report.by_tool || []).map((item) => item.tool), t("activity.filter.toolAria"));
  if (shared.latestStatus) setChildFilterOptions(shared.latestStatus.children);
}

// Re-renders the last report in the current language without another request.
export function rerenderDailyActivity() {
  if (lastReport) renderDailyActivity(lastReport);
}

function activityQuery() {
  const query = new URLSearchParams({
    date: $("activity-date").value,
    sort: $("activity-sort").value,
    offset: String(activityOffset),
    limit: String(ACTIVITY_PAGE_SIZE),
  });
  for (const [key, id] of FILTERS) {
    if ($(id).value) query.set(key, $(id).value);
  }
  return query;
}

export async function refreshDailyActivity() {
  try {
    renderDailyActivity(await api(`/dashboard/api/activity?${activityQuery()}`));
  } catch (error) { notify(error.message); }
}

export function scheduleDailyRefresh() {
  window.clearTimeout(dailyRefreshTimer);
  dailyRefreshTimer = window.setTimeout(() => { void refreshDailyActivity(); }, DAILY_REFRESH_DEBOUNCE_MS);
}

function resetFilters() {
  for (const [, id] of FILTERS) {
    $(id).value = "";
    $(id).replaceChildren(allOption());
  }
}

function changePage(step) {
  activityOffset = Math.max(0, activityOffset + step * ACTIVITY_PAGE_SIZE);
  void refreshDailyActivity();
}

export function initActivityControls() {
  $("activity-date").value = localDateValue();
  for (const id of ["activity-date", "activity-sort", "activity-child", "activity-client", "activity-tool"]) {
    $(id).addEventListener("change", () => {
      activityOffset = 0;
      if (id === "activity-date") resetFilters();
      void refreshDailyActivity();
    });
  }
  $("activity-prev").addEventListener("click", () => changePage(-1));
  $("activity-next").addEventListener("click", () => changePage(1));
}
