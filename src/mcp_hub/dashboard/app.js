const $ = (id) => document.getElementById(id);
let selectedJobId = null;
let selectedChildId = null;
let refreshTimer = null;
let dailyRefreshTimer = null;
let lastActivitySequence = 0;
let activityStreamId = null;
let activityOffset = 0;
let latestStatus = null;
let activitySource = null;
let animationFrame = null;
let lastFrameAt = 0;
const buffaloActors = new Map();
const ACTIVITY_PAGE_SIZE = 20;
const WORLD = { width: 480, height: 270, gateX: 253, barn: { left: 34, right: 221, top: 54, bottom: 222 } };
const BUFFALO_PALETTE = ["#6d523d", "#725b48", "#5e4d3f", "#806342", "#665548", "#775d49"];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return response.json();
}

function notify(message) {
  const node = $("notice");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 3000);
}

function badge(state) {
  const node = document.createElement("span");
  node.className = `badge ${state || ""}`;
  node.textContent = state || "unknown";
  return node;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  return element;
}

function makeBuffalo() {
  const svg = svgElement("svg", {
    viewBox: "0 0 40 40",
    class: "buffalo-sprite",
    "aria-hidden": "true",
    "shape-rendering": "crispEdges",
  });
  const legGroup = svgElement("g", { class: "buffalo-legs" });
  for (const [x, y] of [[8, 9], [26, 9], [8, 27], [26, 27]]) {
    legGroup.append(svgElement("rect", { x, y, width: 6, height: 7, fill: "#44372c" }));
    legGroup.append(svgElement("rect", { x: x + 1, y: y + 4, width: 5, height: 3, fill: "#302820" }));
  }
  svg.append(
    legGroup,
    svgElement("rect", { x: 5, y: 3, width: 5, height: 9, fill: "#dfcda7" }),
    svgElement("rect", { x: 3, y: 1, width: 5, height: 4, fill: "#f0e4c8" }),
    svgElement("rect", { x: 30, y: 3, width: 5, height: 9, fill: "#dfcda7" }),
    svgElement("rect", { x: 32, y: 1, width: 5, height: 4, fill: "#f0e4c8" }),
    svgElement("rect", { x: 11, y: 5, width: 18, height: 10, fill: "#4e3b2d" }),
    svgElement("rect", { x: 13, y: 6, width: 14, height: 28, class: "buffalo-body" }),
    svgElement("rect", { x: 10, y: 13, width: 20, height: 14, class: "buffalo-body" }),
    svgElement("rect", { x: 16, y: 8, width: 8, height: 4, fill: "#ad8a5d" }),
    svgElement("rect", { x: 18, y: 5, width: 4, height: 4, fill: "#302820" }),
    svgElement("rect", { x: 18, y: 34, width: 4, height: 4, fill: "#4e3b2d" }),
  );
  return svg;
}

function barnPosition(index) {
  const column = index % 3;
  const row = Math.floor(index / 3);
  return { x: 66 + column * 66, y: 91 + row * 62 };
}

function fieldPosition(index) {
  const column = index % 3;
  const row = Math.floor(index / 3);
  return { x: 310 + column * 62, y: 77 + row * 62 };
}

function setActorPosition(actor) {
  actor.element.style.left = `${actor.x / WORLD.width * 100}%`;
  actor.element.style.top = `${actor.y / WORLD.height * 100}%`;
}

function makeBuffaloActor(name, index) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = "buffalo-actor";
  element.dataset.child = name;
  element.dataset.mode = "idle";
  element.dataset.facing = "south";
  element.style.setProperty("--buffalo-coat", BUFFALO_PALETTE[index % BUFFALO_PALETTE.length]);
  const nameTag = document.createElement("span");
  nameTag.className = "buffalo-tag";
  nameTag.textContent = name;
  const workingTag = document.createElement("span");
  workingTag.className = "buffalo-working-tag";
  const sprite = makeBuffalo();
  element.append(nameTag, workingTag, sprite);
  const position = barnPosition(index);
  const actor = {
    name, index, element, x: position.x, y: position.y, mode: "idle", connection: "connected",
    activeCalls: new Map(), route: [], target: null, waitUntil: performance.now() + index * 180,
  };
  element.addEventListener("click", () => {
    selectedChildId = name;
    renderBuffaloDetail(name, actor.activity || {}, actor.connection);
    for (const item of buffaloActors.values()) item.element.classList.toggle("selected", item.name === name);
  });
  setActorPosition(actor);
  return actor;
}

