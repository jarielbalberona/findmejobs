const DATA_BASE = "/var/ui-data";
const REFRESH_MS = 60_000;

const state = {
  data: null,
  jobs: [],
  selectedJobId: null,
  selectedApplyJobId: null,
};

const els = {
  errorBox: document.getElementById("errorBox"),
  generatedAt: document.getElementById("generatedAt"),
  overviewCards: document.getElementById("overviewCards"),
  pipelineRuns: document.getElementById("pipelineRuns"),
  profileGrid: document.getElementById("profileGrid"),
  settingsGrid: document.getElementById("settingsGrid"),
  rankingPolicy: document.getElementById("rankingPolicy"),
  weightsTable: document.getElementById("weightsTable"),
  titleFamilies: document.getElementById("titleFamilies"),
  sourcesTable: document.getElementById("sourcesTable"),
  jobsTable: document.getElementById("jobsTable"),
  jobsMeta: document.getElementById("jobsMeta"),
  refreshBtn: document.getElementById("refreshBtn"),
  applicationsCards: document.getElementById("applicationsCards"),
  applicationsTable: document.getElementById("applicationsTable"),
  applySessionsCards: document.getElementById("applySessionsCards"),
  applySessionsTable: document.getElementById("applySessionsTable"),
  applySessionMeta: document.getElementById("applySessionMeta"),
  applySessionLinks: document.getElementById("applySessionLinks"),
  applySessionGrid: document.getElementById("applySessionGrid"),
  applyFilledFieldsTable: document.getElementById("applyFilledFieldsTable"),
  applyUnresolvedFieldsTable: document.getElementById("applyUnresolvedFieldsTable"),
  applyApprovalsTable: document.getElementById("applyApprovalsTable"),
  applyReportText: document.getElementById("applyReportText"),
  copyAllApplyCommandsBtn: document.getElementById("copyAllApplyCommandsBtn"),
  copyApplyCommandsStatus: document.getElementById("copyApplyCommandsStatus"),
  applyCommandsList: document.getElementById("applyCommandsList"),
  jobDetailMeta: document.getElementById("jobDetailMeta"),
  jobDetailLinks: document.getElementById("jobDetailLinks"),
  jobDetailGrid: document.getElementById("jobDetailGrid"),
  coverLetterText: document.getElementById("coverLetterText"),
  answersText: document.getElementById("answersText"),
  draftReportText: document.getElementById("draftReportText"),
  jobDescriptionHtml: document.getElementById("jobDescriptionHtml"),
  copyCoverLetterBtn: document.getElementById("copyCoverLetterBtn"),
  copyCoverLetterStatus: document.getElementById("copyCoverLetterStatus"),
  copyAllCommandsBtn: document.getElementById("copyAllCommandsBtn"),
  copyCommandsStatus: document.getElementById("copyCommandsStatus"),
  jobCommandsList: document.getElementById("jobCommandsList"),
  jobSearch: document.getElementById("jobSearch"),
  statusFilter: document.getElementById("statusFilter"),
  sourceFilter: document.getElementById("sourceFilter"),
  minScore: document.getElementById("minScore"),
  sortBy: document.getElementById("sortBy"),
};

function showError(msg) {
  els.errorBox.textContent = msg;
  els.errorBox.classList.remove("hidden");
}

function clearError() {
  els.errorBox.textContent = "";
  els.errorBox.classList.add("hidden");
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path} returned HTTP ${res.status}`);
  }
  return res.json();
}

async function fetchText(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    return null;
  }
  return (await res.text()).trim();
}

async function fetchOptionalJson(path, fallback) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) {
    return fallback;
  }
  return res.json();
}

function card(label, value) {
  return `<div class="card"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(String(value))}</div></div>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function sanitizeHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const blocked = new Set(["script", "style", "iframe", "object", "embed", "link", "meta"]);
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT);
  const toRemove = [];
  while (walker.nextNode()) {
    const el = walker.currentNode;
    const tag = el.tagName.toLowerCase();
    if (blocked.has(tag)) {
      toRemove.push(el);
      continue;
    }
    for (const attr of [...el.attributes]) {
      const name = attr.name.toLowerCase();
      const value = attr.value || "";
      if (name.startsWith("on")) {
        el.removeAttribute(attr.name);
        continue;
      }
      if ((name === "href" || name === "src") && value.trim().toLowerCase().startsWith("javascript:")) {
        el.removeAttribute(attr.name);
      }
    }
  }
  for (const node of toRemove) node.remove();
  return template.innerHTML;
}

