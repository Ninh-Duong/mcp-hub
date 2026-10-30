// Optional activity UI (barn, daily activity, connected clients). The Hub reports it in
// status.extensions.activityUi; its modules are imported only when it is on (hubctl open -Ui).
import { $ } from "./dom.js";

const PANEL_IDS = ["farm-panel", "daily-panel", "clients-panel"];
let modules = null;
let loading = null;

export function activityUiEnabled(status) {
  return Boolean(status?.extensions?.activityUi);
}

function showPanels(enabled) {
  for (const id of PANEL_IDS) $(id).hidden = !enabled;
  $("activity-ui-hint").hidden = enabled;
}

async function loadModules() {
  const [barn, activity, stream] = await Promise.all([
    import("./barn.js"), import("./activity.js"), import("./stream.js"),
  ]);
  barn.watchMotionPreference();
  activity.initActivityControls();
  return { barn, activity, stream };
}

// Shows or hides the activity panels for this status and returns the loaded modules, or null when off.
export async function syncActivityUi(status) {
  const enabled = activityUiEnabled(status);
  showPanels(enabled);
  if (!enabled) {
    modules?.stream.disconnectActivityStream();
    return null;
  }
  if (!modules) {
    loading ||= loadModules();
    try { modules = await loading; }
    finally { loading = null; }
  }
  return modules;
}

// The loaded modules, or null while the activity UI is off or not loaded yet.
export function activityUi() {
  return modules;
}
