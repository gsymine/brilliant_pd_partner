/* Brilliant PD Partner — web UI for the backend API (served by the backend at "/").
   Plain JS, no build step. Polls the backend every few seconds while a run is active. */

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
async function api(path, opts = {}) {
  const res = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(typeof data.detail === 'string' ? data.detail : res.statusText); e.status = res.status; throw e; }
  return data;
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600);
}
const time = ts => ts ? new Date(ts).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '';
const usd = v => v == null ? '–' : '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
const val = v => Array.isArray(v) ? v.join(', ') : (v && typeof v === 'object') ? JSON.stringify(v) : v;
function elapsed(from, to) {
  const s = Math.max(0, Math.round((new Date(to || Date.now()) - new Date(from)) / 1000));
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

const RUN_STEPS = [['outreach', 'Talking to vendors'], ['extracting', 'Extracting products'], ['analyzing', 'Scoring & ranking'],
  ['awaiting_decision', 'Your decision'], ['decided', 'Decided']];
const ACTIVE = ['outreach', 'extracting', 'analyzing'];
const THREAD = {
  new:              ['p-grey', 'Preparing'],
  awaiting_reply:   ['p-blue', 'Waiting for vendor'],
  processing_reply: ['p-warn', 'Agent reading reply'],
  complete:         ['p-good', 'Complete'],
  declined:         ['p-bad', 'Declined'],
  stalled:          ['p-grey', 'Stalled'],
};
const tPill = s => `<span class="pill ${(THREAD[s] || ['p-grey'])[0]}">${(THREAD[s] || [0, s])[1]}</span>`;
const EVENT_ICON = {run_started: '🚀', email_out: '📤', email_in: '📥', facts_saved: '🧠', thread_closed: '✅', gate_open: '🚪',
  offers_saved: '📦', report_saved: '📊', ready_for_decision: '⭐', decision: '🏁', error: '⚠️', agent_run_failed: '⚠️'};

// ------------------------------------------------------------------ state
const state = {
  runId: localStorage.getItem('runId'),
  run: null,
  runs: [],
  selected: null,          // Set of offer ids chosen on the decision page
  selectedFor: null,       // run id the selection belongs to
  notes: '',
  seenEvents: new Set(),
  lastHtml: '',
};

// ------------------------------------------------------------------ pages
const pages = {};

pages.start = async () => {
  const [vendors, demo] = await Promise.all([api('/sim/vendors').catch(() => []), api('/demo/brief').catch(() => null)]);
  const replay = demo && demo.dataset === 'brightwell';
  return `
  <div class="start-hero">
    <div class="sc-kicker">New sourcing run</div>
    <h1>Start a sourcing run</h1>
    <p>The agents email every vendor, chase missing information, extract and score the products, and come back to you with a recommendation.</p>
  </div>
  <div class="grid g2" style="align-items:start">
    <div class="card start-card">
      <div class="pgroup-head" style="margin:0 0 12px"><h2>Vendors (${vendors.length})</h2></div>
      <div class="svlist">${vendors.map(v => `<div class="svitem"><span class="avatar">${esc(initials(v.name))}</span>
          <div style="min-width:0;flex:1"><b>${esc(v.name)}</b><div class="muted small sv-mail">${esc(v.email)}</div></div>
          <span class="pc-item">${esc(v.country)}</span></div>`).join('')
        || '<div class="muted">Vendor simulator is off (SIM_MODE=off).</div>'}</div>
    </div>
    <div class="card start-card brief-card">
      <h2>Brief</h2>
      ${demo ? `<div class="brief-title">${esc(demo.title)}</div>
        <div class="brief-items">${demo.brief_items.map(i => `<div class="brief-item"><b>${esc(i.name)}</b>${i.requirements ? `<span class="muted"> · ${esc(i.requirements)}</span>` : ''}</div>`).join('')}</div>
        ${demo.required_fields ? `<div class="brief-fields">📋 Asks every vendor for <b>${demo.required_fields.length} fields</b> per product.</div>` : ''}` : ''}
      <div class="real-vendor">
        <label class="lbl" style="margin-top:0">Optional: add a real vendor (they reply from a real inbox)</label>
        <div class="grid" style="grid-template-columns:1fr 1.4fr 70px;gap:6px">
          <input type="text" id="rvName" placeholder="Name"><input type="text" id="rvEmail" placeholder="email@example.com"><input type="text" id="rvCountry" placeholder="CN">
        </div>
        <div class="muted small" style="margin-top:4px">Needs EMAIL_MODE=smtp and IMAP settings on the backend.</div>
      </div>
      <button class="btn primary big go-btn" id="go">🚀 Start sourcing</button>
    </div>
  </div>`;
};
pages.start.after = () => {
  $('#go').onclick = async () => {
    const name = $('#rvName').value.trim(), email = $('#rvEmail').value.trim();
    const body = {extra_vendors: email ? [{name: name || email, email, country: $('#rvCountry').value.trim() || null}] : []};
    $('#go').disabled = true; $('#go').textContent = 'Starting…';
    try {
      const run = await api('/demo/start', {method: 'POST', body});
      setRun(run.id); location.hash = 'mission'; toast('Run started — agents are emailing vendors');
    } catch (e) { toast('⚠ ' + e.message); $('#go').disabled = false; $('#go').textContent = '🚀 Start sourcing'; }
  };
};

function stepper(run) {
  const st = ['accepted', 'denied_all'].includes(run.status) ? 'decided' : run.status;
  const idx = RUN_STEPS.findIndex(s => s[0] === st);
  return `<div class="stepper">${RUN_STEPS.map(([k, label], i) => {
    const cls = run.status === 'needs_attention' && i === 1 ? 'fail' : i < idx ? 'done' : i === idx ? (k === 'decided' ? 'done' : 'now') : '';
    return `<div class="${cls}">${i + 1} · ${label}</div>`;
  }).join('')}</div>`;
}
function runHeader(run, events) {
  const last = events.length ? events[events.length - 1].created_at : null;
  const live = ACTIVE.includes(run.status);
  return `<header class="top">
    <div><h1>${esc(run.title)}</h1>
      <div class="sub">${live ? '<span class="live">LIVE</span> · ' : ''}${run.threads.length} vendors · started ${time(run.created_at)} ·
        ${live ? 'running for ' + elapsed(run.created_at) : 'took ' + elapsed(run.created_at, last)}</div></div>
    ${run.status === 'outreach' ? '<button class="btn" id="closeOutreach" title="Stop waiting for vendors and analyse what we have">Stop waiting &amp; analyse now</button>' : ''}
  </header>
  <div class="card" style="margin-bottom:14px">${stepper(run)}</div>
  ${run.status === 'awaiting_decision' ? `<div class="callout" style="margin-bottom:14px">⭐ <b>Analysis ready.</b> The agents finished; your decision is the only step left. <a href="#report">Review &amp; decide →</a></div>` : ''}
  ${run.status === 'needs_attention' ? `<div class="callout warn row" style="margin-bottom:14px"><span>⚠️ <b>An agent step failed:</b> ${esc(run.error)}</span><span class="spacer"></span><button class="btn sm" id="retry">Retry extraction &amp; analysis</button></div>` : ''}`;
}
function bindRunHeader(root) {
  const c = $('#closeOutreach', root);
  if (c) c.onclick = () => confirm('Stop waiting? Open vendor conversations will be marked stalled and analysis starts now.') &&
    api(`/runs/${state.runId}/close-outreach`, {method: 'POST'}).then(() => toast('Analysis started'), e => toast('⚠ ' + e.message));
  const r = $('#retry', root);
  if (r) r.onclick = () => api(`/runs/${state.runId}/retry`, {method: 'POST'}).then(() => toast('Retrying'), e => toast('⚠ ' + e.message));
}

function eventText(e, run) {
  return esc(e.message).replace(/\[PD-[0-9a-f]+\]/gi, '').trim();
}

pages.mission = async () => {
  if (!state.runId) return noRun();
  const [run, events] = await Promise.all([api(`/runs/${state.runId}`), api(`/runs/${state.runId}/events?limit=500`)]);
  state.run = run;
  events.sort((a, b) => a.id - b.id);
  const count = k => events.filter(e => e.kind === k).length;
  const followups = events.filter(e => e.kind === 'email_out' && /^(follow_up|reminder|clarification)/.test(e.message)).length;
  const closed = run.threads.filter(t => ['complete', 'declined', 'stalled'].includes(t.status)).length;
  const lastActivity = {};
  events.forEach(e => { if (e.thread_id) lastActivity[e.thread_id] = e; });
  const feed = events.filter(e => e.kind !== 'facts_saved' || true).slice(-60).reverse();
  const fresh = new Set(feed.filter(e => !state.seenEvents.has(e.id)).map(e => e.id));
  feed.forEach(e => state.seenEvents.add(e.id));
  return `${runHeader(run, events)}
  <div class="grid g4" style="margin-bottom:22px">
    <div class="stat-tile t-teal"><span class="st-ico">✅</span><div><div class="v">${closed} / ${run.threads.length}</div><div class="l">Vendor conversations closed</div></div></div>
    <div class="stat-tile t-violet"><span class="st-ico">📤</span><div><div class="v">${count('email_out')}</div><div class="l">Emails sent by the agent</div></div></div>
    <div class="stat-tile t-blue"><span class="st-ico">📥</span><div><div class="v">${count('email_in')}</div><div class="l">Vendor replies read</div></div></div>
    <div class="stat-tile t-amber"><span class="st-ico">🔔</span><div><div class="v">${followups}</div><div class="l">Follow-ups &amp; reminders (automatic)</div></div></div>
  </div>
  <div class="grid g-main" style="align-items:start">
    <div>
      <div class="pgroup-head"><h2>Vendors</h2><span class="muted small">${run.threads.length} conversations</span></div>
      <div class="vgrid">${run.threads.map(t => {
        const le = lastActivity[t.id];
        return `<div class="vcard st-${esc(t.status)} ${t.status === 'processing_reply' ? 'pulse' : ''}" data-tid="${t.id}">
          <div class="vc-top"><span class="avatar">${esc(initials(t.vendor_name))}</span>
            <div class="vc-name"><span class="nm">${esc(t.vendor_name)}</span><span class="muted small">${esc(t.country || '')}</span></div></div>
          <div style="margin-top:10px">${tPill(t.status)}</div>
          <div class="meta"><span class="chip-sm">📤 ${t.outbound_count}</span><span class="chip-sm">🔔 ${t.reminders_sent}</span>${t.last_inbound_at ? `<span class="chip-sm">📥 ${time(t.last_inbound_at)}</span>` : ''}</div>
          ${t.close_reason ? `<div class="muted small" style="margin-top:6px">${esc(t.close_reason).slice(0, 110)}</div>`
            : le ? `<div class="muted small" style="margin-top:6px">${eventText(le).slice(0, 90)}</div>` : ''}
        </div>`;
      }).join('')}</div>
    </div>
    <div class="card activity-card">
      <div class="row"><h2 style="margin:0">🤖 Agent activity</h2><span class="spacer"></span>${ACTIVE.includes(run.status) ? '<span class="live">LIVE</span>' : ''}</div>
      <ul class="feed timeline" style="max-height:560px;overflow:auto;margin-top:10px">${feed.map(e =>
        `<li class="${fresh.has(e.id) && state.lastHtml ? 'new' : ''}"><span class="t">${time(e.created_at)}</span><span class="ico">${EVENT_ICON[e.kind] || '•'}</span><div>${eventText(e)}</div></li>`).join('')}</ul>
    </div>
  </div>`;
};
pages.mission.after = root => {
  bindRunHeader(root);
  root.querySelectorAll('.vcard').forEach(c => c.onclick = () => { location.hash = 'vendors/' + c.dataset.tid; });
};

pages.vendors = async (tid) => {
  if (!state.runId) return noRun();
  const run = state.run = await api(`/runs/${state.runId}`);
  tid = tid || run.threads[0]?.id;
  const t = tid ? await api(`/runs/${state.runId}/threads/${tid}`) : null;
  const req = run.required_fields || [];
  return `<header class="top"><div><h1>Vendor conversations</h1><div class="sub">Every email the agent sent and received, and what it saved to Vendor Memory (with the source quote).</div></div></header>
  <div class="grid" style="grid-template-columns:250px 1fr;align-items:start">
    <div class="vlist">${run.threads.map(x => `<div class="vlist-item click ${x.id === tid ? 'sel' : ''}" data-tid="${x.id}">
      <span class="avatar">${esc(initials(x.vendor_name))}</span>
      <div style="min-width:0"><b>${esc(x.vendor_name)}</b><div style="margin-top:4px">${tPill(x.status)}</div></div></div>`).join('')}</div>
    ${t ? `<div class="grid g2" style="align-items:start">
      <div class="card convo-card">
        <div class="convo-head"><span class="avatar lg">${esc(initials(t.vendor_name))}</span>
          <div style="min-width:0;flex:1"><div class="row"><h2 style="margin:0">${esc(t.vendor_name)}</h2>${tPill(t.status)}</div>
          <div class="muted small" style="margin-top:2px">${esc(t.vendor_email)}${t.close_reason ? ' · ' + esc(t.close_reason) : ''}</div></div></div>
        ${t.emails.map(e => `<div class="bubble ${e.direction}"><div class="h">${e.direction === 'out' ? '📤 Agent → vendor' : '📥 Vendor → agent'} · ${time(e.created_at)} · ${esc((e.subject || '').replace(/\[PD-[0-9a-f]+\]/gi, ''))}</div>${esc(e.body)}</div>`).join('')
          || '<div class="empty">No emails yet.</div>'}
      </div>
      <div class="card memory-card">
        <h2>🧠 Vendor Memory</h2>
        ${(t.memory.products || []).map(p => `<div class="mem-product ${p.missing_fields?.length ? 'gaps' : 'full'}">
          <div class="row"><b>${esc(p.product_ref)}</b><span class="spacer"></span>${p.missing_fields?.length ? `<span class="pill p-warn">${p.missing_fields.length} missing</span>` : '<span class="pill p-good">all fields</span>'}</div>
          ${req.map(f => { const x = p.fields?.[f]; return `<div class="factrow"><span class="muted">${esc(f)}</span>
            <span>${x ? `${esc(val(x.value))}${x.quote ? `<div class="q">“${esc(x.quote).slice(0, 120)}”</div>` : ''}` : '<span class="pill p-bad">missing</span>'}</span></div>`; }).join('')}
        </div>`).join('') || '<div class="empty">Nothing saved yet.</div>'}
      </div></div>` : '<div class="empty">No vendors.</div>'}
  </div>`;
};
pages.vendors.after = root => root.querySelectorAll('[data-tid]').forEach(tr => tr.onclick = () => { location.hash = 'vendors/' + tr.dataset.tid; });

pages.offers = async () => {
  if (!state.runId) return noRun();
  const [run, offers] = await Promise.all([api(`/runs/${state.runId}`), api(`/runs/${state.runId}/offers`)]);
  state.run = run;
  const vname = Object.fromEntries(run.threads.map(t => [t.id, t.vendor_name]));
  const items = Object.fromEntries((run.brief_items || []).map(i => [i.sku, i.name]));
  return `<header class="top"><div><h1>Products</h1><div class="sub">One row per product a vendor proposed, extracted by the Extraction Agent from the email threads.</div></div></header>
  ${offers.length ? productGroups(offers, vname, items)
    : `<div class="card empty">${ACTIVE.includes(run.status) && run.status === 'outreach'
        ? 'Products appear after every vendor conversation closes and the Extraction Agent runs. Follow progress in <a href="#mission">Mission control</a>.'
        : 'No products extracted yet.'}</div>`}`;
};

// Products page: one card per offer, grouped by brief item (same fields as the old table).
const PATH_STYLE = {accept: 'path-accept', modify: 'path-modify', co_develop: 'path-codev'};
function splitCode(name) {  // "TK3K53CTC Golden Doodle dog travel cup" -> ["TK3K53CTC", "Golden Doodle dog travel cup"]
  const m = String(name || '').match(/^([A-Z0-9][A-Z0-9-]{3,})\s*[—–-]?\s+(.+)$/);
  return m ? [m[1], m[2]] : ['', name || ''];
}
const initials = s => String(s || '?').split(/\s+/).filter(w => /^[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0].toUpperCase()).join('');
function productCard(o, vname, items) {
  const [code, name] = splitCode(o.product_name);
  const vendor = vname[o.thread_id] || '';
  const issues = o.spec_issues || [];
  return `<article class="pcard ${o.meets_spec ? 'ok' : 'fail'}">
    <div class="pc-head">
      ${code ? `<span class="pc-code">${esc(code)}</span>` : ''}
      <span class="spacer"></span>
      ${o.meets_spec ? '<span class="pill p-good">✓ meets spec</span>' : '<span class="pill p-bad">✕ fails spec</span>'}
    </div>
    <h3 class="pc-name">${esc(name)}</h3>
    ${o.design ? `<p class="pc-desc">${esc(o.design).slice(0, 90)}</p>` : ''}
    <div class="pc-vendor"><span class="avatar">${esc(initials(vendor))}</span>
      <div><b>${esc(vendor)}</b><div class="muted small">${esc(o.country || '')}</div></div>
      <span class="spacer"></span><span class="pc-item" title="${esc(items[o.brief_item] || '')}">${esc(o.brief_item)}</span></div>
    <div class="pc-stats">
      <div><span class="l">Unit price</span><span class="v">${o.unit_price != null ? `<small>${esc(o.currency || '')}</small> ${o.unit_price}` : '–'}</span><span class="s">${esc(o.incoterm || '')}</span></div>
      <div><span class="l">MOQ</span><span class="v">${o.moq != null ? Number(o.moq).toLocaleString() : '–'}</span><span class="s">${o.moq != null ? 'pcs' : ''}</span></div>
      <div><span class="l">Lead time</span><span class="v">${o.production_days != null ? o.production_days : '–'}</span><span class="s">${o.production_days != null ? 'days' : ''}</span></div>
    </div>
    <div class="pc-foot">
      <span class="pathpill ${PATH_STYLE[o.path] || ''}">${esc(o.path || '–')}</span>
      ${issues.length ? `<details class="pc-issues"><summary>⚠ ${issues.length} issue${issues.length > 1 ? 's' : ''}</summary>
        <ul>${issues.map(i => `<li>${esc(i)}</li>`).join('')}</ul></details>` : '<span class="muted small">No issues</span>'}
    </div>
  </article>`;
}
function productGroups(offers, vname, items) {
  const groups = {};
  offers.forEach(o => (groups[o.brief_item] = groups[o.brief_item] || []).push(o));
  return Object.entries(groups).map(([sku, list]) => `<section class="pgroup">
    <div class="pgroup-head"><h2>${esc(items[sku] || sku)}</h2><span class="pc-item">${esc(sku)}</span>
      <span class="muted small">${list.length} product${list.length > 1 ? 's' : ''}</span></div>
    <div class="pgrid">${list.map(o => productCard(o, vname, items)).join('')}</div></section>`).join('');
}

pages.report = async () => {
  if (!state.runId) return noRun();
  const run = state.run = await api(`/runs/${state.runId}`);
  let report = null;
  try { report = await api(`/runs/${state.runId}/report`); } catch (e) { if (e.status !== 404) throw e; }
  if (!report) return `<header class="top"><div><h1>Analysis &amp; decision</h1></div></header>
    <div class="card empty">The Analysis Agent hasn't finished yet (run status: <b>${esc(run.status)}</b>). Follow it live in <a href="#mission">Mission control</a>.</div>`;
  const byId = Object.fromEntries(report.offers.map(o => [o.offer_id, o]));
  const rec = new Set(report.recommended_offer_ids || []);
  if (state.selectedFor !== run.id) { state.selected = new Set(rec); state.selectedFor = run.id; }
  const decided = run.decision;
  const items = Object.fromEntries((run.brief_items || []).map(i => [i.sku, i.name]));
  const group = (sku, ids) => {
    const list = ids.map(id => byId[id]).filter(Boolean);
    return `<section class="pgroup">
      <div class="pgroup-head"><h2>${esc(items[sku] || sku)}</h2><span class="pc-item">${esc(sku)}</span>
        <span class="muted small">${list.length} product${list.length > 1 ? 's' : ''} · ranked</span></div>
      <div class="pgrid rgrid">${list.map(o => rankCard(o, (report.images || {})[o.offer_id], rec.has(o.offer_id), !decided)).join('')}</div></section>`;
  };
  return `<header class="top"><div><h1>Analysis &amp; decision</h1><div class="sub">Ranked by risk-adjusted cost per unit sold (landed cost + excess-MOQ cost + late-delivery margin loss). Weights: ${Object.entries(report.weights || {}).map(([k, v]) => `${esc(k)} ${v}`).join(' · ')}</div></div></header>
  <div class="summary-card"><div class="sc-kicker">🤖 Analysis Agent</div><h2>Agent's summary</h2><ul>${(report.executive_summary || []).map(s => `<li>${esc(s)}</li>`).join('')}</ul></div>
  ${Object.entries(report.ranking_by_item || {}).map(([sku, ids]) => group(sku, ids)).join('')}
  <div class="grid g2" style="align-items:start">
    <div class="card risk-card"><h2>⚠️ Risks &amp; open questions</h2><ul class="small" style="margin:0;padding-left:18px">${(report.risks_and_open_questions || []).map(r => `<li style="margin-bottom:4px">${esc(r)}</li>`).join('')}</ul></div>
    <div class="card decision-card">
      <h2>🏁 Your decision</h2>
      ${decided ? `<div class="callout">🏁 <b>${decided.outcome === 'accepted' ? `Accepted ${decided.accepted_offer_ids.length} product(s)` : 'Denied all'}</b>
          ${decided.accepted_offer_ids.map(id => `<div>• ${esc(byId[id]?.product_name || id)} — ${esc(byId[id]?.vendor || '')}</div>`).join('')}
          ${decided.notes ? `<div class="muted small" style="margin-top:6px">${esc(decided.notes)}</div>` : ''}</div>`
        : run.status !== 'awaiting_decision' ? `<div class="muted">Decisions open when the run reaches “Your decision”.</div>`
        : `<div class="small muted">Pre-selected: the agent's recommendation. You can pick any products from any vendors.</div>
          <textarea id="notes" placeholder="Notes (optional)" style="min-height:70px;margin-top:8px">${esc(state.notes)}</textarea>
          <div class="row" style="margin-top:10px"><button class="btn" id="denyAll">Deny all</button><span class="spacer"></span>
            <button class="btn primary" id="accept">Accept ${state.selected.size} selected</button></div>`}
    </div>
  </div>`;
};
pages.report.after = root => {
  root.querySelectorAll('input[data-oid]').forEach(cb => cb.onchange = () => {
    cb.checked ? state.selected.add(cb.dataset.oid) : state.selected.delete(cb.dataset.oid);
    cb.closest('.rcard')?.classList.toggle('sel', cb.checked);
    const b = $('#accept', root); if (b) b.textContent = `Accept ${state.selected.size} selected`;
  });
  const n = $('#notes', root); if (n) n.oninput = () => { state.notes = n.value; };
  const decide = async body => {
    try { await api(`/runs/${state.runId}/decision`, {method: 'POST', body: {...body, notes: state.notes || null, decided_by: 'web'}}); toast('Decision saved'); render(true); }
    catch (e) { toast('⚠ ' + e.message); }
  };
  const a = $('#accept', root);
  if (a) a.onclick = () => state.selected.size ? decide({outcome: 'accepted', accepted_offer_ids: [...state.selected]}) : toast('Select at least one product');
  const d = $('#denyAll', root);
  if (d) d.onclick = () => confirm('Deny all products in this run?') && decide({outcome: 'denied_all', accepted_offer_ids: []});
};

// Score colour: high (>= 75) green, middle (50-74) yellow, low (< 50) red.
const scoreBand = v => v == null ? 'band-none' : v >= 75 ? 'band-hi' : v >= 50 ? 'band-mid' : 'band-lo';
function rankCard(o, photos, recommended, selectable) {
  const [code, name] = splitCode(o.product_name);
  const sel = state.selected && state.selected.has(o.offer_id);
  const risks = o.assessment?.risks || [], flags = o.flags || [];
  const photo = photos && photos.length
    ? `<a href="${esc(photos[0])}" target="_blank" rel="noopener" class="rc-photo"><img src="${esc(photos[0])}" alt="Product photo" loading="lazy"></a>`
    : '<div class="rc-photo none"><span>No photo from the vendor</span></div>';
  return `<article class="pcard rcard ${o.meets_spec ? 'ok' : 'fail'} ${recommended ? 'is-rec' : ''} ${sel ? 'sel' : ''}">
    <div class="rc-media">${photo}
      <span class="rank-badge">#${o.rank_in_item ?? '–'}</span>
      ${recommended ? '<span class="rec-ribbon" title="Recommended by the Analysis Agent">★ Recommended</span>' : ''}
      ${photos && photos.length > 1 ? `<span class="photo-count">+${photos.length - 1} photo${photos.length > 2 ? 's' : ''}</span>` : ''}
    </div>
    <div class="pc-head">${code ? `<span class="pc-code">${esc(code)}</span>` : ''}<span class="spacer"></span>
      ${o.meets_spec ? '<span class="pill p-good">✓ meets</span>' : '<span class="pill p-bad">✕ fails</span>'}</div>
    <div class="rc-title"><h3 class="pc-name">${esc(name)}</h3>
      <div class="ring ${scoreBand(o.score)}" style="--p:${Math.max(0, Math.min(100, Number(o.score) || 0))}" title="Score: ${o.score ?? '–'} (green ≥ 75 · yellow 50–74 · red < 50)"><span>${o.score ?? '–'}</span></div></div>
    <div class="pc-vendor"><span class="avatar">${esc(initials(o.vendor))}</span><div><b>${esc(o.vendor)}</b></div></div>
    <div class="pc-stats rc-stats">
      <div><span class="l">Landed</span><span class="v">${usd(o.landed_usd)}</span></div>
      <div class="hl"><span class="l">Cost / sold unit</span><span class="v">${usd(o.cost_per_sold_unit)}</span></div>
      <div><span class="l">Excess $</span><span class="v">${o.excess_cost_usd ? usd(o.excess_cost_usd) : '–'}</span></div>
      <div><span class="l">Days late</span><span class="v">${o.days_late ?? '–'}</span></div>
    </div>
    <details class="rc-why"><summary>Why this ranking</summary>
      <div class="small" style="margin-top:6px">${esc(o.assessment?.rationale || '')}</div>
      ${risks.length ? `<ul class="small">${risks.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      ${flags.length ? `<div class="small muted">Flags: ${flags.map(esc).join(' · ')}</div>` : ''}</details>
    ${selectable ? `<label class="accept-toggle"><input type="checkbox" data-oid="${o.offer_id}" ${sel ? 'checked' : ''}><span>Accept this product</span></label>` : ''}
  </article>`;
}

function thumbs(urls) {
  if (!urls || !urls.length) return '<div class="thumb none" title="The vendor sent no photo for this product">no photo</div>';
  return `<div class="thumbs">${urls.map(u => `<a href="${esc(u)}" target="_blank" rel="noopener"><img class="thumb" src="${esc(u)}" alt="Product photo" loading="lazy"></a>`).join('')}</div>`;
}

function noRun() {
  return `<div class="hero"><h1>No sourcing run yet</h1><p class="sub">Start one and watch the agents work.</p>
    <a class="btn primary big" href="#start">🚀 Start sourcing</a></div>`;
}

// ------------------------------------------------------------------ shell
function setRun(id) {
  state.runId = id; localStorage.setItem('runId', id);
  state.seenEvents = new Set(); state.lastHtml = '';
}
async function refreshRuns() {
  state.runs = (await api('/runs').catch(() => [])).sort((x, y) => x.created_at.localeCompare(y.created_at));
  if (state.runId && !state.runs.some(r => r.id === state.runId)) state.runId = null;
  if (!state.runId && state.runs.length) setRun(state.runs[state.runs.length - 1].id);
  const sel = $('#runPick');
  sel.innerHTML = state.runs.length ? [...state.runs].reverse().map(r =>
    `<option value="${r.id}" ${r.id === state.runId ? 'selected' : ''}>${esc(r.title.slice(0, 28))} · ${time(r.created_at)} · ${esc(r.status)}</option>`).join('')
    : '<option>No runs yet</option>';
  const cur = state.runs.find(r => r.id === state.runId);
  $('#bDecide').textContent = cur && cur.status === 'awaiting_decision' ? '1' : '';
}
let rendering = false;
async function render(force = false) {
  if (rendering) return;
  rendering = true;
  const [page, arg] = (location.hash.slice(1) || 'mission').split('/');
  const fn = pages[page] || pages.mission;
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + page));
  const main = $('#main');
  try {
    const html = await fn(arg);
    if (force || html !== state.lastHtml) {      // only touch the DOM when something changed
      const scrollers = [...main.querySelectorAll('.feed')].map(f => f.scrollTop);
      main.innerHTML = html; state.lastHtml = html;
      main.querySelectorAll('.feed').forEach((f, i) => { f.scrollTop = scrollers[i] || 0; });
      fn.after?.(main);
    }
  } catch (e) {
    main.innerHTML = `<div class="callout warn">Couldn't load this page: ${esc(e.message)}. Is the backend running?</div>`;
    state.lastHtml = '';
  } finally { rendering = false; }
}
$('#runPick').onchange = e => { setRun(e.target.value); state.selectedFor = null; render(true); };
window.addEventListener('hashchange', () => { state.lastHtml = ''; window.scrollTo(0, 0); render(true); });

async function poll() {
  await refreshRuns();
  const page = (location.hash.slice(1) || 'mission').split('/')[0];
  const cur = state.runs.find(r => r.id === state.runId);
  // Live pages refresh while the run is moving; the start page and finished runs stay still.
  if (page !== 'start' && (!cur || ACTIVE.includes(cur.status) || page === 'mission')) await render();
}
(async () => {
  const h = await api('/health').catch(() => null);
  $('#health').innerHTML = h ? `Backend ✓ · email ${esc(h.email_mode)} · simulator ${esc(h.vendor_simulator?.mode || 'off')}` : 'Backend unreachable';
  await refreshRuns();
  if (!location.hash) location.hash = state.runId ? 'mission' : 'start';
  await render(true);
  setInterval(poll, 3000);
})();