function connectionLabel(state) {
  return ({
    connected: "Đã kết nối",
    disabled: "Đang tắt",
    "credentials required": "Thiếu thông tin xác thực",
    unavailable: "Không khả dụng",
  })[state] || state || "Không rõ trạng thái";
}

function renderBuffaloDetail(name, activity, connection) {
  const detail = $("buffalo-detail");
  detail.replaceChildren();
  detail.hidden = false;
  const heading = document.createElement("strong");
  heading.textContent = name;
  const summary = document.createElement("span");
  const activeTools = activity.active_tools || [];
  const last = activity.active_calls
    ? `Đang chạy: ${activeTools.join(", ")}`
    : activity.last_tool
      ? `Gần nhất: ${activity.last_tool} · ${activity.last_duration_ms ?? 0} ms · ${activity.last_state === "failed" ? "lỗi" : "xong"}`
    : "Chưa có lời gọi tool trong phiên này";
  summary.textContent = `${connectionLabel(connection)} · ${activity.tool_count || 0} tool · ${activity.active_calls || 0} lượt đang chạy${activeTools.length ? ` (${activeTools.join(", ")})` : ""} · ${last}${activity.last_error_type ? ` · Lỗi gần nhất: ${activity.last_error_type}` : ""}`;
  detail.append(heading, summary);
}

function sendBuffaloToField(actor) {
  const field = fieldPosition(actor.index);
  if (reducedMotion.matches) {
    actor.x = field.x;
    actor.y = field.y;
    actor.mode = "working";
    actor.target = null;
    actor.route = [];
    return;
  }
  actor.route = [{ x: WORLD.gateX, y: 134 }, field];
  actor.mode = "outbound";
  actor.target = null;
}

function returnBuffaloToBarn(actor) {
  const home = barnPosition(actor.index);
  if (reducedMotion.matches) {
    actor.x = home.x;
    actor.y = home.y;
    actor.mode = "idle";
    actor.target = null;
    actor.route = [];
    return;
  }
  actor.route = actor.x < WORLD.gateX
    ? [home]
    : [{ x: WORLD.gateX, y: 134 }, home];
  actor.mode = "returning";
  actor.target = null;
}

function faceActor(actor, dx, dy) {
  if (Math.abs(dx) > Math.abs(dy)) actor.element.dataset.facing = dx >= 0 ? "east" : "west";
  else actor.element.dataset.facing = dy >= 0 ? "south" : "north";
}

function moveActorToward(actor, target, delta) {
  const dx = target.x - actor.x;
  const dy = target.y - actor.y;
  const distance = Math.hypot(dx, dy);
  faceActor(actor, dx, dy);
  const step = target.speed * delta;
  if (distance <= step || distance < 0.5) {
    actor.x = target.x;
    actor.y = target.y;
    actor.target = null;
    return true;
  }
  actor.x += dx / distance * step;
  actor.y += dy / distance * step;
  return false;
}