function renderDescriptionHtml(raw) {
  const text = raw || "";
  if (!text.trim()) {
    return `<p class="muted">No job description available.</p>`;
  }
  const hasHtml = /<\s*[a-z][\s\S]*>/i.test(text);
  if (hasHtml) {
    return sanitizeHtml(text);
  }
  // Plain text fallback: keep paragraphs readable.
  return text
    .split(/\n{2,}/)
    .map((part) => `<p>${escapeHtml(part).replaceAll("\n", "<br>")}</p>`)
    .join("");
}

function renderPlainBlock(el, text, emptyMessage) {
  const value = text || "";
  if (!value.trim()) {
    el.innerHTML = `<span class="muted">${escapeHtml(emptyMessage)}</span>`;
    return;
  }
  el.textContent = value;
}

function formatValue(v) {
  if (v === null || v === undefined) return "-";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "-";
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function renderKvCard(title, object, keys) {
  const rows = keys
    .map((k) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(formatValue(object?.[k]))}</dd>`)
    .join("");
  return `<article class="kv"><h3>${escapeHtml(title)}</h3><dl>${rows}</dl></article>`;
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((el) => el.classList.remove("active"));
  document.querySelectorAll(".panel").forEach((el) => el.classList.remove("active"));
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  const panel = document.querySelector(`.panel[data-panel="${name}"]`);
  if (tab) tab.classList.add("active");
  if (panel) panel.classList.add("active");
}

function jobsById() {
  return new Map((state.jobs || []).map((job) => [job.job_id, job]));
}

function applicationByJobId() {
  const apps = state.data?.application?.applications || [];
  return new Map(apps.map((app) => [app.job_id, app]));
}

function jobDetailsById() {
  const jobs = state.data?.jobDetails?.jobs || {};
  return new Map(Object.entries(jobs));
}

function applySessionsByJobId() {
  const rows = state.data?.applySessions?.sessions || [];
  return new Map(rows.map((row) => [row.job_id, row]));
}

function configProfile(data) {
  return data?.config?.summary?.profile || data?.config?.profile || {};
}

function configApp(data) {
  return data?.config?.summary?.app || data?.config?.app || {};
}

function configPaths(data) {
  return data?.config?.artifacts?.paths || data?.config?.paths || {};
}

function rankingSummary(data) {
  return data?.ranking?.summary || data?.ranking || {};
}

function sourcesSummary(data) {
  return data?.sources?.summary || data?.sources || {};
}

function jobsSummary(data) {
  return data?.jobs?.summary || data?.jobs || {};
}

function applySessionsSummary(data) {
  return data?.applySessions || {};
}

function renderOverview(data) {
  const ranking = data.report?.ranking || {};
  const delivery = data.report?.delivery || {};
  const runs = data.report?.pipeline_runs || [];
  const appTotals = data.application?.totals || {};

  const latestRun = runs[0];
  els.overviewCards.innerHTML = [
    card("Sources (runtime)", data.report?.sources?.length || 0),
    card("Jobs ranked", ranking.ranked ?? 0),
    card("Jobs filtered", ranking.filtered ?? 0),
    card("Latest digest", delivery.latest_digest_status || "none"),
    card("Applications", appTotals.applications ?? 0),
    card("Latest run", latestRun ? `${latestRun.command}:${latestRun.status}` : "none"),
  ].join("");

  if (!runs.length) {
    els.pipelineRuns.innerHTML = `<p class="muted">No pipeline runs found.</p>`;
    return;
  }

  const rows = runs
    .map(
      (run) => `
      <tr>
        <td>${escapeHtml(run.command)}</td>
        <td><span class="status-chip status-${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></td>
        <td>${escapeHtml(run.started_at || "-")}</td>
        <td>${escapeHtml(run.finished_at || "-")}</td>
        <td>${escapeHtml(JSON.stringify(run.stats || {}))}</td>
      </tr>`,
    )
    .join("");

  els.pipelineRuns.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Command</th><th>Status</th><th>Started</th><th>Finished</th><th>Stats</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderProfileAndSettings(data) {
  const profile = configProfile(data);
  const app = configApp(data);

  els.profileGrid.innerHTML = [
    renderKvCard("Identity", profile, ["full_name", "headline", "email", "phone", "location_text", "years_experience"]),
    renderKvCard("Targets", profile, ["target_titles", "required_skills", "preferred_skills", "allowed_countries"]),
    renderKvCard("Career", profile, ["summary", "strengths", "recent_titles", "recent_companies"]),
    renderKvCard("Application", profile.application || {}, ["remote_preference", "relocation_preference", "salary_expectation", "notice_period", "current_availability"]),
  ].join("");

  els.settingsGrid.innerHTML = [
    renderKvCard("Config Paths", configPaths(data), ["app_config_path", "profile_path", "ranking_path", "sources_path"]),
    renderKvCard("HTTP", app.http || {}, ["timeout_seconds", "max_attempts", "user_agent"]),
    renderKvCard("Delivery", app.delivery || {}, ["channel", "daily_hour", "digest_max_items"]),
    renderKvCard("Email (non-secret)", app.delivery?.email || {}, ["enabled", "host", "port", "username", "use_tls", "sender", "recipient"]),
  ].join("");
}

function renderRanking(data) {
  const summary = rankingSummary(data);
  const policy = summary?.ranking_policy || {};
  const reviewGate = summary?.review_eligibility || {};

  els.rankingPolicy.innerHTML = [
    renderKvCard("Policy", policy, ["stale_days", "minimum_score", "minimum_salary", "require_remote", "remote_first"]),
    renderKvCard("Review Eligibility", reviewGate, ["minimum_score", "must_pass_hard_filters", "note"]),
    renderKvCard("Preference Lists", policy, ["blocked_companies", "blocked_title_keywords", "allowed_companies", "preferred_companies", "preferred_timezones"]),
    renderKvCard("Scoring Inputs", summary?.profile_fields_for_scoring || {}, ["target_titles", "required_skills", "preferred_skills", "preferred_locations"]),
  ].join("");

  const weights = policy.weights || {};
  const weightRows = Object.entries(weights)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([name, weight]) => `<tr><td>${escapeHtml(name)}</td><td>${escapeHtml(String(weight))}</td></tr>`)
    .join("");

  els.weightsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Component</th><th>Weight</th></tr></thead>
        <tbody>${weightRows || '<tr><td colspan="2">No weights</td></tr>'}</tbody>
      </table>
    </div>`;

  const families = policy.title_families || {};
  const familyRows = Object.entries(families)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([family, patterns]) => `<tr><td>${escapeHtml(family)}</td><td>${escapeHtml((patterns || []).join(", "))}</td></tr>`)
    .join("");

  els.titleFamilies.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Family</th><th>Patterns</th></tr></thead>
        <tbody>${familyRows || '<tr><td colspan="2">No title families</td></tr>'}</tbody>
      </table>
    </div>`;
}

function renderSources(data) {
  const reportSources = data.report?.sources || [];
  const configSources = (sourcesSummary(data)?.sources || []).map((s) => s.name);

  const rows = reportSources
    .map((s) => {
      const inConfig = configSources.includes(s.name);
      return `
      <tr>
        <td>${escapeHtml(s.name)}</td>
        <td>${escapeHtml(s.kind)}</td>
        <td>${escapeHtml(s.family || "-")}</td>
        <td>${escapeHtml(String(s.enabled))}</td>
        <td>${escapeHtml(String(s.priority))}</td>
        <td>${escapeHtml(String(s.trust_weight))}</td>
        <td>${escapeHtml(s.latest_status || "-")}</td>
        <td>${escapeHtml(String(s.raw_seen ?? 0))}</td>
        <td>${escapeHtml(String(s.inserted ?? 0))}</td>
        <td>${escapeHtml(String(s.updated ?? 0))}</td>
        <td>${escapeHtml(String(s.failed ?? 0))}</td>
        <td>${escapeHtml(String(s.parse_errors ?? 0))}</td>
        <td>${inConfig ? "yes" : "no"}</td>
      </tr>`;
    })
    .join("");

  els.sourcesTable.innerHTML = `
    <p class="muted">Configured source entries in sources.yaml: ${escapeHtml(String(configSources.length))}</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th><th>Kind</th><th>Family</th><th>Enabled</th><th>Priority</th><th>Trust</th>
            <th>Latest</th><th>Raw Seen</th><th>Inserted</th><th>Updated</th><th>Failed</th><th>Parse Errors</th><th>In YAML</th>
          </tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="13">No runtime sources found.</td></tr>'}</tbody>
      </table>
    </div>`;

  populateSourceFilter(reportSources.map((s) => s.name));
}

function populateSourceFilter(sourceNames) {
  const unique = [...new Set(sourceNames)].sort((a, b) => a.localeCompare(b));
  const options = ['<option value="all">All sources</option>']
    .concat(unique.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`))
    .join("");
  els.sourceFilter.innerHTML = options;
}

