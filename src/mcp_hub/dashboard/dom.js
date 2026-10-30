// Small DOM helpers shared by every dashboard module.
export function $(id) {
  return document.getElementById(id);
}

export function element(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null) node.textContent = text;
  return node;
}

export function mutedNote(tag, text) {
  return element(tag, "muted", text);
}

export function notify(message) {
  const node = $("notice");
  node.textContent = message;
  node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 3000);
}

export function badge(state) {
  return element("span", `badge ${state || ""}`, state || "unknown");
}
