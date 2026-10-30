// Investigation jobs: the shared job list and the selected job's detail, approval, evidence, and timeline.
import { api } from "./api.js";
import { $, badge, element, mutedNote, notify } from "./dom.js";
import { formatTime, t } from "./i18n.js";
import { requestRefresh } from "./state.js";

const CANCELLABLE_STATES = ["queued", "awaiting_input", "running", "awaiting_approval"];
const DETAIL_PAGE_LIMIT = 2000;
let selectedJobId = null;
let lastJobs = [];
// What the detail panel shows now; a poll that finds the same job unchanged leaves the panel alone.
let renderedFingerprint = null;
// The open evidence output (and its loaded pages) survives re-renders of the same job.
let evidenceView = null;

function jobPath(jobId, suffix = "") {
  return `/dashboard/api/jobs/${encodeURIComponent(jobId)}${suffix}`;
}

export function selectedJob() {
  return selectedJobId;
}

function jobSummaryText(job) {
  if (job.summary) return job.summary;
  return t(job.summary_unavailable ? "jobs.summaryUnavailable" : "jobs.noSummary");
}

function jobButton(job) {
  const button = element("button", `job ${job.job_id === selectedJobId ? "selected" : ""}`);
  button.type = "button";
  const top = element("div", "job-top");
  top.append(element("span", "job-id", job.job_id), badge(job.state));
  button.append(top, element("span", "job-summary", jobSummaryText(job)));
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
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

async function approvalCodeControls(jobId) {
  let codeResult;
  try {
    codeResult = await api(jobPath(jobId, "/approval-code"));
  } catch {
    return [];
  }
  if (!codeResult.approval_code) return [];
  const code = element("code", "approval-code", codeResult.approval_code);
  const copy = actionButton("button small secondary", "approval.copyCode", async () => {
    await navigator.clipboard.writeText(codeResult.approval_code);
    notify(t("approval.codeCopied"));
  });
  return [code, copy];
}

// The pending write's preview, or null when it is gone (404 after a race) or could not be read.
async function pendingPreview(jobId) {
  try {
    return await api(jobPath(jobId, "/pending-call"));
  } catch (error) {
    if (error.status !== 404) notify(error.message);
    return null;
  }
}

function previewNodes(preview) {
  const nodes = [element("pre", "detail-output approval-preview", preview.arguments_preview)];
  if (preview.truncated) nodes.push(mutedNote("p", t("approval.truncated", { total: preview.total_chars })));
  return nodes;
}

function resetApproveButton(button) {
  button.disabled = false;
  button.textContent = t("approval.approve");
}

// Asks for confirmation and stays disabled while the approval request is in flight.
function approveButton(job) {
  const button = element("button", "button small", t("approval.approve"));
  button.type = "button";
  button.addEventListener("click", async () => {
    if (!window.confirm(t("approval.confirm", { tool: job.pending_approval.tool_name }))) return;
    button.disabled = true;
    button.textContent = t("approval.sending");
    try {
      const result = await api(jobPath(job.job_id, "/approve"), { method: "POST" });
      notify(t(result.approved ? "approval.sent" : "approval.notAccepted"));
      if (!result.approved) resetApproveButton(button);
      await requestRefresh();
    } catch (error) {
      notify(error.message);
      resetApproveButton(button);
    }
  });
  return button;
}

async function approvalBox(job) {
  const box = element("div", "approval");
  box.append(element("span", "", t("approval.pending", { tool: job.pending_approval.tool_name })));
  const preview = await pendingPreview(job.job_id);
  if (!preview) {
    box.append(mutedNote("p", t("approval.noPendingWrite")));
    return box;
  }
  box.append(element("p", "muted", t("approval.explanation")), ...previewNodes(preview));
  box.append(...await approvalCodeControls(job.job_id), approveButton(job));
  return box;
}

function evidenceViewFor(jobId) {
  if (evidenceView?.jobId !== jobId) evidenceView = { jobId, node: element("div", "evidence-view") };
  return evidenceView.node;
}

function evidenceSection(job) {
  const list = element("div", "evidence");
  const view = evidenceViewFor(job.job_id);
  for (const item of job.evidence_refs || []) {
    const button = element("button", "button quiet small", `${item.tool} · ${item.source_id}`);
    button.type = "button";
    button.addEventListener("click", () => loadDetail(job.job_id, item.source_id, view));
    list.append(button);
  }
  if (!list.childElementCount) list.append(mutedNote("span", t("detail.noEvidence")));
  return [element("div", "subheading", t("detail.evidence")), list, view];
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
  if (job.summary || job.summary_unavailable) root.append(element("p", "summary", jobSummaryText(job)));
  if (job.pending_approval) root.append(await approvalBox(job));
  root.append(...evidenceSection(job), ...timelineSection(job));
  if (CANCELLABLE_STATES.includes(job.state)) root.append(cancelButton(job));
  $("detail").replaceChildren(root);
}

function detailPagePath(jobId, sourceId, offset) {
  return jobPath(jobId, `/details/${encodeURIComponent(sourceId)}?offset=${offset}&limit=${DETAIL_PAGE_LIMIT}`);
}

function loadMoreButton(jobId, sourceId, result, output) {
  const next = element("button", "button quiet small", t("detail.loadMore"));
  next.type = "button";
  next.addEventListener("click", async () => {
    try {
      const page = await api(detailPagePath(jobId, sourceId, result.next_offset));
      output.textContent += page.content;
      result.next_offset = page.next_offset;
      if (page.next_offset == null) next.remove();
    } catch (error) { notify(error.message); }
  });
  return next;
}

async function loadDetail(jobId, sourceId, view) {
  try {
    const result = await api(detailPagePath(jobId, sourceId, 0));
    const output = element("pre", "detail-output", result.content);
    view.replaceChildren(output);
    if (result.next_offset != null) view.append(loadMoreButton(jobId, sourceId, result, output));
  } catch (error) { notify(error.message); }
}

function jobFingerprint(job) {
  return JSON.stringify([
    job.job_id, job.state, job.updated_at, job.summary, job.summary_unavailable,
    (job.events || []).length, (job.evidence_refs || []).length, job.pending_approval?.tool_name ?? null,
  ]);
}

// `force: false` (the poll) skips the render when the job has not changed since it was shown.
export async function selectJob(jobId, { force = true } = {}) {
  selectedJobId = jobId;
  try {
    const job = await api(jobPath(jobId));
    const fingerprint = jobFingerprint(job);
    if (!force && fingerprint === renderedFingerprint) return;
    renderedFingerprint = fingerprint;
    await renderJob(job);
  } catch (error) { notify(error.message); }
}
