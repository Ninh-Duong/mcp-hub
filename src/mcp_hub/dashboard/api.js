import { t } from "./i18n.js";

// Calls a dashboard JSON endpoint with the session cookie and raises the server's error message.
export async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || t("error.requestFailed", { status: response.status }));
  }
  return response.json();
}