function updateActorMovement(actor, delta, now) {
  if (actor.mode === "unavailable") return;
  if (actor.mode === "outbound" || actor.mode === "returning") {
    if (!actor.target && actor.route.length) actor.target = { ...actor.route.shift(), speed: actor.mode === "outbound" ? 210 : 240 };
    if (actor.target && !moveActorToward(actor, actor.target, delta)) return;
    if (!actor.target && !actor.route.length) {
      if (actor.mode === "outbound") {
        if (actor.activeCalls.size) actor.mode = "working";
        else returnBuffaloToBarn(actor);
      } else {
        actor.mode = "idle";
        actor.waitUntil = now + 350;
      }
    }
    return;
  }
  if (actor.mode === "working") return;
  if (now < actor.waitUntil) return;
  if (!actor.target) {
    actor.target = {
      x: WORLD.barn.left + Math.random() * (WORLD.barn.right - WORLD.barn.left),
      y: WORLD.barn.top + Math.random() * (WORLD.barn.bottom - WORLD.barn.top),
      speed: 24,
    };
  }
  if (moveActorToward(actor, actor.target, delta)) actor.waitUntil = now + 700 + Math.random() * 1500;
}

function updateActorLabel(actor) {
  const active = actor.connection === "connected" ? actor.activeCalls.size : 0;
  let label = actor.connection !== "connected"
    ? connectionLabel(actor.connection)
    : active
      ? actor.mode === "working" ? `Cày ×${active}` : `Ra ruộng ×${active}`
      : actor.mode === "returning" ? "Về chuồng" : "Đang nghỉ";
  actor.element.dataset.mode = actor.mode;
  actor.element.classList.toggle("selected", selectedChildId === actor.name);
  actor.element.querySelector(".buffalo-working-tag").textContent = label;
  actor.element.setAttribute("aria-label", `${actor.name}: ${label}. Chọn để xem hoạt động.`);
  setActorPosition(actor);
}

function animateWorld(now) {
  animationFrame = null;
  if (reducedMotion.matches || document.hidden) return;
  const delta = lastFrameAt ? Math.min((now - lastFrameAt) / 1000, 0.05) : 0;
  lastFrameAt = now;
  for (const actor of buffaloActors.values()) {
    updateActorMovement(actor, delta, now);
    updateActorLabel(actor);
  }
  animationFrame = window.requestAnimationFrame(animateWorld);
}

function ensureWorldAnimation() {
  if (reducedMotion.matches || document.hidden || animationFrame !== null) return;
  lastFrameAt = 0;
  animationFrame = window.requestAnimationFrame(animateWorld);
}

function renderBuffaloes(status) {
  const container = $("children");
  const states = status.children || {};
  const activities = status.activity?.children || {};
  const names = Object.keys(states);
  const empty = $("world-empty");
  empty.hidden = names.length > 0;
  if (!names.length) {
    $("active-count").textContent = "0";
    for (const actor of buffaloActors.values()) actor.element.remove();
    buffaloActors.clear();
    return;
  }

  for (const [name, actor] of buffaloActors) {
    if (!Object.hasOwn(states, name)) {
      actor.element.remove();
      buffaloActors.delete(name);
    }
  }

  let anyBusy = false;
  names.forEach((name, index) => {
    const connection = states[name];
    const activity = activities[name] || {};
    let actor = buffaloActors.get(name);
    if (!actor) {
      actor = makeBuffaloActor(name, index);
      buffaloActors.set(name, actor);
      container.append(actor.element);
    }
    actor.index = index;
    actor.activity = activity;
    actor.connection = connection;
    actor.activeCalls = new Map((activity.active_call_details || []).map((call) => [String(call.call_id), call.tool]));
    const active = connection === "connected" && actor.activeCalls.size > 0;
    if (connection !== "connected") {
      actor.mode = "unavailable";
      actor.target = null;
      actor.route = [];
    } else if (active && actor.mode !== "outbound" && actor.mode !== "working") {
      sendBuffaloToField(actor);
    } else if (!active && (actor.mode === "outbound" || actor.mode === "working")) {
      returnBuffaloToBarn(actor);
    } else if (actor.mode === "unavailable") {
      actor.mode = "idle";
      const home = barnPosition(index);
      actor.x = home.x;
      actor.y = home.y;
      actor.waitUntil = performance.now() + 350;
    }
    updateActorLabel(actor);
    anyBusy ||= active;
  });
  const count = Object.entries(activities).reduce((sum, [name, item]) => (
    sum + (states[name] === "connected" ? item.active_calls || 0 : 0)
  ), 0);
  $("active-count").textContent = String(count);
  $("farm-title").closest("section").classList.toggle("dispatching", anyBusy);
  if (selectedChildId && Object.hasOwn(states, selectedChildId)) {
    const actor = buffaloActors.get(selectedChildId);
    renderBuffaloDetail(selectedChildId, actor.activity, actor.connection);
  }
  ensureWorldAnimation();
}

