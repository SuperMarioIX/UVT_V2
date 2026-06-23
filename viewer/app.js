/* ============================================================
 * whisper2 viewer — main application logic
 *
 * Loads diagnostics.json (required) and frames.json (optional)
 * produced by `whisper2/main.py` and renders an interactive
 * single-page UI with no server / no install dependency.
 * ============================================================ */

(() => {
'use strict';

// =============================================================
// Mermaid init
// =============================================================
if (window.mermaid) {
  mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'loose',
    flowchart: { useMaxWidth: true },
    stateDiagram: { useMaxWidth: true, padding: 12 },
    themeVariables: {
      darkMode: true,
      background: '#0f1320',
      primaryColor: '#1d2440',
      primaryTextColor: '#e8edf7',
      primaryBorderColor: '#34406c',
      lineColor: '#5d6788',
      secondaryColor: '#232b4d',
      tertiaryColor: '#151a2c',
      fontFamily: 'JetBrains Mono, monospace',
    },
  });
}

// =============================================================
// State
// =============================================================
const state = {
  diagnostics: null,        // { verdict, flows, log_warnings }
  frames: null,             // { COMP_NAME: { Frame1[ts]: {...}, ... } }
  activeTab: 'overview',
  flowFilter: 'all',
  issuesFilter: 'all',
  warningsLevel: 'all',
  componentsSort: 'activity',
  search: '',
  selectedFlow: null,
  selectedComp: null,
  selectedIssue: null,
};

// =============================================================
// DOM helpers
// =============================================================
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class')      node.className = v;
    else if (k === 'style') Object.assign(node.style, v);
    else if (k === 'html')  node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v === false || v == null) continue;
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, ch => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
}

function fmtDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m${s.toString().padStart(2,'0')}s`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  // Accept "2026-05-10T18:22:51.174481" or numbers
  const t = String(iso);
  const m = t.match(/T(\d{2}:\d{2}:\d{2}(\.\d+)?)/);
  if (m) return m[1].slice(0, 12);
  return t;
}

function shortPath(loc) {
  if (!loc) return '';
  return String(loc).split('/').pop();
}

function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h) + s.charCodeAt(i) | 0;
  return h;
}

// =============================================================
// Toasts
// =============================================================
function toast(msg, kind = 'info', title) {
  const container = $('#toastContainer');
  const t = el('div', { class: `toast ${kind}` },
    title && el('div', { class: 'toast-title' }, title),
    el('div', { class: 'toast-msg' }, msg),
  );
  container.appendChild(t);
  setTimeout(() => {
    t.classList.add('removing');
    setTimeout(() => t.remove(), 200);
  }, 3500);
}

// =============================================================
// File loading
// =============================================================
async function loadFiles(fileList) {
  const files = Array.from(fileList);
  if (!files.length) return;

  for (const f of files) {
    try {
      const text = await f.text();
      const data = JSON.parse(text);
      const kind = detectKind(data);
      if (kind === 'diagnostics') {
        state.diagnostics = data;
        toast(`Loaded ${f.name}`, 'success', 'diagnostics');
      } else if (kind === 'frames') {
        state.frames = data;
        toast(`Loaded ${f.name}`, 'success', 'frames');
      } else if (kind === 'bundle') {
        state.diagnostics = data.diagnostics;
        state.frames = data.frames;
        toast(`Loaded bundle ${f.name}`, 'success');
      } else {
        toast(`${f.name}: unknown JSON shape`, 'warning');
      }
    } catch (err) {
      console.error(err);
      toast(`Failed to read ${f.name}: ${err.message}`, 'danger', 'Parse error');
    }
  }

  if (state.diagnostics) {
    refreshAll();
    document.body.classList.remove('empty');
    $('#emptyState').classList.add('hidden');
    $('#app').classList.remove('empty');
  }
}

function detectKind(data) {
  if (!data || typeof data !== 'object') return null;
  if (data.diagnostics && data.frames) return 'bundle';
  if ('verdict' in data && 'flows' in data) return 'diagnostics';
  // frames.json: top-level dict of <COMP> -> dict of "Frame*[..]" -> { State, ico_summary, ... }
  const keys = Object.keys(data);
  if (keys.length > 0) {
    const first = data[keys[0]];
    if (first && typeof first === 'object') {
      const innerKeys = Object.keys(first);
      if (innerKeys.length === 0) return 'frames';  // empty component
      if (innerKeys.some(k => /^Frame\d+\[/.test(k))) return 'frames';
    }
  }
  return null;
}

// =============================================================
// Refresh / render
// =============================================================
function refreshAll() {
  refreshTopbar();
  refreshSidebarMeta();
  refreshBadges();
  renderActiveView();
}

function refreshTopbar() {
  const d = state.diagnostics;
  if (!d) return;
  const sb = $('#statusBar');
  sb.removeAttribute('hidden');

  const v = d.verdict || {};
  const f = d.flows || {};
  const w = d.log_warnings || {};
  const issues = (v.issues || []).length;
  const flowsTotal = (f.summary && f.summary.declared) || (f.results && f.results.length) || 0;
  const flowsOK    = (f.summary && f.summary.validated) || 0;
  const flowsMiss  = (f.summary && f.summary.missing) || 0;
  const wrnKept    = (w.summary && w.summary.total_kept) || 0;

  const pass = (v.passed === true) || (v.global_verdict === 'pass' && issues === 0 && flowsMiss === 0 && flowsTotal > 0);
  const verdictText = (v.global_verdict || 'unknown').toUpperCase();
  const pill = $('#verdictPill');
  pill.textContent = pass ? 'PASS' : verdictText;
  pill.dataset.verdict = pass ? 'pass' : (v.global_verdict || 'unknown');

  const meta = $('#statusMeta');
  meta.innerHTML = '';
  const parts = [];
  if (v.test_name)  parts.push(`<strong>${escapeHtml(v.test_name)}</strong>`);
  if (f.config)     parts.push(`config: <strong>${escapeHtml(f.config)}</strong>`);
  parts.push(`<strong>${flowsOK}</strong> / <strong>${flowsTotal}</strong> flows`);
  parts.push(`<strong>${issues}</strong> issues`);
  if (wrnKept) parts.push(`<strong>${wrnKept}</strong> warnings`);
  meta.innerHTML = parts.join('<span class="sep">·</span>');
}

function refreshSidebarMeta() {
  const d = state.diagnostics;
  if (!d) return;
  $('#metaCard').removeAttribute('hidden');
  const v = d.verdict || {};
  const f = d.flows || {};
  $('#metaTest').textContent = v.test_name || '—';
  $('#metaTest').setAttribute('title', v.test_name || '');
  $('#metaConfig').textContent = f.config || '—';
  // Duration: parse from tcfi_at if available, no other source for now
  $('#metaDuration').textContent = computeDuration();
  $('#metaComponents').textContent = state.frames ? Object.keys(state.frames).length : '—';
}

function computeDuration() {
  // Estimate from earliest declared flow timestamp to tcfi_at
  const f = state.diagnostics?.flows;
  const v = state.diagnostics?.verdict;
  if (!v?.tcfi_at) return '—';
  const decls = (f?.results || []).map(r => r.declared_at).filter(Boolean).sort();
  if (decls.length === 0) return '—';
  const t0 = new Date(decls[0]).getTime();
  const t1 = new Date(v.tcfi_at).getTime();
  if (!isFinite(t0) || !isFinite(t1)) return '—';
  return fmtDuration((t1 - t0) / 1000);
}

function refreshBadges() {
  const d = state.diagnostics;
  if (!d) return;

  const setBadge = (key, count, kind) => {
    const b = document.querySelector(`[data-badge="${key}"]`);
    if (!b) return;
    b.textContent = count;
    b.classList.remove('danger', 'warning', 'success');
    if (kind) b.classList.add(kind);
  };

  const issues = (d.verdict?.issues || []).length;
  const f = d.flows || {};
  const flowsTotal = (f.summary && f.summary.declared) || 0;
  const flowsMiss  = (f.summary && f.summary.missing) || 0;
  const compsTotal = state.frames ? Object.keys(state.frames).length : 0;
  const wrnKept    = d.log_warnings?.summary?.total_kept || 0;

  setBadge('issues', issues, issues > 0 ? 'danger' : null);
  setBadge('flows', flowsMiss > 0 ? `${flowsTotal - flowsMiss}/${flowsTotal}` : flowsTotal, flowsMiss > 0 ? 'danger' : 'success');
  setBadge('components', compsTotal);
  setBadge('warnings', wrnKept, wrnKept > 0 ? 'warning' : null);
}

// =============================================================
// Tabs
// =============================================================
function setActiveTab(tab) {
  state.activeTab = tab;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  $$('.view').forEach(v => v.classList.toggle('hidden', v.dataset.view !== tab));
  renderActiveView();
}

function renderActiveView() {
  if (!state.diagnostics) return;
  switch (state.activeTab) {
    case 'overview':   renderOverview(); break;
    case 'issues':     renderIssues();   break;
    case 'flows':      renderFlows();    break;
    case 'components': renderComponents(); break;
    case 'warnings':   renderWarnings(); break;
  }
}

// =============================================================
// OVERVIEW VIEW
// =============================================================
function renderOverview() {
  const body = $('#overviewBody');
  clear(body);
  const d = state.diagnostics;
  const v = d.verdict || {};
  const f = d.flows || {};
  const w = d.log_warnings || {};

  const issues = v.issues || [];
  const issuesBySev = {};
  for (const i of issues) issuesBySev[i.severity] = (issuesBySev[i.severity] || 0) + 1;

  const flowsTotal = (f.summary && f.summary.declared) || 0;
  const flowsOK    = (f.summary && f.summary.validated) || 0;
  const flowsMiss  = (f.summary && f.summary.missing) || 0;

  const compsTotal = state.frames ? Object.keys(state.frames).length : null;
  const wrnKept    = w.summary?.total_kept || 0;
  const wrnSuppressed = w.summary?.suppressed || 0;

  const overallPass = (v.passed === true) || (v.global_verdict === 'pass' && issues.length === 0 && flowsMiss === 0);

  // KPI grid
  const kpis = el('div', { class: 'kpi-grid' },
    kpiCard('Verdict', overallPass ? 'PASS' : (v.global_verdict || 'unknown').toUpperCase(),
            overallPass ? 'success' : 'danger',
            `tcfi @ ${fmtTime(v.tcfi_at)}`,
            iconCheck()),
    kpiCard('Flows', `${flowsOK}/${flowsTotal}`, flowsMiss === 0 ? 'success' : 'danger',
            flowsMiss === 0 ? 'all validated' : `${flowsMiss} missing`,
            iconFlow()),
    kpiCard('Issues', issues.length, issues.length === 0 ? 'success' : 'danger',
            issues.length === 0 ? 'no failures detected' :
              Object.entries(issuesBySev).map(([s,n]) => `${n} ${s.toLowerCase()}`).join(' · '),
            iconAlert()),
    kpiCard('Components', compsTotal != null ? compsTotal : '—', 'info',
            compsTotal != null ? 'tracked in this run' : 'load frames.json to see',
            iconCode()),
    kpiCard('Warnings', wrnKept, wrnKept === 0 ? 'success' : 'warning',
            wrnSuppressed > 0 ? `${wrnSuppressed} suppressed by whitelist` : 'no signals',
            iconBolt()),
    kpiCard('Duration', computeDuration(), 'info', 'wall-clock', iconClock()),
  );
  body.appendChild(kpis);

  // Critical issues quick jump (only if any)
  if (issues.length > 0) {
    const sec = el('div', { class: 'overview-section' },
      el('h3', {}, 'Top issues'),
    );
    const list = el('div', {});
    issues.slice(0, 5).forEach(i => list.appendChild(buildIssueCard(i, false)));
    sec.appendChild(list);
    if (issues.length > 5) {
      sec.appendChild(el('div', { class: 'kv-key', style: { marginTop: '8px', textAlign: 'right' } },
        el('a', { href: '#', class: 'kv-val',
                  style: { color: 'var(--accent)', cursor: 'pointer' },
                  onclick: (e) => { e.preventDefault(); setActiveTab('issues'); }
                }, `+ ${issues.length - 5} more →`)));
    }
    body.appendChild(sec);
  }

  // Missing flows quick view
  const missing = (f.results || []).filter(r => !r.validated);
  if (missing.length > 0) {
    const sec = el('div', { class: 'overview-section' },
      el('h3', {}, 'Missing flows'),
    );
    missing.slice(0, 8).forEach(r => sec.appendChild(buildFlowCard(r)));
    body.appendChild(sec);
  }

  // Top components (if frames loaded)
  if (state.frames) {
    const sec = el('div', { class: 'overview-section' },
      el('h3', {}, 'Most active components'),
    );
    const grid = el('div', { class: 'components-grid' });
    const top = sortComponents(Object.keys(state.frames), 'activity').slice(0, 6);
    top.forEach(name => grid.appendChild(buildComponentCard(name)));
    sec.appendChild(grid);
    body.appendChild(sec);
  }

  // Top warnings
  if ((w.items || []).length > 0) {
    const sec = el('div', { class: 'overview-section' },
      el('h3', {}, 'Top warnings'),
    );
    const list = el('div', { class: 'warnings-table' });
    (w.items || []).slice(0, 5).forEach(item => list.appendChild(buildWarningRow(item)));
    sec.appendChild(list);
    body.appendChild(sec);
  }
}

function kpiCard(label, value, kind = 'info', meta = '', icon = null) {
  return el('div', { class: `kpi-card ${kind}` },
    el('div', { class: 'kpi-label' }, icon, el('span', {}, label)),
    el('div', { class: 'kpi-value' }, value),
    meta && el('div', { class: 'kpi-meta' }, meta),
  );
}

// =============================================================
// ISSUES VIEW
// =============================================================
function renderIssues() {
  const body = $('#issuesBody');
  clear(body);
  const issues = state.diagnostics?.verdict?.issues || [];

  let filtered = issues;
  if (state.issuesFilter !== 'all') {
    filtered = filtered.filter(i => i.severity === state.issuesFilter);
  }
  if (state.search) {
    const q = state.search.toLowerCase();
    filtered = filtered.filter(i =>
      [i.kind, i.message, i.component, i.source].some(s => (s || '').toLowerCase().includes(q))
    );
  }

  if (!filtered.length) {
    body.appendChild(emptyMsg(
      issues.length === 0 ? 'No issues detected' : 'No issues match the filter',
      issues.length === 0 ? 'The test passed cleanly.' : 'Try clearing the filter or search.',
      iconCheck(),
    ));
    return;
  }

  filtered.forEach(i => body.appendChild(buildIssueCard(i)));
}

function buildIssueCard(issue) {
  const card = el('button', {
    class: 'issue-card',
    'data-severity': issue.severity,
    onclick: () => openIssueDrawer(issue),
  });
  card.innerHTML = `
    <div class="issue-row1">
      <span class="issue-sev" data-severity="${escapeHtml(issue.severity)}">${escapeHtml(issue.severity)}</span>
      <span class="issue-kind">${escapeHtml(issue.kind)}</span>
      ${issue.component ? `<span class="issue-comp">${escapeHtml(issue.component)}</span>` : ''}
    </div>
    <div class="issue-msg">${escapeHtml(issue.message)}</div>
    <div class="issue-meta">${[
        issue.ts && fmtTime(issue.ts),
        issue.source && shortPath(issue.source),
        issue.from_verdict && issue.to_verdict && `verdict: ${issue.from_verdict} → ${issue.to_verdict}`,
      ].filter(Boolean).join(' · ')}</div>
  `;
  return card;
}

function openIssueDrawer(issue) {
  state.selectedIssue = issue;
  const header = $('#drawerHeader');
  header.innerHTML = `
    <button class="drawer-close" onclick="window.__viewer.closeDrawer()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
    <h3>
      <span class="issue-sev" data-severity="${escapeHtml(issue.severity)}">${escapeHtml(issue.severity)}</span>
      ${escapeHtml(issue.kind)}
    </h3>
    <div class="drawer-sub">${escapeHtml(issue.message)}</div>
  `;

  const body = $('#drawerBody');
  body.innerHTML = '';
  const kv = el('div', { class: 'drawer-section' },
    el('h4', {}, 'Details'),
    el('div', { class: 'kv-list' },
      kvRow('Severity', issue.severity),
      kvRow('Kind', issue.kind),
      issue.ts && kvRow('Timestamp', fmtTime(issue.ts)),
      issue.component && kvRow('Component', issue.component),
      issue.source && kvRow('Source', issue.source),
      issue.from_verdict != null && kvRow('From verdict', issue.from_verdict),
      issue.to_verdict != null && kvRow('To verdict', issue.to_verdict),
      issue.test_name && kvRow('Test', issue.test_name),
    ),
  );
  body.appendChild(kv);

  if (issue.component && state.frames && state.frames[issue.component]) {
    body.appendChild(el('div', { class: 'drawer-section' },
      el('h4', {}, 'Jump to'),
      el('button', {
        class: 'btn-primary',
        onclick: () => { closeDrawer(); setActiveTab('components'); setTimeout(() => openComponentDrawer(issue.component), 250); },
      }, `Open component ${issue.component} →`),
    ));
  }

  openDrawer();
}

function kvRow(k, v) { return el('div', { class: 'kv-row' }, el('div', { class: 'kv-key' }, k), el('div', { class: 'kv-val' }, String(v))); }

// =============================================================
// FLOWS VIEW
// =============================================================
function renderFlows() {
  const body = $('#flowsBody');
  clear(body);
  const results = state.diagnostics?.flows?.results || [];

  let filtered = results;
  if (state.flowFilter === 'validated') filtered = filtered.filter(r => r.validated);
  else if (state.flowFilter === 'missing') filtered = filtered.filter(r => !r.validated);
  if (state.search) {
    const q = state.search.toLowerCase();
    filtered = filtered.filter(r =>
      [r.message, r.kind, r.validating_component, r.validating_location].some(s => (s || '').toLowerCase().includes(q))
    );
  }

  if (!filtered.length) {
    body.appendChild(emptyMsg('No flows match', 'Try clearing the filter or search.', iconFlow()));
    return;
  }

  filtered.forEach(r => body.appendChild(buildFlowCard(r)));
}

function buildFlowCard(r) {
  const card = el('button', {
    class: `flow-card ${r.validated ? 'validated' : 'missing'}`,
    'data-index': r.index,
    onclick: () => openFlowDrawer(r),
  });
  card.innerHTML = `
    <div class="flow-status">
      ${r.validated
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'}
    </div>
    <div class="flow-index">#${r.index}</div>
    <div class="flow-content">
      <div class="flow-msg">${escapeHtml(r.message)}</div>
      <div class="flow-meta">
        <span class="chip ${r.kind === 'Startup flow' ? 'kind-startup' : ''}">${escapeHtml(r.kind)}</span>
        ${r.validating_component ? `<span>↳ ${escapeHtml(r.validating_component)}</span>` : ''}
        ${r.validating_location ? `<span class="source">${escapeHtml(r.validating_location)}</span>` : ''}
        ${r.validation_count > 0 ? `<span>${r.validation_count}× validated</span>` : ''}
      </div>
    </div>
    <div class="flow-time">${r.first_validated_at ? fmtTime(r.first_validated_at) : '<span style="color:var(--danger)">never</span>'}</div>
  `;
  return card;
}

function openFlowDrawer(r) {
  state.selectedFlow = r;
  const header = $('#drawerHeader');
  const statusPill = r.validated
    ? '<span class="verdict-pill" data-verdict="pass">VALIDATED</span>'
    : '<span class="verdict-pill" data-verdict="fail">MISSING</span>';
  header.innerHTML = `
    <button class="drawer-close" onclick="window.__viewer.closeDrawer()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
    <h3>${statusPill} <span class="mono" style="color:var(--text-dim)">#${r.index}</span> ${escapeHtml(r.kind)}</h3>
    <div class="drawer-sub">${escapeHtml(r.message)}</div>
  `;

  const body = $('#drawerBody');
  body.innerHTML = '';

  // ---- 1. Validation summary
  body.appendChild(el('div', { class: 'drawer-section' },
    el('h4', {}, 'Validation'),
    el('div', { class: 'kv-list' },
      kvRow('Status', r.validated ? 'Validated' : 'MISSING'),
      r.first_validated_at && kvRow('Validated at', fmtTime(r.first_validated_at)),
      r.validating_component && kvRow('By component', r.validating_component),
      r.validating_location && kvRow('Source', r.validating_location),
    ),
  ));

  // ---- 2. Components active around the flow timestamp (correlation)
  const anchorTs = r.first_validated_at || r.declared_at;
  if (anchorTs && state.frames) {
    const correlated = correlateComponentsAroundTs(anchorTs, 5.0); // ±5s window
    if (correlated.length > 0) {
      const sec = el('div', { class: 'drawer-section' });
      sec.appendChild(el('h4', {}, `Components active around ${fmtTime(anchorTs)} (±5s)`));
      const list = el('div', { class: 'corr-list' });
      correlated.forEach(c => {
        const card = el('button', {
          class: 'corr-card',
          onclick: () => { closeDrawer(); setActiveTab('components'); setTimeout(() => openComponentDrawer(c.name), 250); },
        });
        const dirBadges = [
          c.in  > 0 ? `<span class="corr-num in">↓${c.in}</span>`  : '',
          c.cons > 0 ? `<span class="corr-num cons">✓${c.cons}</span>` : '',
          c.out > 0 ? `<span class="corr-num out">↑${c.out}</span>` : '',
        ].filter(Boolean).join(' ');
        card.innerHTML = `
          <div class="corr-head">
            <span class="corr-name">${escapeHtml(c.name)}</span>
            <span class="corr-state">${escapeHtml(c.state)}</span>
          </div>
          <div class="corr-stats">${dirBadges || '<span style="color:var(--text-dim)">no messages in window</span>'}</div>
          ${c.lastMsg ? `<div class="corr-msg">${escapeHtml(c.lastMsg)}</div>` : ''}
        `;
        list.appendChild(card);
      });
      sec.appendChild(list);
      body.appendChild(sec);
    }
  }

  // ---- 3. All occurrences timeline (when this body appeared multiple times)
  if ((r.all_validation_ts || []).length > 1) {
    const tsList = r.all_validation_ts.map((t, i) =>
      el('div', { class: 'frame-entry' },
        el('div', { class: 'ts' }, fmtTime(t)),
        el('div', { class: 'state' }, i === 0 && r.validated && t === r.first_validated_at ? '✓ claimed by this row' : '✓ validated (other declaration)'),
      )
    );
    body.appendChild(el('div', { class: 'drawer-section' },
      el('h4', {}, `All ${r.all_validation_ts.length} occurrences across the run`),
      el('div', { class: 'frames-timeline' }, ...tsList),
    ));
  }

  if (!r.validated) {
    body.appendChild(el('div', { class: 'drawer-section' },
      el('h4', {}, 'Why missing?'),
      el('div', { class: 'note-box' },
        `This flow was declared but no matching ulog "${r.kind}: ${r.message}" `
        + `print appeared after the declaration timestamp `
        + (r.expected_count > 1 ? `(this body is declared ${r.expected_count}× total in the test, but only ${r.validation_count} hit(s) were observed; earlier declarations claimed the available hits first).` : `(no occurrences in the log).`),
      ),
    ));
  }

  openDrawer();
}

/**
 * For a given timestamp, find components whose frames fall within ±windowSec
 * seconds. Returns a list of objects:
 *   { name, state, in, cons, out, lastMsg }
 * sorted by total in-window message activity (descending).
 */
function correlateComponentsAroundTs(anchorIso, windowSec) {
  if (!state.frames) return [];
  const anchor = new Date(anchorIso).getTime();
  if (!isFinite(anchor)) return [];
  const lo = anchor - windowSec * 1000;
  const hi = anchor + windowSec * 1000;

  const result = [];
  for (const [name, frames] of Object.entries(state.frames)) {
    let inMsg = 0, cons = 0, out = 0;
    let lastFrameInWindow = null;
    let prevIn = 0, prevCons = 0, prevOut = 0;
    let prevState = null;

    for (const [key, f] of Object.entries(frames)) {
      const ts = parseFrameKeyTs(key, anchor);
      const cumIn = f.ico_summary?.in || 0;
      const cumCons = f.ico_summary?.consume || 0;
      const cumOut = f.ico_summary?.out || 0;
      if (ts != null && ts >= lo && ts <= hi) {
        inMsg += Math.max(0, cumIn - prevIn);
        cons  += Math.max(0, cumCons - prevCons);
        out   += Math.max(0, cumOut - prevOut);
        lastFrameInWindow = f;
      }
      prevIn = cumIn; prevCons = cumCons; prevOut = cumOut;
      if (lastFrameInWindow) prevState = f.State;
    }

    if (lastFrameInWindow) {
      const allMsgs = [
        ...(lastFrameInWindow.Outgoing_messages || []).map(m => `↑ ${m}`),
        ...(lastFrameInWindow.Consumed_messages || []).map(m => `✓ ${m}`),
        ...(lastFrameInWindow.Incoming_messages || []).map(m => `↓ ${m}`),
      ];
      result.push({
        name,
        state: lastFrameInWindow.State || '?',
        in: inMsg, cons, out,
        lastMsg: allMsgs[0] || '',
        score: inMsg + cons + out,
      });
    }
  }

  // sort by activity in window, descending; cap at 12 to keep drawer readable
  result.sort((a, b) => b.score - a.score);
  return result.slice(0, 12);
}

/**
 * Parse "FrameN[HH:MM:SS.micro]" -> ms timestamp on the same date as anchor.
 * Anchor is used to recover the date because frame keys only have time-of-day.
 */
function parseFrameKeyTs(frameKey, anchorMs) {
  const m = frameKey.match(/\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\]/);
  if (!m) return null;
  const d = new Date(anchorMs);
  d.setHours(parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10),
             m[4] ? parseInt(m[4].slice(0, 3).padEnd(3, '0'), 10) : 0);
  return d.getTime();
}

// =============================================================
// COMPONENTS VIEW
// =============================================================
function renderComponents() {
  const body = $('#componentsBody');
  clear(body);

  if (!state.frames) {
    body.appendChild(emptyMsg(
      'No frames data',
      'Drop the matching <code>*_frames.json</code> file to see component state machines.',
      iconCode(),
    ));
    return;
  }

  let names = Object.keys(state.frames).filter(n => Object.keys(state.frames[n]).length > 0);
  if (state.search) {
    const q = state.search.toLowerCase();
    names = names.filter(n => n.toLowerCase().includes(q));
  }

  names = sortComponents(names, state.componentsSort);

  if (!names.length) {
    body.appendChild(emptyMsg('No components match', 'Try clearing the search.', iconCode()));
    return;
  }

  const grid = el('div', { class: 'components-grid' });
  names.forEach(n => grid.appendChild(buildComponentCard(n)));
  body.appendChild(grid);
}

function sortComponents(names, key) {
  const totals = name => {
    const frames = state.frames[name];
    if (!frames || Object.keys(frames).length === 0) return { total: 0, frames: 0 };
    const last = Object.values(frames).at(-1);
    const ico = last?.ico_summary || {};
    return {
      total: (ico.in||0) + (ico.consume||0) + (ico.out||0),
      frames: Object.keys(frames).length,
    };
  };
  const arr = names.slice();
  if (key === 'name') arr.sort((a,b) => a.localeCompare(b));
  else if (key === 'frames') arr.sort((a,b) => totals(b).frames - totals(a).frames);
  else /* activity */ arr.sort((a,b) => totals(b).total - totals(a).total);
  return arr;
}

function buildComponentCard(name) {
  const frames = state.frames[name];
  const frameNames = Object.keys(frames);
  const last = Object.values(frames).at(-1);
  const ico = last?.ico_summary || {};

  // verdict from cofi (in registry-derived component_verdicts)
  const compVerdicts = state.diagnostics?.verdict?.component_verdicts || {};
  const verdict = compVerdicts[name] || null;

  // Sparkline: per-frame total messages (incremental from cumulative)
  const sparkPoints = [];
  let prevTotal = 0;
  for (const f of Object.values(frames)) {
    const t = (f.ico_summary?.in || 0) + (f.ico_summary?.consume || 0) + (f.ico_summary?.out || 0);
    sparkPoints.push(Math.max(0, t - prevTotal));
    prevTotal = t;
  }
  const max = Math.max(1, ...sparkPoints);
  const bars = sparkPoints.length > 60
    ? bucketize(sparkPoints, 60)
    : sparkPoints;
  const maxB = Math.max(1, ...bars);

  const card = el('button', {
    class: `component-card ${verdict === 'pass' ? 'pass' : ''} ${verdict === 'fail' ? 'fail' : ''}`,
    onclick: () => openComponentDrawer(name),
  });
  card.innerHTML = `
    <div class="head">
      <span>${escapeHtml(name)}</span>
      <span class="verdict-dot" title="${verdict || 'no verdict'}"></span>
    </div>
    <div class="sparkline">
      ${bars.map(v => `<div class="spark-bar" style="height: ${(v/maxB)*100}%"></div>`).join('')}
    </div>
    <div class="stats">
      <div class="stat"><span class="stat-label">in</span><span class="stat-value in">${ico.in||0}</span></div>
      <div class="stat"><span class="stat-label">consumed</span><span class="stat-value cons">${ico.consume||0}</span></div>
      <div class="stat"><span class="stat-label">out</span><span class="stat-value out">${ico.out||0}</span></div>
    </div>
    <div class="footer">
      <span class="frame-count">${frameNames.length} frames</span>
      <span>·</span>
      <span>${countUniqueStates(frames)} states</span>
      ${verdict ? `<span>·</span><span style="color:var(--${verdict==='pass'?'success':verdict==='fail'?'danger':'text-muted'})">${verdict}</span>` : ''}
    </div>
  `;
  return card;
}

function bucketize(arr, n) {
  const size = Math.ceil(arr.length / n);
  const out = [];
  for (let i = 0; i < arr.length; i += size) {
    out.push(arr.slice(i, i + size).reduce((a,b) => a+b, 0));
  }
  return out;
}

function countUniqueStates(frames) {
  const set = new Set();
  for (const f of Object.values(frames)) set.add(f.State);
  return set.size;
}

function openComponentDrawer(name) {
  state.selectedComp = name;
  const frames = state.frames[name];
  if (!frames) return;
  const frameItems = Object.entries(frames);
  const last = frameItems.at(-1)?.[1];
  const ico = last?.ico_summary || {};
  const compVerdicts = state.diagnostics?.verdict?.component_verdicts || {};
  const verdict = compVerdicts[name];

  const header = $('#drawerHeader');
  header.innerHTML = `
    <button class="drawer-close" onclick="window.__viewer.closeDrawer()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
    <h3>
      ${verdict === 'pass' ? '<span class="verdict-pill" data-verdict="pass">pass</span>' :
        verdict === 'fail' ? '<span class="verdict-pill" data-verdict="fail">fail</span>' : ''}
      ${escapeHtml(name)}
    </h3>
    <div class="drawer-sub">${frameItems.length} frames · ${countUniqueStates(frames)} unique states · ${(ico.in||0)+(ico.consume||0)+(ico.out||0)} messages</div>
  `;

  const body = $('#drawerBody');
  body.innerHTML = '';

  // ---- Header: counts at a glance, color-coded
  body.appendChild(el('div', { class: 'comp-stats-row' },
    el('div', { class: 'comp-stat in' },
      el('div', { class: 'comp-stat-label' }, 'Received (queued)'),
      el('div', { class: 'comp-stat-val' }, String(ico.in || 0)),
    ),
    el('div', { class: 'comp-stat cons' },
      el('div', { class: 'comp-stat-label' }, 'Consumed'),
      el('div', { class: 'comp-stat-val' }, String(ico.consume || 0)),
    ),
    el('div', { class: 'comp-stat out' },
      el('div', { class: 'comp-stat-label' }, 'Sent'),
      el('div', { class: 'comp-stat-val' }, String(ico.out || 0)),
    ),
  ));

  // ---- States visited (chips, ordered)
  const states = uniqueStatesInOrder(frames);
  body.appendChild(el('div', { class: 'drawer-section' },
    el('h4', {}, `States visited (${states.length})`),
    el('div', { class: 'state-chips' },
      ...states.map((s, i) => el('span', { class: 'state-chip' },
        el('span', { class: 'state-chip-idx' }, String(i + 1)),
        el('span', {}, s),
      )),
    ),
  ));

  // ---- Mermaid state diagram
  const mermaidCode = buildStateDiagram(frames);
  if (mermaidCode) {
    const sec = el('div', { class: 'drawer-section' });
    sec.appendChild(el('h4', {}, 'State machine flow'));
    const container = el('div', { class: 'drawer-mermaid', id: `mermaid-${hashStr(name)}` });
    container.textContent = mermaidCode;
    sec.appendChild(container);
    body.appendChild(sec);

    if (window.mermaid) {
      const id = `m_${hashStr(name)}_${Date.now()}`;
      mermaid.render(id, mermaidCode).then(({ svg }) => {
        container.innerHTML = svg;
      }).catch(err => {
        console.warn('Mermaid render failed', err);
        container.textContent = mermaidCode;
      });
    }
  }

  // ---- Messages SENT (aggregated by name with counts)
  const sentAgg = aggregateMessages(frameItems, 'Outgoing_messages');
  if (sentAgg.length > 0) {
    body.appendChild(el('div', { class: 'drawer-section' },
      el('h4', {}, `Messages sent (${sentAgg.length} unique)`),
      el('div', { class: 'msg-list out' },
        ...sentAgg.slice(0, 20).map(m => el('div', { class: 'msg-row' },
          el('span', { class: 'msg-arrow' }, '↑'),
          el('span', { class: 'msg-name' }, m.name),
          el('span', { class: 'msg-count' }, `×${m.count}`),
        )),
      ),
      sentAgg.length > 20 && el('div', { class: 'kv-key', style: { marginTop: '6px', textAlign: 'right' } },
        `+ ${sentAgg.length - 20} more`),
    ));
  }

  // ---- Messages RECEIVED (incoming + consumed)
  const inAgg = aggregateMessages(frameItems, 'Incoming_messages');
  const consAgg = aggregateMessages(frameItems, 'Consumed_messages');
  if (inAgg.length > 0 || consAgg.length > 0) {
    const sec = el('div', { class: 'drawer-section' });
    sec.appendChild(el('h4', {}, 'Messages received'));
    const list = el('div', { class: 'msg-list in' });
    // Merge incoming + consumed: same name, two counts
    const allMsgs = new Map();
    for (const m of inAgg) allMsgs.set(m.name, { name: m.name, in: m.count, cons: 0 });
    for (const m of consAgg) {
      const cur = allMsgs.get(m.name) || { name: m.name, in: 0, cons: 0 };
      cur.cons = m.count;
      allMsgs.set(m.name, cur);
    }
    const merged = [...allMsgs.values()].sort((a, b) => (b.in + b.cons) - (a.in + a.cons));
    for (const m of merged.slice(0, 25)) {
      list.appendChild(el('div', { class: 'msg-row' },
        el('span', { class: 'msg-arrow' }, '↓'),
        el('span', { class: 'msg-name' }, m.name),
        el('span', { class: 'msg-tags' },
          m.in > 0 && el('span', { class: 'msg-tag in' }, `queued ×${m.in}`),
          m.cons > 0 && el('span', { class: 'msg-tag cons' }, `consumed ×${m.cons}`),
          m.in > 0 && m.cons === 0 && el('span', { class: 'msg-tag warn' }, 'never consumed'),
        ),
      ));
    }
    sec.appendChild(list);
    if (merged.length > 25) {
      sec.appendChild(el('div', { class: 'kv-key', style: { marginTop: '6px', textAlign: 'right' } },
        `+ ${merged.length - 25} more`));
    }
    body.appendChild(sec);
  }

  // ---- Frame timeline (last 30) — kept compact, auxiliary detail
  const recent = frameItems.slice(-30);
  body.appendChild(el('div', { class: 'drawer-section' },
    el('h4', {}, `Recent activity (last ${recent.length} of ${frameItems.length} frames)`),
    el('div', { class: 'frames-timeline' },
      ...recent.map(([key, f]) => {
        const tsMatch = key.match(/\[([^\]]+)\]/);
        const ts = tsMatch ? tsMatch[1].slice(0, 12) : '';
        const inMsgs = (f.Incoming_messages || []).slice(0, 2).join(', ');
        const cons = (f.Consumed_messages || []).slice(0, 2).join(', ');
        const out = (f.Outgoing_messages || []).slice(0, 2).join(', ');
        const msgs = [
          out  && `<span style="color:var(--accent-3)">↑ ${escapeHtml(out)}</span>`,
          cons && `<span style="color:var(--success)">✓ ${escapeHtml(cons)}</span>`,
          inMsgs && `<span style="color:var(--accent)">↓ ${escapeHtml(inMsgs)}</span>`,
        ].filter(Boolean).join('&nbsp; ');
        const row = el('div', { class: 'frame-entry' });
        row.innerHTML = `
          <div class="ts">${ts}</div>
          <div>
            <span class="state">${escapeHtml(f.State)}</span>
            ${msgs ? `<span class="messages" style="margin-left:10px">${msgs}</span>` : ''}
          </div>
        `;
        return row;
      }),
    ),
  ));

  openDrawer();
}

/**
 * Return ordered list of unique states in the order they first appeared.
 */
function uniqueStatesInOrder(frames) {
  const seen = new Set();
  const out = [];
  for (const f of Object.values(frames)) {
    if (!seen.has(f.State)) {
      seen.add(f.State);
      out.push(f.State);
    }
  }
  return out;
}

/**
 * Aggregate per-frame message arrays into a unique list with counts.
 * Each entry in *_messages may already contain "Name(N)" suffix from the
 * compress_messages step in frames.py — we extract the count from there.
 */
function aggregateMessages(frameItems, key) {
  const map = new Map();
  for (const [, f] of frameItems) {
    const arr = f[key] || [];
    for (const raw of arr) {
      const m = String(raw).match(/^(.+?)\((\d+)\)$/);
      const name = m ? m[1] : raw;
      const cnt = m ? parseInt(m[2], 10) : 1;
      map.set(name, (map.get(name) || 0) + cnt);
    }
  }
  return [...map.entries()].map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);
}

function buildStateDiagram(frames) {
  const states = [];
  const seen = new Set();
  const transitions = new Map();  // key "A->B" -> count
  let prev = null;
  for (const f of Object.values(frames)) {
    const s = f.State || 'UNKNOWN';
    if (!seen.has(s)) { seen.add(s); states.push(s); }
    if (prev != null && prev !== s) {
      const key = prev + '|' + s;
      transitions.set(key, (transitions.get(key) || 0) + 1);
    }
    prev = s;
  }
  if (states.length === 0) return null;

  // mermaid stateDiagram-v2 syntax: state names with underscores are fine.
  // Sanitize: replace any chars outside [A-Za-z0-9_] with _.
  const sanitize = s => s.replace(/[^A-Za-z0-9_]/g, '_');
  const aliases = {};
  for (const s of states) aliases[s] = sanitize(s);

  let lines = ['stateDiagram-v2'];
  // Friendly labels for states
  for (const s of states) {
    if (aliases[s] !== s) lines.push(`  ${aliases[s]} : ${s}`);
  }
  lines.push(`  [*] --> ${aliases[states[0]]}`);
  for (const [key, count] of transitions.entries()) {
    const [from, to] = key.split('|');
    const label = count > 1 ? ` : ×${count}` : '';
    lines.push(`  ${aliases[from]} --> ${aliases[to]}${label}`);
  }
  lines.push(`  ${aliases[states[states.length-1]]} --> [*]`);
  return lines.join('\n');
}

// =============================================================
// WARNINGS VIEW
// =============================================================
function renderWarnings() {
  const body = $('#warningsBody');
  clear(body);
  const items = state.diagnostics?.log_warnings?.items || [];

  let filtered = items;
  if (state.warningsLevel === 'WRN')
    filtered = filtered.filter(i => /WRN|WARN/i.test(i.level));
  else if (state.warningsLevel === 'ERR')
    filtered = filtered.filter(i => /ERR|FATAL|CRITICAL/i.test(i.level));

  if (state.search) {
    const q = state.search.toLowerCase();
    filtered = filtered.filter(i =>
      [i.message, i.component, i.module, i.level].some(s => (s || '').toLowerCase().includes(q))
    );
  }

  if (!filtered.length) {
    body.appendChild(emptyMsg('No warnings', 'Nothing matches the current filter.', iconBolt()));
    return;
  }

  const wrap = el('div', { class: 'warnings-table' });
  filtered.forEach(item => wrap.appendChild(buildWarningRow(item)));
  body.appendChild(wrap);
}

function buildWarningRow(item) {
  const row = el('button', {
    class: 'warning-row',
    'data-level': item.level,
    onclick: () => openWarningDrawer(item),
    style: { display: 'grid', textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit', color: 'inherit' },
  });
  row.innerHTML = `
    <span class="warning-level">${escapeHtml(item.level)}</span>
    <span class="warning-count">×${item.count}</span>
    <span class="warning-msg">${escapeHtml(item.message)}</span>
    <span><span class="warning-comp">${escapeHtml(item.component || '?')}</span> <span class="warning-mod">(${escapeHtml(item.module || '?')})</span></span>
  `;
  return row;
}

function openWarningDrawer(item) {
  const header = $('#drawerHeader');
  header.innerHTML = `
    <button class="drawer-close" onclick="window.__viewer.closeDrawer()">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
    <h3><span class="warning-level">${escapeHtml(item.level)}</span> ${escapeHtml(item.module || '?')}</h3>
    <div class="drawer-sub">×${item.count} occurrences</div>
  `;

  const body = $('#drawerBody');
  body.innerHTML = '';
  body.appendChild(el('div', { class: 'drawer-section' },
    el('h4', {}, 'Message'),
    el('div', { class: 'kv-val', style: { whiteSpace: 'pre-wrap' } }, item.message),
  ));
  body.appendChild(el('div', { class: 'drawer-section' },
    el('h4', {}, 'Details'),
    el('div', { class: 'kv-list' },
      kvRow('Level', item.level),
      kvRow('Module', item.module || '—'),
      kvRow('Component', item.component || '—'),
      item.sample_source && kvRow('Sample source', item.sample_source),
      kvRow('Count', item.count),
      item.first_ts && kvRow('First seen', fmtTime(item.first_ts)),
      item.last_ts && kvRow('Last seen', fmtTime(item.last_ts)),
    ),
  ));

  if (item.component && state.frames && state.frames[item.component]) {
    body.appendChild(el('div', { class: 'drawer-section' },
      el('h4', {}, 'Jump to'),
      el('button', {
        class: 'btn-primary',
        onclick: () => { closeDrawer(); setActiveTab('components'); setTimeout(() => openComponentDrawer(item.component), 250); },
      }, `Open ${item.component} →`),
    ));
  }

  openDrawer();
}

// =============================================================
// Drawer
// =============================================================
function openDrawer() {
  $('#drawer').classList.add('open');
  $('#drawerBackdrop').classList.add('open');
  $('#drawer').setAttribute('aria-hidden', 'false');
}
function closeDrawer() {
  $('#drawer').classList.remove('open');
  $('#drawerBackdrop').classList.remove('open');
  $('#drawer').setAttribute('aria-hidden', 'true');
}

// =============================================================
// Empty message helper
// =============================================================
function emptyMsg(title, sub, icon) {
  const node = el('div', { class: 'empty-msg' });
  if (icon) node.appendChild(icon);
  node.appendChild(el('h3', {}, title));
  node.appendChild(el('p', { html: sub }));
  return node;
}

// =============================================================
// Inline icons
// =============================================================
const _icon = (path) => () => {
  const s = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  const wrap = document.createElement('span');
  wrap.innerHTML = s;
  return wrap.firstChild;
};
const iconCheck = _icon('<polyline points="20 6 9 17 4 12"/>');
const iconAlert = _icon('<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>');
const iconFlow  = _icon('<polyline points="9 11 12 14 22 4"/>');
const iconCode  = _icon('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>');
const iconBolt  = _icon('<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>');
const iconClock = _icon('<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>');

// =============================================================
// Drag & drop
// =============================================================
function setupDragDrop() {
  const dz = $('#dropzone');
  let dragCounter = 0;

  ['dragenter', 'dragover'].forEach(evt =>
    document.addEventListener(evt, e => {
      e.preventDefault();
      e.stopPropagation();
      if (evt === 'dragenter') dragCounter++;
      dz?.classList.add('dragging');
    })
  );

  ['dragleave'].forEach(evt =>
    document.addEventListener(evt, e => {
      e.preventDefault();
      e.stopPropagation();
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        dz?.classList.remove('dragging');
      }
    })
  );

  document.addEventListener('drop', e => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter = 0;
    dz?.classList.remove('dragging');
    if (e.dataTransfer?.files?.length) {
      loadFiles(e.dataTransfer.files);
    }
  });
}

// =============================================================
// Search
// =============================================================
function setupSearch() {
  const input = $('#searchInput');
  let debounce;
  input.addEventListener('input', e => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.search = e.target.value.trim();
      renderActiveView();
    }, 120);
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      input.value = '';
      state.search = '';
      renderActiveView();
      input.blur();
    }
  });
}

// =============================================================
// Keyboard shortcuts
// =============================================================
function setupKeyboard() {
  document.addEventListener('keydown', e => {
    // Don't intercept while typing in fields
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
      if (e.key === 'Escape') e.target.blur();
      return;
    }

    if (e.key === '/') { e.preventDefault(); $('#searchInput').focus(); return; }
    if (e.key === 'Escape') { closeDrawer(); return; }
    if (e.key === 't' || e.key === 'T') { toggleTheme(); return; }
    if (e.key === 'l' || e.key === 'L') { $('#fileInput').click(); return; }

    const tabs = ['overview','issues','flows','components','warnings'];
    const num = parseInt(e.key, 10);
    if (num >= 1 && num <= tabs.length) {
      setActiveTab(tabs[num - 1]);
      return;
    }
  });
}

function toggleTheme() {
  document.documentElement.classList.toggle('light');
  document.documentElement.classList.toggle('dark');
  if (window.mermaid) {
    const isLight = document.documentElement.classList.contains('light');
    mermaid.initialize({
      startOnLoad: false,
      theme: isLight ? 'default' : 'dark',
      securityLevel: 'loose',
    });
  }
  // Re-render current view to refresh mermaid if drawer is open
  renderActiveView();
}

// =============================================================
// Tab + filter wiring
// =============================================================
function setupTabsAndFilters() {
  $$('.tab').forEach(t => {
    t.addEventListener('click', () => setActiveTab(t.dataset.tab));
  });

  $$('[data-flows-filter]').forEach(b => {
    b.addEventListener('click', () => {
      state.flowFilter = b.dataset.flowsFilter;
      $$('[data-flows-filter]').forEach(x => x.classList.toggle('active', x === b));
      renderFlows();
    });
  });

  $$('[data-issues-filter]').forEach(b => {
    b.addEventListener('click', () => {
      state.issuesFilter = b.dataset.issuesFilter;
      $$('[data-issues-filter]').forEach(x => x.classList.toggle('active', x === b));
      renderIssues();
    });
  });

  $$('[data-components-sort]').forEach(b => {
    b.addEventListener('click', () => {
      state.componentsSort = b.dataset.componentsSort;
      $$('[data-components-sort]').forEach(x => x.classList.toggle('active', x === b));
      renderComponents();
    });
  });

  $$('[data-warnings-level]').forEach(b => {
    b.addEventListener('click', () => {
      state.warningsLevel = b.dataset.warningsLevel;
      $$('[data-warnings-level]').forEach(x => x.classList.toggle('active', x === b));
      renderWarnings();
    });
  });
}

// =============================================================
// Init
// =============================================================
function init() {
  $('#loadBtn').addEventListener('click', () => $('#fileInput').click());
  $('#emptyLoadBtn').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', e => loadFiles(e.target.files));
  $('#themeToggle').addEventListener('click', toggleTheme);
  $('#searchTrigger').addEventListener('click', () => $('#searchInput').focus());
  $('#drawerBackdrop').addEventListener('click', closeDrawer);

  setupDragDrop();
  setupSearch();
  setupKeyboard();
  setupTabsAndFilters();

  // Initial state
  document.body.classList.add('empty');
  $('#app').classList.add('empty');
  setActiveTab('flows'); // default landing tab once data is loaded
  state.activeTab = 'overview'; // but set internal default to overview

  // Try auto-load via ?file= and ?frames=
  const params = new URLSearchParams(window.location.search);
  const f1 = params.get('file');
  const f2 = params.get('frames');
  if (f1) {
    fetch(f1).then(r => r.json()).then(d => {
      if (detectKind(d)) {
        if (detectKind(d) === 'diagnostics') state.diagnostics = d;
        else if (detectKind(d) === 'frames') state.frames = d;
      }
      if (f2) return fetch(f2).then(r => r.json()).then(d2 => {
        const k = detectKind(d2);
        if (k === 'frames') state.frames = d2;
        else if (k === 'diagnostics') state.diagnostics = d2;
      });
    }).then(() => {
      if (state.diagnostics) {
        document.body.classList.remove('empty');
        $('#app').classList.remove('empty');
        $('#emptyState').classList.add('hidden');
        refreshAll();
        setActiveTab('overview');
      }
    }).catch(err => toast(`Auto-load failed: ${err.message}`, 'warning'));
  }
}

// Expose minimal API for inline-button onclick handlers
window.__viewer = { closeDrawer, setActiveTab, openComponentDrawer, openFlowDrawer, openIssueDrawer, openWarningDrawer };

document.addEventListener('DOMContentLoaded', init);
})();