function renderJobs(data) {
  state.jobs = jobsSummary(data)?.jobs || [];
  applyJobFilters();
}

function renderApplications(data) {
  const totals = data.application?.totals || {};
  const rows = data.application?.applications || [];

  els.applicationsCards.innerHTML = [
    card("Tracked jobs", totals.applications ?? 0),
    card("Prepared packets", totals.prepared ?? 0),
    card("Cover letters ready", totals.cover_letters_ready ?? 0),
    card("Answers ready", totals.answers_ready ?? 0),
    card("Awaiting OpenClaw", totals.awaiting_openclaw_results ?? 0),
  ].join("");

  const body = rows
    .map(
      (app) => `
      <tr>
        <td><button class="linkish js-view-job" data-job-id="${escapeHtml(app.job_id || "")}" type="button">${escapeHtml(app.job_id || "-")}</button></td>
        <td>${escapeHtml(app.company_name || "-")}</td>
        <td>${escapeHtml(app.role_title || "-")}</td>
        <td>${escapeHtml(app.source_name || "-")}</td>
        <td>${escapeHtml(String(app.prepared))}</td>
        <td>${escapeHtml(String(app.questions_count ?? 0))}</td>
        <td>${escapeHtml(String(app.missing_inputs_count ?? 0))}</td>
        <td>${escapeHtml(String(app.cover_letter?.ready ?? false))} (${escapeHtml(app.cover_letter?.origin || "-")})</td>
        <td>${escapeHtml(String(app.answers?.ready ?? false))} (${escapeHtml(app.answers?.origin || "-")})</td>
        <td>${escapeHtml(app.openclaw?.status || "-")}</td>
        <td>${escapeHtml(app.updated_at || "-")}</td>
      </tr>`,
    )
    .join("");

  els.applicationsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Job ID</th><th>Company</th><th>Role</th><th>Source</th><th>Prepared</th><th>Questions</th>
            <th>Missing Inputs</th><th>Cover Letter</th><th>Answers</th><th>OpenClaw</th><th>Updated (UTC)</th>
          </tr>
        </thead>
        <tbody>${body || '<tr><td colspan="11">No application state found under state/applications yet.</td></tr>'}</tbody>
      </table>
    </div>`;
}

function buildApplicationCommands(jobId) {
  const quotedJob = JSON.stringify(jobId);
  return [
    `findmejobs prepare-application --job-id ${quotedJob}`,
    `findmejobs draft-cover-letter --job-id ${quotedJob}`,
    `findmejobs draft-answers --job-id ${quotedJob}`,
    `findmejobs show-application --job-id ${quotedJob}`,
    `findmejobs validate-application --job-id ${quotedJob}`,
    `findmejobs regenerate-application --job-id ${quotedJob}`,
  ];
}

function buildApplyCommands(jobId, actionId) {
  const quotedJob = JSON.stringify(jobId);
  const base = [
    `findmejobs apply prepare --job-id ${quotedJob}`,
    `findmejobs apply open --job-id ${quotedJob} --mode assisted`,
    `findmejobs apply status --job-id ${quotedJob}`,
    `findmejobs apply resume --job-id ${quotedJob}`,
  ];
  if (actionId) {
    base.push(`findmejobs apply approve --job-id ${quotedJob} --action ${JSON.stringify(actionId)}`);
  }
  base.push(`findmejobs apply report --job-id ${quotedJob}`);
  return base;
}

function renderCommandList(jobId) {
  const commands = buildApplicationCommands(jobId);
  const rows = commands
    .map(
      (cmd, idx) =>
        `<div class="cmd-row"><code>${escapeHtml(cmd)}</code><button type="button" class="js-copy-cmd" data-cmd-idx="${idx}">Copy</button></div>`,
    )
    .join("");
  els.jobCommandsList.innerHTML = `<div class="cmd-list">${rows}</div>`;
  els.jobCommandsList.setAttribute("data-commands", JSON.stringify(commands));
  els.copyCommandsStatus.textContent = "";
}

function renderApplyCommandList(jobId, actionId) {
  const commands = buildApplyCommands(jobId, actionId);
  const rows = commands
    .map(
      (cmd, idx) =>
        `<div class="cmd-row"><code>${escapeHtml(cmd)}</code><button type="button" class="js-copy-apply-cmd" data-cmd-idx="${idx}">Copy</button></div>`,
    )
    .join("");
  els.applyCommandsList.innerHTML = `<div class="cmd-list">${rows}</div>`;
  els.applyCommandsList.setAttribute("data-commands", JSON.stringify(commands));
  els.copyApplyCommandsStatus.textContent = "";
}

function renderApplySessions(data) {
  const summary = applySessionsSummary(data);
  const totals = summary?.totals || {};
  const rows = summary?.sessions || [];

  els.applySessionsCards.innerHTML = [
    card("Sessions", totals.sessions ?? 0),
    card("Awaiting approval", totals.awaiting_approval ?? 0),
    card("Ready to resume", totals.ready_to_resume ?? 0),
    card("Manual submit", totals.awaiting_manual_submit ?? 0),
    card("With unresolved fields", totals.with_unresolved_fields ?? 0),
  ].join("");

  const body = rows
    .map(
      (row) => `
      <tr>
        <td><button class="linkish js-view-apply-session" data-job-id="${escapeHtml(row.job_id || "")}" type="button">${escapeHtml(row.job_id || "-")}</button></td>
        <td>${escapeHtml(row.company_name || "-")}</td>
        <td>${escapeHtml(row.role_title || "-")}</td>
        <td>${escapeHtml(row.mode || "-")}</td>
        <td><span class="status-chip status-${escapeHtml(row.status || "opened")}">${escapeHtml(row.status || "-")}</span></td>
        <td>${escapeHtml(String(row.parse_confidence ?? "-"))}</td>
        <td>${escapeHtml(String(row.pending_approvals ?? 0))}</td>
        <td>${escapeHtml(String(row.unresolved_fields_count ?? 0))}</td>
        <td>${escapeHtml(String(row.filled_fields_count ?? 0))}</td>
        <td>${escapeHtml(row.current_step || "-")}</td>
        <td>${escapeHtml(row.updated_at || "-")}</td>
      </tr>`,
    )
    .join("");

  els.applySessionsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Job ID</th><th>Company</th><th>Role</th><th>Mode</th><th>Status</th><th>Parse</th>
            <th>Pending Approvals</th><th>Unresolved</th><th>Filled</th><th>Current Step</th><th>Updated (UTC)</th>
          </tr>
        </thead>
        <tbody>${body || '<tr><td colspan="11">No apply session state found under state/apply_sessions yet.</td></tr>'}</tbody>
      </table>
    </div>`;

  if (state.selectedApplyJobId && applySessionsByJobId().has(state.selectedApplyJobId)) {
    renderApplySessionDetail(state.selectedApplyJobId);
  }
}

