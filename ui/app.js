const DATA_BASE = "/var/ui-data";
const REFRESH_MS = 60_000;

const state = {
  data: null,
  jobs: [],
  selectedJobId: null,
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
  jobDetailMeta: document.getElementById("jobDetailMeta"),
  jobDetailLinks: document.getElementById("jobDetailLinks"),
  jobDetailGrid: document.getElementById("jobDetailGrid"),
  coverLetterText: document.getElementById("coverLetterText"),
  answersText: document.getElementById("answersText"),
  draftReportText: document.getElementById("draftReportText"),
  jobDescriptionText: document.getElementById("jobDescriptionText"),
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
  const profile = data.config?.profile || {};
  const app = data.config?.app || {};

  els.profileGrid.innerHTML = [
    renderKvCard("Identity", profile, ["full_name", "headline", "email", "phone", "location_text", "years_experience"]),
    renderKvCard("Targets", profile, ["target_titles", "required_skills", "preferred_skills", "allowed_countries"]),
    renderKvCard("Career", profile, ["summary", "strengths", "recent_titles", "recent_companies"]),
    renderKvCard("Application", profile.application || {}, ["remote_preference", "relocation_preference", "salary_expectation", "notice_period", "current_availability"]),
  ].join("");

  els.settingsGrid.innerHTML = [
    renderKvCard("Config Paths", data.config?.paths || {}, ["app_config_path", "profile_path", "ranking_path", "sources_path"]),
    renderKvCard("HTTP", app.http || {}, ["timeout_seconds", "max_attempts", "user_agent"]),
    renderKvCard("Delivery", app.delivery || {}, ["channel", "daily_hour", "digest_max_items"]),
    renderKvCard("Email (non-secret)", app.delivery?.email || {}, ["enabled", "host", "port", "username", "use_tls", "sender", "recipient"]),
  ].join("");
}

function renderRanking(data) {
  const policy = data.ranking?.ranking_policy || {};
  const reviewGate = data.ranking?.review_eligibility || {};

  els.rankingPolicy.innerHTML = [
    renderKvCard("Policy", policy, ["stale_days", "minimum_score", "minimum_salary", "require_remote", "remote_first"]),
    renderKvCard("Review Eligibility", reviewGate, ["minimum_score", "must_pass_hard_filters", "note"]),
    renderKvCard("Preference Lists", policy, ["blocked_companies", "blocked_title_keywords", "allowed_companies", "preferred_companies", "preferred_timezones"]),
    renderKvCard("Scoring Inputs", data.ranking?.profile_fields_for_scoring || {}, ["target_titles", "required_skills", "preferred_skills", "preferred_locations"]),
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
  const configSources = (data.sources?.sources || []).map((s) => s.name);

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
  state.jobs = data.jobs?.jobs || [];
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

function renderJobDetail(jobId) {
  state.selectedJobId = jobId;
  const job = jobsById().get(jobId);
  const app = applicationByJobId().get(jobId);
  const detail = jobDetailsById().get(jobId);

  if (!job && !app) {
    els.jobDetailMeta.textContent = `No detail found for job_id=${jobId}`;
    els.jobDetailLinks.innerHTML = "";
    els.jobDetailGrid.innerHTML = "";
    els.coverLetterText.value = "";
    els.answersText.value = "";
    els.draftReportText.value = "";
    els.jobDescriptionText.value = "";
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

  els.coverLetterText.value = app?.cover_letter?.text || "";
  els.answersText.value = app?.answers?.text || "";
  els.draftReportText.value = app?.draft_report_text || app?.missing_inputs_text || "";
  els.jobDescriptionText.value = detail?.description_text || app?.packet_summary?.description_excerpt || job?.description_snippet || "";
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
    const [config, ranking, sources, jobs, report, application, jobDetails, generatedAt] = await Promise.all([
      fetchJson(`${DATA_BASE}/config.json`),
      fetchJson(`${DATA_BASE}/ranking.json`),
      fetchJson(`${DATA_BASE}/sources.json`),
      fetchJson(`${DATA_BASE}/jobs.json`),
      fetchJson(`${DATA_BASE}/report.json`),
      fetchJson(`${DATA_BASE}/application.json`),
      fetchJson(`${DATA_BASE}/job_details.json`),
      fetchText(`${DATA_BASE}/generated_at.txt`),
    ]);

    state.data = { config, ranking, sources, jobs, report, application, jobDetails, generatedAt };
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

  els.copyCoverLetterBtn.addEventListener("click", async () => {
    const text = els.coverLetterText.value || "";
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
}

function init() {
  bindTabs();
  bindFilters();
  loadAll();
  setInterval(loadAll, REFRESH_MS);
}

init();