function localDateValue(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function formatDuration(milliseconds) {
  const value = Number(milliseconds || 0);
  if (value < 1000) return `${value} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} giây`;
  const minutes = Math.floor(value / 60000);
  const seconds = Math.round((value % 60000) / 1000);
  return `${minutes} phút ${seconds} giây`;
}

function setSelectOptions(select, values, label, preservePrevious = true) {
  const selected = select.value;
  const previous = preservePrevious ? [...select.options].map((option) => option.value).filter(Boolean) : [];
  const merged = [...new Set([...previous, ...values])];
  const options = [new Option("Tất cả", ""), ...merged.map((value) => new Option(value, value))];
  select.replaceChildren(...options);
  if (values.includes(selected)) select.value = selected;
  select.setAttribute("aria-label", label);
}

function renderActivitySummary(report) {
  const summary = $("activity-summary");
  summary.replaceChildren();
  const items = [
    ["Lượt gọi", report.summary?.total_calls || 0],
    ["Tổng thời lượng", formatDuration(report.summary?.total_duration_ms)],
    ["Lượt lỗi", report.summary?.failures || 0],
    ["Bị ngắt", report.summary?.interrupted || 0],
    ["AI client", (report.by_client || []).length],
  ];
  for (const [label, value] of items) {
    const card = document.createElement("div");
    card.className = "activity-metric";
    const number = document.createElement("strong");
    number.textContent = String(value);
    const caption = document.createElement("span");
    caption.textContent = label;
    card.append(number, caption);
    summary.append(card);
  }
}

function renderActivityRanking(report) {
  const list = $("activity-ranking");
  list.replaceChildren();
  const rows = report.by_child || [];
  if (!rows.length) {
    list.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "Ngày này chưa có lời gọi MCP." }));
    return;
  }
  const metric = $("activity-sort").value;
  const maximum = Math.max(1, ...rows.map((row) => metric === "calls" ? row.calls : row.duration_ms));
  rows.forEach((row, index) => {
    const item = document.createElement("div");
    item.className = "ranking-row";
    const title = document.createElement("div");
    title.className = "ranking-title";
    const name = document.createElement("strong");
    name.textContent = `${index + 1}. ${row.server_id}`;
    const value = document.createElement("span");
    value.textContent = metric === "calls" ? `${row.calls} lượt` : formatDuration(row.duration_ms);
    title.append(name, value);
    const track = document.createElement("div");
    track.className = "ranking-track";
    const bar = document.createElement("span");
    bar.style.width = `${Math.max(4, ((metric === "calls" ? row.calls : row.duration_ms) / maximum) * 100)}%`;
    track.append(bar);
    const meta = document.createElement("small");
    meta.textContent = `${row.failures || 0} lỗi · ${formatDuration(row.duration_ms)} tổng thời lượng`;
    item.append(title, track, meta);
    list.append(item);
  });
}

function stateLabel(state) {
  return ({ running: "Đang chạy", succeeded: "Thành công", failed: "Lỗi", interrupted: "Bị ngắt" })[state] || state;
}

