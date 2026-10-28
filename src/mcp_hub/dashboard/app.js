const $ = (id) => document.getElementById(id);
let selectedJobId = null;
let refreshTimer = null;

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

function renderStatus(status) {
  const health = $("health");
  health.className = "health ok";
  health.lastElementChild.textContent = "Hub đang hoạt động";

  const children = $("children");
  children.replaceChildren();
  const entries = Object.entries(status.children || {});
  if (!entries.length) children.append(Object.assign(document.createElement("span"), { className: "muted", textContent: "Chưa phát hiện MCP con" }));
  for (const [name, state] of entries) {
    const chip = document.createElement("span");
    chip.className = `chip ${state === "connected" ? "connected" : "disconnected"}`;
    chip.textContent = `${name} · ${state}`;
    children.append(chip);
  }

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

async function refresh() {
  try {
    const [status, jobData] = await Promise.all([
      api("/dashboard/api/status"),
      api("/dashboard/api/jobs?limit=100"),
    ]);
    renderStatus(status);
    renderJobs(jobData.jobs || []);
    if (selectedJobId) {
      const selected = (jobData.jobs || []).find((job) => job.job_id === selectedJobId);
      if (selected) await selectJob(selectedJobId);
    }
    const active = (jobData.jobs || []).some((job) => ["queued", "running", "awaiting_approval"].includes(job.state));
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refresh, active ? 2000 : 10000);
  } catch (error) {
    const health = $("health");
    health.className = "health bad";
    health.lastElementChild.textContent = "Không kết nối được";
    notify(error.message);
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(refresh, 10000);
  }
}

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
refresh();
