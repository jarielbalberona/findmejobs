const DATA_BASE = "/var/ui-data";
const REFRESH_MS = 60_000;

const state = {
  data: null,
  jobs: [],
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

function renderOverview(data) {
  const ranking = data.report?.ranking || {};
  const delivery = data.report?.delivery || {};
  const runs = data.report?.pipeline_runs || [];

  const latestRun = runs[0];
  els.overviewCards.innerHTML = [
    card("Sources (runtime)", data.report?.sources?.length || 0),
    card("Jobs ranked", ranking.ranked ?? 0),
    card("Jobs filtered", ranking.filtered ?? 0),
    card("Latest digest", delivery.latest_digest_status || "none"),
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
        <td>${job.canonical_url ? `<a href="${escapeHtml(job.canonical_url)}" target="_blank" rel="noreferrer">open</a>` : "-"}</td>
      </tr>`,
    )
    .join("");

  els.jobsTable.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Title</th><th>Company</th><th>Location</th><th>Score</th><th>Status</th><th>Source</th>
            <th>Signals</th><th>Tags</th><th>URL</th>
          </tr>
        </thead>
        <tbody>${rows || '<tr><td colspan="9">No jobs match current filters.</td></tr>'}</tbody>
      </table>
    </div>`;
}

async function loadAll() {
  clearError();
  try {
    const [config, ranking, sources, jobs, report, generatedAt] = await Promise.all([
      fetchJson(`${DATA_BASE}/config.json`),
      fetchJson(`${DATA_BASE}/ranking.json`),
      fetchJson(`${DATA_BASE}/sources.json`),
      fetchJson(`${DATA_BASE}/jobs.json`),
      fetchJson(`${DATA_BASE}/report.json`),
      fetchText(`${DATA_BASE}/generated_at.txt`),
    ]);

    state.data = { config, ranking, sources, jobs, report, generatedAt };
    els.generatedAt.textContent = generatedAt ? `Snapshot: ${generatedAt}` : "Snapshot loaded";

    renderOverview(state.data);
    renderProfileAndSettings(state.data);
    renderRanking(state.data);
    renderSources(state.data);
    renderJobs(state.data);
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
    const name = btn.dataset.tab;

    document.querySelectorAll(".tab").forEach((el) => el.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((el) => el.classList.remove("active"));

    btn.classList.add("active");
    const panel = document.querySelector(`.panel[data-panel="${name}"]`);
    if (panel) panel.classList.add("active");
  });
}

function bindFilters() {
  [els.jobSearch, els.statusFilter, els.sourceFilter, els.minScore, els.sortBy].forEach((el) => {
    el.addEventListener("input", applyJobFilters);
    el.addEventListener("change", applyJobFilters);
  });

  els.refreshBtn.addEventListener("click", loadAll);
}

function init() {
  bindTabs();
  bindFilters();
  loadAll();
  setInterval(loadAll, REFRESH_MS);
}

init();