function renderActivityEvents(report) {
  const list = $("activity-events");
  list.replaceChildren();
  const events = report.events || [];
  if (!events.length) {
    list.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "Không có lượt gọi phù hợp." }));
  }
  for (const event of events) {
    const item = document.createElement("article");
    item.className = `activity-event ${event.state || ""}`;
    const top = document.createElement("div");
    top.className = "activity-event-top";
    const tool = document.createElement("strong");
    tool.textContent = `${event.server_id} · ${event.tool}`;
    const state = document.createElement("span");
    state.className = `state-${event.state || "unknown"}`;
    state.textContent = stateLabel(event.state);
    top.append(tool, state);
    const time = document.createElement("time");
    const started = new Date(event.started_at);
    time.dateTime = event.started_at;
    time.textContent = Number.isNaN(started.getTime()) ? "" : started.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const details = document.createElement("span");
    details.className = "activity-event-details";
    const parts = [event.client || "không rõ client"];
    if (event.agent_role) parts.push(`sub-agent ${event.agent_role}`);
    if (event.duration_ms != null) parts.push(formatDuration(event.duration_ms));
    if (event.error_type) parts.push(event.error_type);
    details.textContent = parts.join(" · ");
    item.append(top, time, details);
    list.append(item);
  }
  const page = Math.floor(activityOffset / ACTIVITY_PAGE_SIZE) + 1;
  $("activity-page-label").textContent = `Trang ${page} · ${report.date || ""} (${report.timezone || "giờ máy"})`;
  $("activity-prev").disabled = activityOffset === 0;
  $("activity-next").disabled = !report.has_more;
}

function renderDailyActivity(report) {
  renderActivitySummary(report);
  renderActivityRanking(report);
  renderActivityEvents(report);
  setSelectOptions($("activity-client"), (report.by_client || []).map((item) => item.client), "Lọc theo AI client");
  setSelectOptions($("activity-tool"), (report.by_tool || []).map((item) => item.tool), "Lọc theo tool");
  if (latestStatus) {
    setSelectOptions($("activity-child"), Object.keys(latestStatus.children || {}), "Lọc theo MCP con", false);
  }
}

function renderStatus(status) {
  const priorActivity = latestStatus?.activity;
  const nextActivity = status.activity || {};
  if (priorActivity?.stream_id === nextActivity.stream_id
      && (priorActivity.sequence || 0) > (nextActivity.sequence || 0)) {
    status.activity = priorActivity;
  }
  latestStatus = status;
  const activity = status.activity || {};
  if (activity.stream_id && activity.stream_id !== activityStreamId) {
    activityStreamId = activity.stream_id;
    lastActivitySequence = activity.sequence || 0;
  } else {
    lastActivitySequence = Math.max(lastActivitySequence, activity.sequence || 0);
  }
  const health = $("health");
  health.className = "health ok";
  health.lastElementChild.textContent = "Hub đang hoạt động";

  renderBuffaloes(status);
  if (latestStatus) setSelectOptions($("activity-child"), Object.keys(status.children || {}), "Lọc theo MCP con", false);

  const agents = $("agents");
  agents.replaceChildren();
  for (const [name, agent] of Object.entries(status.agents || {})) {
    const card = document.createElement("div");
    card.className = "agent";
    const title = document.createElement("strong");
    title.textContent = name;
    const provider = document.createElement("span");
    provider.textContent = `${agent.provider || "provider chưa đặt"} · ${agent.model || "model chưa đặt"}`;
    const state = document.createElement("span");
    state.className = agent.configured ? "ready" : "";
    state.textContent = agent.configured ? "Sẵn sàng" : "Chưa cấu hình";
    card.append(title, provider, state);
    agents.append(card);
  }

  const projects = $("projects");
  projects.replaceChildren();
  if (!(status.projects || []).length) {
    projects.append(Object.assign(document.createElement("span"), { className: "muted", textContent: "Chưa đăng ký project alias" }));
  }
  for (const project of status.projects || []) {
    const chip = document.createElement("span");
    chip.className = "chip connected";
    const defaultEnv = project.default_env || "cần chọn env";
    chip.textContent = `${project.alias} · ${defaultEnv} · ${project.allowed_envs.join(", ")}`;
    projects.append(chip);
  }
}

function setLiveStatus(state, label) {
  const indicator = $("live-status");
  indicator.dataset.state = state;
  indicator.textContent = label;
}

