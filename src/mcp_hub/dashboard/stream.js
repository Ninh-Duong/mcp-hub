// Live activity over Server-Sent Events: applies snapshots and per-call events to the barn.
// Part of the optional activity UI; app.js imports it only when the Hub runs with -Ui.
import { refreshDailyActivity, scheduleDailyRefresh, setChildFilterOptions } from "./activity.js";
import { renderBuffaloes } from "./barn.js";
import { $ } from "./dom.js";
import { translate } from "./i18n.js";
import { requestRefresh, shared } from "./state.js";

let activitySource = null;

// Keeps the newer live activity when a slower status poll returns an older sequence of the same stream.
function mergeActivity(status) {
  const priorActivity = shared.latestStatus?.activity;
  const nextActivity = status.activity || {};
  if (priorActivity?.stream_id === nextActivity.stream_id
      && (priorActivity.sequence || 0) > (nextActivity.sequence || 0)) {
    status.activity = priorActivity;
  }
  shared.latestStatus = status;
  const activity = status.activity || {};
  if (activity.stream_id && activity.stream_id !== shared.activityStreamId) {
    shared.activityStreamId = activity.stream_id;
    shared.lastActivitySequence = activity.sequence || 0;
  } else {
    shared.lastActivitySequence = Math.max(shared.lastActivitySequence, activity.sequence || 0);
  }
}

// Applies a polled status to the barn and the child filter.
export function applyStatus(status) {
  mergeActivity(status);
  renderBuffaloes(status);
  setChildFilterOptions(status.children);
}

function setLiveStatus(state, key) {
  const indicator = $("live-status");
  indicator.dataset.state = state;
  translate(indicator, key);
}

function applyActivitySnapshot(snapshot) {
  const activity = snapshot.activity || { children: {}, events: [], sequence: 0 };
  shared.activityStreamId = activity.stream_id || null;
  shared.lastActivitySequence = activity.sequence || 0;
  shared.latestStatus = {
    ...(shared.latestStatus || {}),
    children: snapshot.children || {},
    activity,
  };
  renderBuffaloes(shared.latestStatus);
  setChildFilterOptions(shared.latestStatus.children);
  setLiveStatus("live", "live.live");
  void refreshDailyActivity();
}

function childActivity(serverId) {
  const activities = shared.latestStatus.activity.children || (shared.latestStatus.activity.children = {});
  return activities[serverId] || (activities[serverId] = {
    tool_count: 0, active_calls: 0, active_tools: [], active_call_details: [],
  });
}

function applyEventToChild(activity, event) {
  const calls = new Map((activity.active_call_details || []).map((call) => [String(call.call_id), call.tool]));
  if (event.state === "started") calls.set(String(event.call_id), event.tool);
  else calls.delete(String(event.call_id));
  activity.active_call_details = [...calls].map(([call_id, tool]) => ({ call_id, tool }));
  activity.active_tools = [...calls.values()];
  activity.active_calls = calls.size;
  activity.last_tool = event.tool;
  activity.last_state = event.state === "started" ? "running" : event.state;
  if (event.duration_ms != null) activity.last_duration_ms = event.duration_ms;
  activity.last_error_type = event.error_type || null;
}

function applyActivityEvent(event) {
  const status = shared.latestStatus;
  if (!status || event.stream_id !== shared.activityStreamId || event.sequence <= shared.lastActivitySequence) return;
  if (event.sequence > shared.lastActivitySequence + 1) {
    void requestRefresh();
    return;
  }
  shared.lastActivitySequence = event.sequence;
  applyEventToChild(childActivity(event.server_id), event);
  status.activity.sequence = event.sequence;
  status.activity.stream_id = event.stream_id;
  renderBuffaloes(status);
  if (event.state !== "started") scheduleDailyRefresh();
}

function handleStreamMessage(apply, message) {
  try { apply(JSON.parse(message.data)); }
  catch { setLiveStatus("reconnecting", "live.syncing"); }
}

export function disconnectActivityStream() {
  activitySource?.close();
  activitySource = null;
}

// Opens the stream unless one is open or reconnecting; a stream the browser gave up on is replaced.
export function ensureActivityStream() {
  if (activitySource && activitySource.readyState !== EventSource.CLOSED) return;
  connectActivityStream();
}

export function connectActivityStream() {
  activitySource?.close();
  const source = new EventSource("/dashboard/api/activity/stream");
  activitySource = source;
  source.onopen = () => setLiveStatus("live", "live.live");
  source.onerror = () => setLiveStatus("reconnecting", "live.reconnecting");
  source.addEventListener("snapshot", (message) => handleStreamMessage(applyActivitySnapshot, message));
  source.addEventListener("activity", (message) => handleStreamMessage(applyActivityEvent, message));
}
