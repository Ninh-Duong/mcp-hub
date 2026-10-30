// Status panels: Hub health, sub-agents, project aliases, and (with the activity UI) connected AI clients.
import { $, element, mutedNote } from "./dom.js";
import { formatTime, t, translate } from "./i18n.js";

export function setHealth(ok) {
  const health = $("health");
  health.className = ok ? "health ok" : "health bad";
  translate(health.lastElementChild, ok ? "health.ok" : "health.bad");
}

function clock(value) {
  return formatTime(value, { fallback: "—" });
}

function clientState(client) {
  if (!client.live) return t("clients.recent");
  return client.connections > 1 ? t("clients.liveMany", { count: client.connections }) : t("clients.live");
}

function clientCard(client) {
  const card = element("div", "agent");
  card.append(element("strong", "", client.client), element("span", client.live ? "ready" : "", clientState(client)));
  if (client.connected_at) card.append(element("span", "", t("clients.connectedAt", { time: clock(client.connected_at) })));
  card.append(element("span", "", t("clients.lastSeen", { time: clock(client.last_seen) })));
  return card;
}

export function renderClients(clientList) {
  $("client-count").textContent = String(clientList.filter((client) => client.live).length);
  const cards = clientList.length ? clientList.map(clientCard) : [mutedNote("span", t("clients.empty"))];
  $("clients").replaceChildren(...cards);
}

// Why a role cannot run (`unavailable` in /dashboard/api/status) -> locale key.
const UNAVAILABLE_KEYS = { "windows-only": "agents.windowsOnly", "package-missing": "agents.packageMissing" };

function agentState(agent) {
  if (agent.configured) return t("agents.ready");
  return t(UNAVAILABLE_KEYS[agent.unavailable] || "agents.notConfigured");
}

function agentCard(name, agent) {
  const card = element("div", "agent");
  const provider = `${agent.provider || t("agents.noProvider")} · ${agent.model || t("agents.noModel")}`;
  const state = element("span", agent.configured ? "ready" : "", agentState(agent));
  card.append(element("strong", "", name), element("span", "", provider), state);
  return card;
}

function renderAgents(agents) {
  $("agents").replaceChildren(...Object.entries(agents).map(([name, agent]) => agentCard(name, agent)));
}

function projectChip(project) {
  const defaultEnv = project.default_env || t("projects.needsEnv");
  return element("span", "chip connected", `${project.alias} · ${defaultEnv} · ${project.allowed_envs.join(", ")}`);
}

function renderProjects(projects) {
  const chips = projects.length ? projects.map(projectChip) : [mutedNote("span", t("projects.empty"))];
  $("projects").replaceChildren(...chips);
}

export function renderStatus(status) {
  setHealth(true);
  renderAgents(status.agents || {});
  renderProjects(status.projects || []);
}
