// UI translations. Locale files live in /dashboard/locales/<lang>.json and share one key set.
const SUPPORTED_LANGUAGES = ["en", "vi"];
const DEFAULT_LANGUAGE = "en";
const STORAGE_KEY = "mcp-hub.dashboard.language";
const dictionaries = new Map();
let currentLanguage = DEFAULT_LANGUAGE;

function readStoredLanguage() {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeLanguage(lang) {
  try {
    window.localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // Storage can be blocked (private mode, disabled site data); the choice then lasts for this page only.
  }
}

async function loadDictionary(lang) {
  if (dictionaries.has(lang)) return dictionaries.get(lang);
  const response = await fetch(`/dashboard/locales/${lang}.json`, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`Locale "${lang}" failed to load (${response.status})`);
  const dictionary = await response.json();
  dictionaries.set(lang, dictionary);
  return dictionary;
}

export function preferredLanguage() {
  const stored = readStoredLanguage();
  return SUPPORTED_LANGUAGES.includes(stored) ? stored : DEFAULT_LANGUAGE;
}

export async function setLanguage(lang) {
  const next = SUPPORTED_LANGUAGES.includes(lang) ? lang : DEFAULT_LANGUAGE;
  await loadDictionary(DEFAULT_LANGUAGE);
  await loadDictionary(next);
  currentLanguage = next;
  document.documentElement.lang = next;
  storeLanguage(next);
  return next;
}

export function getLanguage() {
  return currentLanguage;
}

// Looks up a key in the active locale, falls back to English, then to the key itself.
// Placeholders are written as {name} and filled from params.
export function t(key, params = {}) {
  const template = dictionaries.get(currentLanguage)?.[key] ?? dictionaries.get(DEFAULT_LANGUAGE)?.[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (match, name) => (Object.hasOwn(params, name) ? String(params[name]) : match));
}

// Sets a node's text from a key and remembers the key so a language switch re-translates it.
export function translate(node, key) {
  node.dataset.i18n = key;
  node.textContent = t(key);
}

// Applies data-i18n="key" (text) and data-i18n-attr="attr:key;attr:key" (attributes) below root.
// A key with no translation keeps the English text already in the markup.
export function applyStaticTranslations(root = document) {
  for (const node of root.querySelectorAll("[data-i18n]")) {
    const text = t(node.dataset.i18n);
    if (text !== node.dataset.i18n) node.textContent = text;
  }
  for (const node of root.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of node.dataset.i18nAttr.split(";")) {
      const [attribute, key] = pair.split(":").map((part) => part.trim());
      const text = key ? t(key) : key;
      if (attribute && key && text !== key) node.setAttribute(attribute, text);
    }
  }
}

// Formats a time of day in the active language; returns fallback for an invalid value.
export function formatTime(value, { seconds = false, fallback = "" } = {}) {
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) return fallback;
  const options = { hour: "2-digit", minute: "2-digit" };
  if (seconds) options.second = "2-digit";
  return parsed.toLocaleTimeString(currentLanguage, options);
}
