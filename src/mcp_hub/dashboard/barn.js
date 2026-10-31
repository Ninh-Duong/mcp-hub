// Pixel world: one buffalo actor per child MCP that walks from the barn to the field while it runs tool calls.
import { $, element } from "./dom.js";
import { t } from "./i18n.js";

const WORLD = { width: 480, height: 270, gateX: 253, barn: { left: 34, right: 221, top: 54, bottom: 222 } };
const GATE_Y = 134;
const BUFFALO_PALETTE = ["#6d523d", "#725b48", "#5e4d3f", "#806342", "#665548", "#775d49"];
const LEG_POSITIONS = [[8, 9], [26, 9], [8, 27], [26, 27]];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const buffaloActors = new Map();
let selectedChildId = null;
let animationFrame = null;
let lastFrameAt = 0;

function svgElement(name, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

function makeBuffaloLegs() {
  const legGroup = svgElement("g", { class: "buffalo-legs" });
  for (const [x, y] of LEG_POSITIONS) {
    legGroup.append(svgElement("rect", { x, y, width: 6, height: 7, fill: "#44372c" }));
    legGroup.append(svgElement("rect", { x: x + 1, y: y + 4, width: 5, height: 3, fill: "#302820" }));
  }
  return legGroup;
}

function makeBuffalo() {
  const svg = svgElement("svg", {
    viewBox: "0 0 40 40",
    class: "buffalo-sprite",
    "aria-hidden": "true",
    "shape-rendering": "crispEdges",
  });
  svg.append(
    makeBuffaloLegs(),
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

function selectActor(actor) {
  selectedChildId = actor.name;
  renderBuffaloDetail(actor.name, actor.activity || {}, actor.connection);
  for (const item of buffaloActors.values()) item.element.classList.toggle("selected", item.name === actor.name);
}

function makeActorElement(name, index) {
  const node = element("button", "buffalo-actor");
  node.type = "button";
  node.dataset.child = name;
  node.dataset.mode = "idle";
  node.dataset.facing = "south";
  node.style.setProperty("--buffalo-coat", BUFFALO_PALETTE[index % BUFFALO_PALETTE.length]);
  node.append(element("span", "buffalo-tag", name), element("span", "buffalo-working-tag"), makeBuffalo());
  return node;
}

function makeBuffaloActor(name, index) {
  const position = barnPosition(index);
  const actor = {
    name, index, element: makeActorElement(name, index), x: position.x, y: position.y, mode: "idle",
    connection: "connected", activeCalls: new Map(), route: [], target: null,
    waitUntil: performance.now() + index * 180,
  };
  actor.element.addEventListener("click", () => selectActor(actor));
  setActorPosition(actor);
  return actor;
}

function connectionLabel(state) {
  const labels = {
    connected: t("connection.connected"),
    connecting: t("connection.connecting"),
    reconnecting: t("connection.reconnecting"),
    disabled: t("connection.disabled"),
    "credentials required": t("connection.credentialsRequired"),
    unavailable: t("connection.unavailable"),
  };
  return labels[state] || state || t("connection.unknown");
}

function lastCallText(activity) {
  if (activity.active_calls) return t("barn.detail.running", { tools: (activity.active_tools || []).join(", ") });
  if (!activity.last_tool) return t("barn.detail.none");
  const outcome = activity.last_state === "failed" ? t("barn.detail.outcome.failed") : t("barn.detail.outcome.done");
  return t("barn.detail.last", { tool: activity.last_tool, duration: activity.last_duration_ms ?? 0, outcome });
}

function buffaloSummary(activity, connection) {
  const activeTools = activity.active_tools || [];
  const activePart = t("barn.detail.activeCalls", { count: activity.active_calls || 0 })
    + (activeTools.length ? ` (${activeTools.join(", ")})` : "");
  const parts = [
    connectionLabel(connection),
    t("barn.detail.toolCount", { count: activity.tool_count || 0 }),
    activePart,
    lastCallText(activity),
  ];
  if (activity.last_error_type) parts.push(t("barn.detail.lastError", { error: activity.last_error_type }));
  return parts.join(" · ");
}

function renderBuffaloDetail(name, activity, connection) {
  const detail = $("buffalo-detail");
  detail.hidden = false;
  detail.replaceChildren(element("strong", "", name), element("span", "", buffaloSummary(activity, connection)));
}

function placeActorAtRest(actor, position, mode) {
  actor.x = position.x;
  actor.y = position.y;
  actor.mode = mode;
  actor.target = null;
  actor.route = [];
}

function sendBuffaloToField(actor) {
  const field = fieldPosition(actor.index);
  if (reducedMotion.matches) {
    placeActorAtRest(actor, field, "working");
    return;
  }
  actor.route = [{ x: WORLD.gateX, y: GATE_Y }, field];
  actor.mode = "outbound";
  actor.target = null;
}

function returnBuffaloToBarn(actor) {
  const home = barnPosition(actor.index);
  if (reducedMotion.matches) {
    placeActorAtRest(actor, home, "idle");
    return;
  }
  actor.route = actor.x < WORLD.gateX ? [home] : [{ x: WORLD.gateX, y: GATE_Y }, home];
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

function updateTravel(actor, delta, now) {
  if (!actor.target && actor.route.length) actor.target = { ...actor.route.shift(), speed: actor.mode === "outbound" ? 210 : 240 };
  if (actor.target && !moveActorToward(actor, actor.target, delta)) return;
  if (actor.target || actor.route.length) return;
  if (actor.mode === "outbound") {
    if (actor.activeCalls.size) actor.mode = "working";
    else returnBuffaloToBarn(actor);
  } else {
    actor.mode = "idle";
    actor.waitUntil = now + 350;
  }
}

function updateWander(actor, delta, now) {
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

function updateActorMovement(actor, delta, now) {
  if (actor.mode === "unavailable" || actor.mode === "working") return;
  if (actor.mode === "outbound" || actor.mode === "returning") updateTravel(actor, delta, now);
  else updateWander(actor, delta, now);
}

function actorLabel(actor) {
  if (actor.connection !== "connected") return connectionLabel(actor.connection);
  const active = actor.activeCalls.size;
  if (active) {
    return actor.mode === "working"
      ? t("barn.label.working", { count: active })
      : t("barn.label.outbound", { count: active });
  }
  return actor.mode === "returning" ? t("barn.label.returning") : t("barn.label.resting");
}

function updateActorLabel(actor) {
  const label = actorLabel(actor);
  actor.element.dataset.mode = actor.mode;
  actor.element.classList.toggle("selected", selectedChildId === actor.name);
  actor.element.querySelector(".buffalo-working-tag").textContent = label;
  actor.element.setAttribute("aria-label", t("barn.actorAria", { name: actor.name, label }));
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

export function pauseWorldAnimation() {
  if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
  animationFrame = null;
}

export function resumeWorldAnimation() {
  lastFrameAt = 0;
  ensureWorldAnimation();
}

function clearAllActors() {
  $("active-count").textContent = "0";
  for (const actor of buffaloActors.values()) actor.element.remove();
  buffaloActors.clear();
}

function pruneRemovedActors(states) {
  for (const [name, actor] of buffaloActors) {
    if (!Object.hasOwn(states, name)) {
      actor.element.remove();
      buffaloActors.delete(name);
    }
  }
}

function ensureActor(name, index) {
  let actor = buffaloActors.get(name);
  if (!actor) {
    actor = makeBuffaloActor(name, index);
    buffaloActors.set(name, actor);
    $("children").append(actor.element);
  }
  return actor;
}

// Moves an actor between barn, field, and unavailable according to its latest state; returns whether it is busy.
function syncActor(actor, index, connection, activity) {
  actor.index = index;
  actor.activity = activity;
  actor.connection = connection;
  actor.activeCalls = new Map((activity.active_call_details || []).map((call) => [String(call.call_id), call.tool]));
  const active = connection === "connected" && actor.activeCalls.size > 0;
  const travelling = actor.mode === "outbound" || actor.mode === "working";
  if (connection !== "connected") {
    actor.mode = "unavailable";
    actor.target = null;
    actor.route = [];
  } else if (active && !travelling) {
    sendBuffaloToField(actor);
  } else if (!active && travelling) {
    returnBuffaloToBarn(actor);
  } else if (actor.mode === "unavailable") {
    const home = barnPosition(index);
    actor.mode = "idle";
    actor.x = home.x;
    actor.y = home.y;
    actor.waitUntil = performance.now() + 350;
  }
  updateActorLabel(actor);
  return active;
}

function countActiveCalls(states, activities) {
  return Object.entries(activities).reduce((sum, [name, item]) => (
    sum + (states[name] === "connected" ? item.active_calls || 0 : 0)
  ), 0);
}

function refreshSelectedDetail(states) {
  if (!selectedChildId || !Object.hasOwn(states, selectedChildId)) return;
  const actor = buffaloActors.get(selectedChildId);
  renderBuffaloDetail(selectedChildId, actor.activity, actor.connection);
}

export function renderBuffaloes(status) {
  const states = status.children || {};
  const activities = status.activity?.children || {};
  const names = Object.keys(states);
  $("world-empty").hidden = names.length > 0;
  if (!names.length) {
    clearAllActors();
    return;
  }
  pruneRemovedActors(states);
  let anyBusy = false;
  names.forEach((name, index) => {
    const busy = syncActor(ensureActor(name, index), index, states[name], activities[name] || {});
    anyBusy ||= busy;
  });
  $("active-count").textContent = String(countActiveCalls(states, activities));
  $("farm-title").closest("section").classList.toggle("dispatching", anyBusy);
  refreshSelectedDetail(states);
  ensureWorldAnimation();
}

function snapActorToRest(actor) {
  const busy = actor.connection === "connected" && actor.activeCalls.size > 0;
  const position = busy ? fieldPosition(actor.index) : barnPosition(actor.index);
  const mode = busy ? "working" : actor.connection === "connected" ? "idle" : "unavailable";
  placeActorAtRest(actor, position, mode);
  updateActorLabel(actor);
}

function syncMotionPreference() {
  pauseWorldAnimation();
  $("motion-note").hidden = !reducedMotion.matches;
  if (reducedMotion.matches) {
    for (const actor of buffaloActors.values()) snapActorToRest(actor);
  } else {
    ensureWorldAnimation();
  }
}

export function watchMotionPreference() {
  reducedMotion.addEventListener("change", syncMotionPreference);
  syncMotionPreference();
}