function applyActivitySnapshot(snapshot) {
  const activity = snapshot.activity || { children: {}, events: [], sequence: 0 };
  activityStreamId = activity.stream_id || null;
  lastActivitySequence = activity.sequence || 0;
  latestStatus = {
    ...(latestStatus || {}),
    children: snapshot.children || {},
    activity,
  };
  renderBuffaloes(latestStatus);
  setSelectOptions($("activity-child"), Object.keys(latestStatus.children), "Lọc theo MCP con", false);
  setLiveStatus("live", "ĐANG TRỰC TIẾP");
  void refreshDailyActivity();
}

function applyActivityEvent(event) {
  if (!latestStatus || event.stream_id !== activityStreamId || event.sequence <= lastActivitySequence) return;
  if (event.sequence > lastActivitySequence + 1) {
    void refresh();
    return;
  }
  lastActivitySequence = event.sequence;
  const activities = latestStatus.activity.children || (latestStatus.activity.children = {});
  const activity = activities[event.server_id] || (activities[event.server_id] = {
    tool_count: 0, active_calls: 0, active_tools: [], active_call_details: [],
  });
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
  latestStatus.activity.sequence = event.sequence;
  latestStatus.activity.stream_id = event.stream_id;
  renderBuffaloes(latestStatus);
  if (event.state !== "started") scheduleDailyRefresh();
}

function connectActivityStream() {
  activitySource?.close();
  const source = new EventSource("/dashboard/api/activity/stream");
  activitySource = source;
  source.onopen = () => setLiveStatus("live", "ĐANG TRỰC TIẾP");
  source.onerror = () => setLiveStatus("reconnecting", "ĐANG KẾT NỐI LẠI");
  source.addEventListener("snapshot", (message) => {
    try { applyActivitySnapshot(JSON.parse(message.data)); }
    catch { setLiveStatus("reconnecting", "ĐANG ĐỒNG BỘ"); }
  });
  source.addEventListener("activity", (message) => {
    try { applyActivityEvent(JSON.parse(message.data)); }
    catch { setLiveStatus("reconnecting", "ĐANG ĐỒNG BỘ"); }
  });
}

function renderJobs(items) {
  const list = $("jobs");
  list.replaceChildren();
  if (!items.length) {
    list.append(Object.assign(document.createElement("p"), { className: "muted", textContent: "Chưa có investigation job." }));
    return;
  }
  for (const job of items) {
    const button = document.createElement("button");
    button.className = `job ${job.job_id === selectedJobId ? "selected" : ""}`;
    button.type = "button";
    const top = document.createElement("div");
    top.className = "job-top";
    const id = document.createElement("span");
    id.className = "job-id";
    id.textContent = job.job_id;
    top.append(id, badge(job.state));
    const summary = document.createElement("span");
    summary.className = "job-summary";
    summary.textContent = job.summary || "Chưa có kết luận.";
    button.append(top, summary);
    button.addEventListener("click", () => selectJob(job.job_id));
    list.append(button);
  }
}