function renderApplySessionDetail(jobId) {
  state.selectedApplyJobId = jobId;
  const session = applySessionsByJobId().get(jobId);
  if (!session) {
    els.applySessionMeta.textContent = `No apply session found for job_id=${jobId}`;
    els.applySessionLinks.innerHTML = "";
    els.applySessionGrid.innerHTML = "";
    els.applyFilledFieldsTable.innerHTML = "";
    els.applyUnresolvedFieldsTable.innerHTML = "";
    els.applyApprovalsTable.innerHTML = "";
    renderPlainBlock(els.applyReportText, "", "No apply report yet.");
    els.applyCommandsList.innerHTML = "";
    els.applyCommandsList.removeAttribute("data-commands");
    return;
  }

  els.applySessionMeta.textContent = `${jobId} | status=${session.status || "-"} | mode=${session.mode || "-"}`;

  const links = [];
  if (session.apply_url) {
    links.push(`<a href="${escapeHtml(session.apply_url)}" target="_blank" rel="noreferrer">Open Apply URL</a>`);
  }
  if (session.current_page_url) {
    links.push(`<a href="${escapeHtml(session.current_page_url)}" target="_blank" rel="noreferrer">Open Current Page</a>`);
  }
  els.applySessionLinks.innerHTML = links.join(" | ");

  const summaryLeft = {
    company: session.company_name || "-",
    role: session.role_title || "-",
    mode: session.mode || "-",
    status: session.status || "-",
    parse_confidence: session.parse_confidence ?? "-",
    submit_available: session.submit_available ?? false,
    manual_submit_required: session.manual_submit_required ?? true,
    current_step: session.current_step || "-",
    candidate_inputs_count: session.candidate_inputs_count ?? 0,
  };
  const summaryRight = {
    pending_approvals: session.pending_approvals ?? 0,
    approved_approvals: session.approved_approvals ?? 0,
    unresolved_fields_count: session.unresolved_fields_count ?? 0,
    filled_fields_count: session.filled_fields_count ?? 0,
    approved_action_ids: session.approved_action_ids || [],
    pending_action_ids: session.pending_action_ids || [],
    updated_at: session.updated_at || "-",
  };

  els.applySessionGrid.innerHTML = [
    renderKvCard("Session", summaryLeft, Object.keys(summaryLeft)),
    renderKvCard("Progress", summaryRight, Object.keys(summaryRight)),
  ].join("");

  const filledRows = (session.filled_fields || [])
    .map(
      (item) => `
      <tr>
        <td>${escapeHtml(item.field_key || "-")}</td>
        <td>${escapeHtml(item.label || "-")}</td>
        <td>${escapeHtml(item.action_type || "-")}</td>
        <td>${escapeHtml(item.status || "-")}</td>
        <td>${escapeHtml(item.source || "-")}</td>
        <td>${escapeHtml(item.page || "-")}</td>
      </tr>`,
    )
    .join("");
  els.applyFilledFieldsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Field Key</th><th>Label</th><th>Action</th><th>Status</th><th>Source</th><th>Page</th></tr></thead>
        <tbody>${filledRows || '<tr><td colspan="6">No filled fields recorded.</td></tr>'}</tbody>
      </table>
    </div>`;

  const unresolvedRows = (session.unresolved_fields || [])
    .map(
      (item) => `
      <tr>
        <td>${escapeHtml(item.field_key || "-")}</td>
        <td>${escapeHtml(item.label || "-")}</td>
        <td>${escapeHtml(item.reason_code || "-")}</td>
        <td>${escapeHtml(item.message || "-")}</td>
        <td>${escapeHtml(String(item.required ?? false))}</td>
        <td>${escapeHtml(item.page || "-")}</td>
      </tr>`,
    )
    .join("");
  els.applyUnresolvedFieldsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Field Key</th><th>Label</th><th>Reason</th><th>Message</th><th>Required</th><th>Page</th></tr></thead>
        <tbody>${unresolvedRows || '<tr><td colspan="6">No unresolved fields.</td></tr>'}</tbody>
      </table>
    </div>`;

  const approvalsRows = (session.approvals_required || [])
    .map(
      (item) => `
      <tr>
        <td>${escapeHtml(item.action_id || "-")}</td>
        <td>${escapeHtml(item.gate_type || "-")}</td>
        <td>${escapeHtml(item.status || "-")}</td>
        <td>${escapeHtml(item.title || "-")}</td>
        <td>${escapeHtml(item.reason || "-")}</td>
      </tr>`,
    )
    .join("");
  els.applyApprovalsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Action ID</th><th>Gate Type</th><th>Status</th><th>Title</th><th>Reason</th></tr></thead>
        <tbody>${approvalsRows || '<tr><td colspan="5">No approval gates.</td></tr>'}</tbody>
      </table>
    </div>`;

  renderPlainBlock(els.applyReportText, session.report_markdown || "", "No apply report yet.");
  const firstPendingAction = (session.approvals_required || []).find((item) => item.status === "pending")?.action_id;
  renderApplyCommandList(jobId, firstPendingAction || null);
}

function renderJobDetail(jobId) {
  state.selectedJobId = jobId;
  const job = jobsById().get(jobId);
  const app = applicationByJobId().get(jobId);
  const detail = jobDetailsById().get(jobId);

  if (!job && !app) {
    els.jobDetailMeta.textContent = `No detail found for job_id=${jobId}`;
    els.jobDetailLinks.innerHTML = "";
    els.jobDetailGrid.innerHTML = "";
    renderPlainBlock(els.coverLetterText, "", "No cover letter draft yet.");
    renderPlainBlock(els.answersText, "", "No answers draft yet.");
    renderPlainBlock(els.draftReportText, "", "No draft report yet.");
    els.jobDescriptionHtml.innerHTML = `<p class="muted">No job description available.</p>`;
    els.jobCommandsList.innerHTML = "";
    els.jobCommandsList.removeAttribute("data-commands");
    return;
  }

  const bits = [jobId];
  if (job?.status) bits.push(`status=${job.status}`);
  if (job?.score !== undefined) bits.push(`score=${job.score}`);
  els.jobDetailMeta.textContent = bits.join(" | ");

  const summaryLeft = {
    title: detail?.title || job?.title || app?.role_title || "-",
    company: detail?.company_name || job?.company_name || app?.company_name || "-",
    source: detail?.source_name || job?.source || app?.source_name || "-",
    location: detail?.location_text || job?.location_text || app?.packet_summary?.location_text || "-",
    employment_type: detail?.employment_type || "-",
    seniority: detail?.seniority || "-",
    status: job?.status || "-",
    score: job?.score ?? app?.packet_summary?.score_total ?? detail?.score_total ?? "-",
    posted_at: detail?.posted_at || "-",
    salary_min: detail?.salary_min ?? "-",
    salary_max: detail?.salary_max ?? "-",
    salary_currency: detail?.salary_currency || "-",
    tags: detail?.tags || job?.tags || [],
    matched_signals: job?.matched_signals || app?.packet_summary?.matched_signals || [],
  };

  const summaryRight = {
    prepared: app?.prepared ?? false,
    questions_count: app?.questions_count ?? app?.packet_summary?.application_questions?.length ?? 0,
    missing_inputs_count: app?.missing_inputs_count ?? 0,
    openclaw_status: app?.openclaw?.status ?? "-",
    cover_letter_ready: app?.cover_letter?.ready ?? false,
    answers_ready: app?.answers?.ready ?? false,
  };

  const canonicalUrl = detail?.canonical_url || job?.canonical_url || app?.packet_summary?.canonical_url || "";
  const sourceUrl = app?.paths?.packet_path || "";
  const linkParts = [];
  if (canonicalUrl) {
    linkParts.push(
      `<a href="${escapeHtml(canonicalUrl)}" target="_blank" rel="noreferrer">Open Canonical URL</a>`,
    );
  }
  if (sourceUrl) {
    linkParts.push(`<span class="muted">packet: ${escapeHtml(sourceUrl)}</span>`);
  }
  els.jobDetailLinks.innerHTML = linkParts.join(" | ");

  els.jobDetailGrid.innerHTML = [
    renderKvCard("Job", summaryLeft, Object.keys(summaryLeft)),
    renderKvCard("Application Helper", summaryRight, Object.keys(summaryRight)),
  ].join("");

  renderPlainBlock(els.coverLetterText, app?.cover_letter?.text || "", "No cover letter draft yet.");
  renderPlainBlock(els.answersText, app?.answers?.text || "", "No answers draft yet.");
  renderPlainBlock(
    els.draftReportText,
    app?.draft_report_text || app?.missing_inputs_text || "",
    "No draft report yet.",
  );
  const description = detail?.description_text || app?.packet_summary?.description_excerpt || job?.description_snippet || "";
  els.jobDescriptionHtml.innerHTML = renderDescriptionHtml(description);
  renderCommandList(jobId);
}

function applyJobFilters() {
  const q = els.jobSearch.value.trim().toLowerCase();
  const status = els.statusFilter.value;
  const source = els.sourceFilter.value;
  const minScoreRaw = els.minScore.value.trim();
  const minScore = minScoreRaw ? Number(minScoreRaw) : null;
  const sortBy = els.sortBy.value;

  let jobs = [...state.jobs];

  jobs = jobs.filter((job) => {
    if (status !== "all" && job.status !== status) return false;
    if (source !== "all" && job.source !== source) return false;
    if (minScore !== null && Number(job.score) < minScore) return false;
    if (q) {
      const hay = `${job.title || ""} ${job.company_name || ""} ${job.location_text || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  jobs.sort((a, b) => {
    if (sortBy === "score_asc") return Number(a.score) - Number(b.score);
    if (sortBy === "title_asc") return (a.title || "").localeCompare(b.title || "");
    if (sortBy === "company_asc") return (a.company_name || "").localeCompare(b.company_name || "");
    return Number(b.score) - Number(a.score);
  });

  els.jobsMeta.textContent = `Showing ${jobs.length} of ${state.jobs.length} rows`;

  const rows = jobs
    .map(
      (job) => `
      <tr>
        <td>${escapeHtml(job.title || "-")}</td>
        <td>${escapeHtml(job.company_name || "-")}</td>
        <td>${escapeHtml(job.location_text || "-")}</td>
        <td>${escapeHtml(String(job.score ?? "-"))}</td>
        <td><span class="status-chip status-${escapeHtml(job.status || "hard_filtered")}">${escapeHtml(job.status || "-")}</span></td>
        <td>${escapeHtml(job.source || "-")}</td>
        <td>${escapeHtml((job.matched_signals || []).join(", ") || "-")}</td>
        <td>${escapeHtml((job.tags || []).join(", ") || "-")}</td>
        <td>
          <button class="linkish js-view-job" data-job-id="${escapeHtml(job.job_id)}" type="button">details</button>
          ${job.canonical_url ? ` | <a href="${escapeHtml(job.canonical_url)}" target="_blank" rel="noreferrer">open</a>` : ""}
        </td>
      </tr>`,
    )
    .join("");

  els.jobsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Title</th><th>Company</th><th>Location</th><th>Score</th><th>Status</th><th>Source</th>
            <th>Signals</th><th>Tags</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="9">No jobs match current filters.</td></tr>'}</tbody>
      </table>
    </div>`;
}

async function loadAll() {
  clearError();
  try {
    const [config, ranking, sources, jobs, report, application, applySessions, jobDetails, generatedAt] = await Promise.all([
      fetchJson(`${DATA_BASE}/config.json`),
      fetchJson(`${DATA_BASE}/ranking.json`),
      fetchJson(`${DATA_BASE}/sources.json`),
      fetchJson(`${DATA_BASE}/jobs.json`),
      fetchJson(`${DATA_BASE}/report.json`),
      fetchJson(`${DATA_BASE}/application.json`),
      fetchOptionalJson(`${DATA_BASE}/apply_sessions.json`, { totals: {}, sessions: [], warnings: ["apply_sessions_not_exported"] }),
      fetchJson(`${DATA_BASE}/job_details.json`),
      fetchText(`${DATA_BASE}/generated_at.txt`),
    ]);

    state.data = { config, ranking, sources, jobs, report, application, applySessions, jobDetails, generatedAt };
    els.generatedAt.textContent = generatedAt ? `Snapshot: ${generatedAt}` : "Snapshot loaded";
    if (report?.status === "error") {
      showError(`report snapshot warning: ${report.message || "report command failed during export"}`);
    }

    renderOverview(state.data);
    renderProfileAndSettings(state.data);
    renderRanking(state.data);
    renderSources(state.data);
    renderJobs(state.data);
    renderApplications(state.data);
    renderApplySessions(state.data);
    if (state.selectedJobId) {
      renderJobDetail(state.selectedJobId);
    }
  } catch (err) {
    showError(
      `Failed to load snapshot data from ${DATA_BASE}. Run scripts/export_ui_data.sh and serve this repo root (not file://). Error: ${err.message}`,
    );
  }
}

