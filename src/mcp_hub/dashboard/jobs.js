// Investigation jobs: the shared job list and the selected job's detail, approval, evidence, and timeline.
import { api } from "./api.js";
import { $, badge, element, mutedNote, notify } from "./dom.js";
import { formatTime, t } from "./i18n.js";
import { requestRefresh } from "./state.js";

const CANCELLABLE_STATES = ["queued", "awaiting_input", "running", "awaiting_approval"];
const DETAIL_PAGE_LIMIT = 2000;
let selectedJobId = null;
let lastJobs = [];

function jobPath(jobId, suffix = "") {
  return `/dashboard/api/jobs/${encodeURIComponent(jobId)}${suffix}`;
}

export function selectedJob() {
  return selectedJobId;
}

function jobButton(job) {
  const button = element("button", `job ${job.job_id === selectedJobId ? "selected" : ""}`);
  button.type = "button";
  const top = element("div", "job-top");
  top.append(element("span", "job-id", job.job_id), badge(job.state));
  button.append(top, element("span", "job-summary", job.summary || t("jobs.noSummary")));
  button.addEventListener("click", () => selectJob(job.job_id));
  return button;
}

export function renderJobs(items) {
  lastJobs = items;
  const rows = items.length ? items.map(jobButton) : [mutedNote("p", t("jobs.empty"))];
  $("jobs").replaceChildren(...rows);
}

// Re-renders the cached job list and re-fetches the open job so both use the current language.
export async function rerenderJobs() {
  renderJobs(lastJobs);
  if (selectedJobId) await selectJob(selectedJobId);
}

function actionButton(className, key, onClick) {
  const button = element("button", className, t(key));
  button.addEventListener("click", onClick);
  return button;
}

async function approvalCodeControls(jobId) {
  const codeResult = await api(jobPath(jobId, "/approval-code"));
  if (!codeResult.approval_code) return [];
  const code = element("code", "approval-code", codeResult.approval_code);
  const copy = actionButton("button small secondary", "approval.copyCode", async () => {
    await navigator.clipboard.writeText(codeResult.approval_code);
    notify(t("approval.codeCopied"));
  });
  return [code, copy];
}

async function approvalBox(job) {
  const box = element("div", "approval");
  box.append(element("span", "", t("approval.pending", { tool: job.pending_approval.tool_name })));
  const preview = await api(jobPath(job.job_id, "/pending-call"));
  box.append(
    element("p", "muted", t("approval.explanation")),
    element("pre", "detail-output approval-preview", preview.arguments_preview),
  );
  box.append(...await approvalCodeControls(job.job_id));
  box.append(actionButton("button small", "approval.approve", async () => {
    await api(jobPath(job.job_id, "/approve"), { method: "POST" });
    notify(t("approval.sent"));
    await requestRefresh();
  }));
  return box;
}

function evidenceSection(job, root) {
  const list = element("div", "evidence");
  for (const item of job.evidence_refs || []) {
    const button = element("button", "button quiet small", `${item.tool} · ${item.source_id}`);
    button.addEventListener("click", () => loadDetail(job.job_id, item.source_id, root));
    list.append(button);
  }
  if (!list.childElementCount) list.append(mutedNote("span", t("detail.noEvidence")));
  return [element("div", "subheading", t("detail.evidence")), list];
}

function timelineRow(event) {
  const row = element("div", "event");
  const parts = [event.event, event.agent, event.tool, event.duration_ms == null ? null : `${event.duration_ms} ms`].filter(Boolean);
  row.append(element("time", "", formatTime(event.created_at, { seconds: true })), element("span", "", parts.join(" · ")));
  return row;
}

function timelineSection(job) {
  const timeline = element("div", "timeline");
  timeline.append(...(job.events || []).map(timelineRow));
  return [element("div", "subheading", t("detail.timeline")), timeline];
}

function cancelButton(job) {
  return actionButton("button danger small", "jobs.cancel", async () => {
    await api(jobPath(job.job_id, "/cancel"), { method: "POST" });
    notify(t("jobs.cancelSent"));
    await requestRefresh();
  });
}

async function renderJob(job) {
  const title = $("detail-title");
  delete title.dataset.i18n;
  title.textContent = t("detail.jobTitle", { id: job.job_id.slice(0, 10) });
  const root = element("div");
  root.append(badge(job.state));
  if (job.summary) root.append(element("p", "summary", job.summary));
  if (job.pending_approval) root.append(await approvalBox(job));
  root.append(...evidenceSection(job, root), ...timelineSection(job));
  if (CANCELLABLE_STATES.includes(job.state)) root.append(cancelButton(job));
  $("detail").replaceChildren(root);
}

function detailPagePath(jobId, sourceId, offset) {
  return jobPath(jobId, `/details/${encodeURIComponent(sourceId)}?offset=${offset}&limit=${DETAIL_PAGE_LIMIT}`);
}

function loadMoreButton(jobId, sourceId, result, output) {
  const next = element("button", "button quiet small", t("detail.loadMore"));
  next.addEventListener("click", async () => {
    const page = await api(detailPagePath(jobId, sourceId, result.next_offset));
    output.textContent += page.content;
    result.next_offset = page.next_offset;
    if (page.next_offset == null) next.remove();
  });
  return next;
}

async function loadDetail(jobId, sourceId, parent) {
  try {
    const result = await api(detailPagePath(jobId, sourceId, 0));
    const output = element("pre", "detail-output", result.content);
    parent.querySelector(".detail-output")?.remove();
    parent.append(output);
    if (result.next_offset != null) parent.append(loadMoreButton(jobId, sourceId, result, output));
  } catch (error) { notify(error.message); }
}

export async function selectJob(jobId) {
  selectedJobId = jobId;
  try { await renderJob(await api(jobPath(jobId))); }
  catch (error) { notify(error.message); }
}