async function renderJob(job) {
  $("detail-title").textContent = `Job ${job.job_id.slice(0, 10)}`;
  const root = document.createElement("div");
  root.append(badge(job.state));
  if (job.summary) {
    const summary = document.createElement("p");
    summary.className = "summary";
    summary.textContent = job.summary;
    root.append(summary);
  }

  if (job.pending_approval) {
    const box = document.createElement("div");
    box.className = "approval";
    const label = document.createElement("span");
    label.textContent = `Đang chờ duyệt thao tác: ${job.pending_approval.tool_name}`;
    box.append(label);
    const preview = await api(`/dashboard/api/jobs/${encodeURIComponent(job.job_id)}/pending-call`);
    const explanation = document.createElement("p");
    explanation.className = "muted";
    explanation.textContent = "Kiểm tra tool và tham số bên dưới trước khi duyệt. Một số giá trị nhạy cảm đã được ẩn.";
    const operation = document.createElement("pre");
    operation.className = "detail-output approval-preview";
    operation.textContent = preview.arguments_preview;
    box.append(explanation, operation);
    const codeResult = await api(`/dashboard/api/jobs/${encodeURIComponent(job.job_id)}/approval-code`);
    if (codeResult.approval_code) {
      const code = document.createElement("code");
      code.className = "approval-code";
      code.textContent = codeResult.approval_code;
      const copy = document.createElement("button");
      copy.className = "button small secondary";
      copy.textContent = "Sao chép mã";
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(codeResult.approval_code);
        notify("Đã sao chép mã duyệt.");
      });
      box.append(code, copy);
    }
    const approve = document.createElement("button");
    approve.className = "button small";
    approve.textContent = "Duyệt thao tác";
    approve.addEventListener("click", async () => {
      await api(`/dashboard/api/jobs/${encodeURIComponent(job.job_id)}/approve`, { method: "POST" });
      notify("Đã gửi quyết định duyệt.");
      await refresh();
    });
    box.append(approve);
    root.append(box);
  }

  const evidence = document.createElement("div");
  evidence.className = "subheading";
  evidence.textContent = "Bằng chứng";
  root.append(evidence);
  const evidenceList = document.createElement("div");
  evidenceList.className = "evidence";
  for (const item of job.evidence_refs || []) {
    const button = document.createElement("button");
    button.className = "button quiet small";
    button.textContent = `${item.tool} · ${item.source_id}`;
    button.addEventListener("click", () => loadDetail(job.job_id, item.source_id, root));
    evidenceList.append(button);
  }
  if (!evidenceList.childElementCount) evidenceList.append(Object.assign(document.createElement("span"), { className: "muted", textContent: "Chưa có tool result." }));
  root.append(evidenceList);

  const timelineLabel = document.createElement("div");
  timelineLabel.className = "subheading";
  timelineLabel.textContent = "Timeline";
  root.append(timelineLabel);
  const timeline = document.createElement("div");
  timeline.className = "timeline";
  for (const event of job.events || []) {
    const row = document.createElement("div");
    row.className = "event";
    const time = document.createElement("time");
    time.textContent = new Date(event.created_at).toLocaleTimeString();
    const label = document.createElement("span");
    const parts = [event.event, event.agent, event.tool, event.duration_ms == null ? null : `${event.duration_ms} ms`].filter(Boolean);
    label.textContent = parts.join(" · ");
    row.append(time, label);
    timeline.append(row);
  }
  root.append(timeline);

  if (["queued", "awaiting_input", "running", "awaiting_approval"].includes(job.state)) {
    const cancel = document.createElement("button");
    cancel.className = "button danger small";
    cancel.textContent = "Hủy job";
    cancel.addEventListener("click", async () => {
      await api(`/dashboard/api/jobs/${encodeURIComponent(job.job_id)}/cancel`, { method: "POST" });
      notify("Đã gửi yêu cầu hủy.");
      await refresh();
    });
    root.append(cancel);
  }

  $("detail").replaceChildren(root);
}

async function loadDetail(jobId, sourceId, parent) {
  try {
    let offset = 0;
    const result = await api(`/dashboard/api/jobs/${encodeURIComponent(jobId)}/details/${encodeURIComponent(sourceId)}?offset=${offset}&limit=2000`);
    const output = document.createElement("pre");
    output.className = "detail-output";
    output.textContent = result.content;
    parent.querySelector(".detail-output")?.remove();
    parent.append(output);
    if (result.next_offset != null) {
      const next = document.createElement("button");
      next.className = "button quiet small";
      next.textContent = "Tải trang tiếp";
      next.addEventListener("click", async () => {
        offset = result.next_offset;
        const page = await api(`/dashboard/api/jobs/${encodeURIComponent(jobId)}/details/${encodeURIComponent(sourceId)}?offset=${offset}&limit=2000`);
        output.textContent += page.content;
        result.next_offset = page.next_offset;
        if (page.next_offset == null) next.remove();
      });
      parent.append(next);
    }
  } catch (error) { notify(error.message); }
}