function bindTabs() {
  document.getElementById("tabs").addEventListener("click", (evt) => {
    const btn = evt.target.closest("button[data-tab]");
    if (!btn) return;
    switchTab(btn.dataset.tab);
  });
}

function bindFilters() {
  [els.jobSearch, els.statusFilter, els.sourceFilter, els.minScore, els.sortBy].forEach((el) => {
    el.addEventListener("input", applyJobFilters);
    el.addEventListener("change", applyJobFilters);
  });

  els.refreshBtn.addEventListener("click", loadAll);

  document.body.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".js-view-job");
    if (!btn) return;
    const jobId = btn.getAttribute("data-job-id");
    if (!jobId) return;
    renderJobDetail(jobId);
    switchTab("job-detail");
  });

  document.body.addEventListener("click", (evt) => {
    const btn = evt.target.closest(".js-view-apply-session");
    if (!btn) return;
    const jobId = btn.getAttribute("data-job-id");
    if (!jobId) return;
    renderApplySessionDetail(jobId);
    switchTab("apply-sessions");
  });

  document.body.addEventListener("click", async (evt) => {
    const btn = evt.target.closest(".js-copy-cmd");
    if (!btn) return;
    const raw = els.jobCommandsList.getAttribute("data-commands");
    if (!raw) return;
    const cmds = JSON.parse(raw);
    const idx = Number(btn.getAttribute("data-cmd-idx"));
    const value = cmds[idx] || "";
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      els.copyCommandsStatus.textContent = "Command copied.";
    } catch (_err) {
      els.copyCommandsStatus.textContent = "Copy failed.";
    }
  });

  document.body.addEventListener("click", async (evt) => {
    const btn = evt.target.closest(".js-copy-apply-cmd");
    if (!btn) return;
    const raw = els.applyCommandsList.getAttribute("data-commands");
    if (!raw) return;
    const cmds = JSON.parse(raw);
    const idx = Number(btn.getAttribute("data-cmd-idx"));
    const value = cmds[idx] || "";
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      els.copyApplyCommandsStatus.textContent = "Command copied.";
    } catch (_err) {
      els.copyApplyCommandsStatus.textContent = "Copy failed.";
    }
  });

  els.copyCoverLetterBtn.addEventListener("click", async () => {
    const text = els.coverLetterText.textContent || "";
    if (!text.trim()) {
      els.copyCoverLetterStatus.textContent = "No cover letter to copy.";
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      els.copyCoverLetterStatus.textContent = "Copied.";
    } catch (_err) {
      els.copyCoverLetterStatus.textContent = "Copy failed.";
    }
  });

  els.copyAllCommandsBtn.addEventListener("click", async () => {
    const raw = els.jobCommandsList.getAttribute("data-commands");
    if (!raw) {
      els.copyCommandsStatus.textContent = "No commands yet.";
      return;
    }
    const cmds = JSON.parse(raw);
    if (!Array.isArray(cmds) || !cmds.length) {
      els.copyCommandsStatus.textContent = "No commands yet.";
      return;
    }
    try {
      await navigator.clipboard.writeText(cmds.join("\n"));
      els.copyCommandsStatus.textContent = "All commands copied.";
    } catch (_err) {
      els.copyCommandsStatus.textContent = "Copy failed.";
    }
  });

  els.copyAllApplyCommandsBtn.addEventListener("click", async () => {
    const raw = els.applyCommandsList.getAttribute("data-commands");
    if (!raw) {
      els.copyApplyCommandsStatus.textContent = "No commands yet.";
      return;
    }
    const cmds = JSON.parse(raw);
    if (!Array.isArray(cmds) || !cmds.length) {
      els.copyApplyCommandsStatus.textContent = "No commands yet.";
      return;
    }
    try {
      await navigator.clipboard.writeText(cmds.join("\n"));
      els.copyApplyCommandsStatus.textContent = "All commands copied.";
    } catch (_err) {
      els.copyApplyCommandsStatus.textContent = "Copy failed.";
    }
  });
}

function init() {
  bindTabs();
  bindFilters();
  loadAll();
  setInterval(loadAll, REFRESH_MS);
}

init();