async function selectJob(jobId) {
  selectedJobId = jobId;
  try { await renderJob(await api(`/dashboard/api/jobs/${encodeURIComponent(jobId)}`)); }
  catch (error) { notify(error.message); }
}

function activityQuery() {
  const query = new URLSearchParams({
    date: $("activity-date").value,
    sort: $("activity-sort").value,
    offset: String(activityOffset),
    limit: String(ACTIVITY_PAGE_SIZE),
  });
  for (const [key, id] of [["server_id", "activity-child"], ["client", "activity-client"], ["tool", "activity-tool"]]) {
    if ($(id).value) query.set(key, $(id).value);
  }
  return query;
}

async function refreshDailyActivity() {
  try {
    const report = await api(`/dashboard/api/activity?${activityQuery()}`);
    renderDailyActivity(report);
  } catch (error) { notify(error.message); }
}

function scheduleDailyRefresh() {
  window.clearTimeout(dailyRefreshTimer);
  dailyRefreshTimer = window.setTimeout(() => { void refreshDailyActivity(); }, 80);
}

async function refresh() {
  try {
    const [status, jobData] = await Promise.all([
      api("/dashboard/api/status"),
      api("/dashboard/api/jobs?limit=100"),
    ]);
    renderStatus(status);
    renderJobs(jobData.jobs || []);
    await refreshDailyActivity();
    if (selectedJobId) {
      const selected = (jobData.jobs || []).find((job) => job.job_id === selectedJobId);
      if (selected) await selectJob(selectedJobId);
    }
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refresh, document.hidden ? 30000 : 10000);
  } catch (error) {
    const health = $("health");
    health.className = "health bad";
    health.lastElementChild.textContent = "Không kết nối được";
    notify(error.message);
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refresh, 10000);
  }
}

function syncMotionPreference() {
  if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
  animationFrame = null;
  $("motion-note").hidden = !reducedMotion.matches;
  if (reducedMotion.matches) {
    for (const actor of buffaloActors.values()) {
      const busy = actor.connection === "connected" && actor.activeCalls.size > 0;
      const position = busy ? fieldPosition(actor.index) : barnPosition(actor.index);
      actor.x = position.x;
      actor.y = position.y;
      actor.mode = busy ? "working" : actor.connection === "connected" ? "idle" : "unavailable";
      actor.target = null;
      actor.route = [];
      updateActorLabel(actor);
    }
  } else {
    ensureWorldAnimation();
  }
}

reducedMotion.addEventListener("change", syncMotionPreference);
syncMotionPreference();
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
    animationFrame = null;
  } else {
    lastFrameAt = 0;
    void refresh();
    ensureWorldAnimation();
  }
});

$("activity-date").value = localDateValue();
for (const id of ["activity-date", "activity-sort", "activity-child", "activity-client", "activity-tool"]) {
  $(id).addEventListener("change", () => {
    activityOffset = 0;
    if (id === "activity-date") {
      for (const filter of ["activity-child", "activity-client", "activity-tool"]) {
        $(filter).value = "";
        $(filter).replaceChildren(new Option("Tất cả", ""));
      }
    }
    void refreshDailyActivity();
  });
}
$("activity-prev").addEventListener("click", () => {
  activityOffset = Math.max(0, activityOffset - ACTIVITY_PAGE_SIZE);
  void refreshDailyActivity();
});
$("activity-next").addEventListener("click", () => {
  activityOffset += ACTIVITY_PAGE_SIZE;
  void refreshDailyActivity();
});
$("refresh").addEventListener("click", refresh);
$("restart").addEventListener("click", async () => {
  await api("/dashboard/api/control/restart", { method: "POST" });
  notify("Đã gửi yêu cầu khởi động lại Hub.");
});
$("stop").addEventListener("click", async () => {
  if (!window.confirm("Dừng mcp-hub? Các client sẽ mất kết nối.")) return;
  await api("/dashboard/api/control/stop", { method: "POST" });
  notify("Đã gửi yêu cầu dừng Hub.");
});
void refresh();
connectActivityStream();
