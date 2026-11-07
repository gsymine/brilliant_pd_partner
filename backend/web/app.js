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
  <div class="hero">
    <h1>Start a sourcing run</h1>
    <p class="sub">The agents email every vendor, chase missing information, extract and score the products, and come back to you with a recommendation.</p>
  </div>
  <div class="grid g2" style="align-items:start">
    <div class="card">
      <h2>Vendors (${vendors.length})</h2>
      <table><thead><tr><th>Vendor</th><th>Country</th></tr></thead><tbody>
        ${vendors.map(v => `<tr><td><b>${esc(v.name)}</b><div class="muted small">${esc(v.email)}</div></td><td>${esc(v.country)}</td></tr>`).join('')
          || '<tr><td colspan="2" class="muted">Vendor simulator is off (SIM_MODE=off).</td></tr>'}
      </tbody></table>
    </div>
    <div class="card">
      <h2>Brief</h2>
      ${demo ? `<div class="small"><b>${esc(demo.title)}</b><br>
        ${demo.brief_items.map(i => `${esc(i.name)}${i.requirements ? ` <span class="muted">· ${esc(i.requirements)}</span>` : ''}`).join('<br>')}</div>
        ${demo.required_fields ? `<div class="muted small" style="margin-top:6px">Asks every vendor for ${demo.required_fields.length} fields per product.</div>` : ''}` : ''}
      <label class="lbl">Optional: add a real vendor (they reply from a real inbox)</label>
      <div class="grid" style="grid-template-columns:1fr 1.4fr 70px;gap:6px">
        <input type="text" id="rvName" placeholder="Name"><input type="text" id="rvEmail" placeholder="email@example.com"><input type="text" id="rvCountry" placeholder="CN">
      </div>
      <div class="muted small" style="margin-top:4px">Needs EMAIL_MODE=smtp and IMAP settings on the backend.</div>
      <div class="row" style="margin-top:18px"><span class="spacer"></span><button class="btn primary big" id="go">🚀 Start sourcing</button></div>
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
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card kpi"><div class="v">${closed} / ${run.threads.length}</div><div class="l">Vendor conversations closed</div></div>
    <div class="card kpi"><div class="v">${count('email_out')}</div><div class="l">Emails sent by the agent</div></div>
    <div class="card kpi"><div class="v">${count('email_in')}</div><div class="l">Vendor replies read</div></div>
    <div class="card kpi"><div class="v">${followups}</div><div class="l">Follow-ups &amp; reminders (automatic)</div></div>
  </div>
  <div class="grid g-main" style="align-items:start">
    <div>
      <h2>Vendors</h2>
      <div class="vgrid">${run.threads.map(t => {
        const le = lastActivity[t.id];
        return `<div class="vcard ${t.status === 'processing_reply' ? 'pulse' : ''}" data-tid="${t.id}">
          <div class="row"><span class="nm">${esc(t.vendor_name)}</span><span class="spacer"></span><span class="muted small">${esc(t.country || '')}</span></div>
          <div style="margin-top:6px">${tPill(t.status)}</div>
          <div class="meta"><span>📤 ${t.outbound_count}</span><span>🔔 ${t.reminders_sent}</span>${t.last_inbound_at ? `<span>📥 ${time(t.last_inbound_at)}</span>` : ''}</div>
          ${t.close_reason ? `<div class="muted small" style="margin-top:6px">${esc(t.close_reason).slice(0, 110)}</div>`
            : le ? `<div class="muted small" style="margin-top:6px">${eventText(le).slice(0, 90)}</div>` : ''}
        </div>`;
      }).join('')}</div>
    </div>
    <div class="card">
      <div class="row"><h2 style="margin:0">Agent activity</h2><span class="spacer"></span>${ACTIVE.includes(run.status) ? '<span class="live">LIVE</span>' : ''}</div>
      <ul class="feed" style="max-height:560px;overflow:auto;margin-top:8px">${feed.map(e =>
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
    <div class="card flush"><table><tbody>${run.threads.map(x => `<tr class="click ${x.id === tid ? 'sel' : ''}" data-tid="${x.id}">
      <td><b>${esc(x.vendor_name)}</b><div style="margin-top:4px">${tPill(x.status)}</div></td></tr>`).join('')}</tbody></table></div>
    ${t ? `<div class="grid g2" style="align-items:start">
      <div class="card">
        <div class="row"><h2 style="margin:0">${esc(t.vendor_name)}</h2>${tPill(t.status)}</div>
        <div class="muted small" style="margin:2px 0 8px">${esc(t.vendor_email)}${t.close_reason ? ' · ' + esc(t.close_reason) : ''}</div>
        ${t.emails.map(e => `<div class="bubble ${e.direction}"><div class="h">${e.direction === 'out' ? '📤 Agent → vendor' : '📥 Vendor → agent'} · ${time(e.created_at)} · ${esc((e.subject || '').replace(/\[PD-[0-9a-f]+\]/gi, ''))}</div>${esc(e.body)}</div>`).join('')
          || '<div class="empty">No emails yet.</div>'}
      </div>
      <div class="card">
        <h2>Vendor Memory</h2>
        ${(t.memory.products || []).map(p => `<div style="margin-bottom:14px">
          <div class="row"><b>${esc(p.product_ref)}</b><span class="spacer"></span>${p.missing_fields?.length ? `<span class="pill p-warn">${p.missing_fields.length} missing</span>` : '<span class="pill p-good">all fields</span>'}</div>
          ${req.map(f => { const x = p.fields?.[f]; return `<div class="factrow"><span class="muted">${esc(f)}</span>
            <span>${x ? `${esc(val(x.value))}${x.quote ? `<div class="q">“${esc(x.quote).slice(0, 120)}”</div>` : ''}` : '<span class="pill p-bad">missing</span>'}</span></div>`; }).join('')}
        </div>`).join('') || '<div class="empty">Nothing saved yet.</div>'}
      </div></div>` : '<div class="empty">No vendors.</div>'}
  </div>`;
};
pages.vendors.after = root => root.querySelectorAll('tr[data-tid]').forEach(tr => tr.onclick = () => { location.hash = 'vendors/' + tr.dataset.tid; });

pages.offers = async () => {
  if (!state.runId) return noRun();
  const [run, offers] = await Promise.all([api(`/runs/${state.runId}`), api(`/runs/${state.runId}/offers`)]);
  state.run = run;
  const vname = Object.fromEntries(run.threads.map(t => [t.id, t.vendor_name]));
  const items = Object.fromEntries((run.brief_items || []).map(i => [i.sku, i.name]));
  return `<header class="top"><div><h1>Products</h1><div class="sub">One row per product a vendor proposed, extracted by the Extraction Agent from the email threads.</div></div></header>
  ${offers.length ? `<div class="card flush scroll-x"><table>
    <thead><tr><th>Product</th><th>Vendor</th><th>Brief item</th><th class="right">Unit price</th><th class="right">MOQ</th><th class="right">Lead time</th><th>Path</th><th>Meets spec</th><th>Issues</th></tr></thead>
    <tbody>${offers.map(o => `<tr>
      <td><b>${esc(o.product_name)}</b><div class="muted small">${esc(o.design || '').slice(0, 90)}</div></td>
      <td>${esc(vname[o.thread_id] || '')}<div class="muted small">${esc(o.country || '')}</div></td>
      <td>${esc(o.brief_item)} <span class="muted small">${esc(items[o.brief_item] || '')}</span></td>
      <td class="right num">${o.unit_price != null ? esc(o.currency || '') + ' ' + o.unit_price : '–'}<div class="muted small">${esc(o.incoterm || '')}</div></td>
      <td class="right num">${o.moq != null ? Number(o.moq).toLocaleString() : '–'}</td>
      <td class="right num">${o.production_days != null ? o.production_days + ' d' : '–'}</td>
      <td>${esc(o.path || '')}</td>
      <td>${o.meets_spec ? '<span class="pill p-good">yes</span>' : '<span class="pill p-bad">no</span>'}</td>
      <td class="small">${(o.spec_issues || []).map(esc).join('<br>')}</td></tr>`).join('')}</tbody></table></div>`
    : `<div class="card empty">${ACTIVE.includes(run.status) && run.status === 'outreach'
        ? 'Products appear after every vendor conversation closes and the Extraction Agent runs. Follow progress in <a href="#mission">Mission control</a>.'
        : 'No products extracted yet.'}</div>`}`;
};

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
  const table = (sku, ids) => `<div class="card flush" style="margin-bottom:14px">
    <div style="padding:12px 14px"><h2 style="margin:0">${esc(sku)} · ${esc(items[sku] || '')}</h2></div>
    <div class="scroll-x"><table>
    <thead><tr>${decided ? '' : '<th>Accept</th>'}<th>#</th><th>Image</th><th>Product</th><th>Vendor</th><th class="right">Landed</th><th class="right">Cost / sold unit</th><th class="right">Excess $</th><th class="right">Days late</th><th>Spec</th><th class="right">Score</th></tr></thead>
    <tbody>${ids.map(id => byId[id]).filter(Boolean).map(o => `<tr>
      ${decided ? '' : `<td><input type="checkbox" data-oid="${o.offer_id}" ${state.selected.has(o.offer_id) ? 'checked' : ''}></td>`}
      <td>${o.rank_in_item ?? ''}</td>
      <td>${thumbs((report.images || {})[o.offer_id])}</td>
      <td><b>${esc(o.product_name)}</b> ${rec.has(o.offer_id) ? '<span class="rec" title="Recommended by the Analysis Agent">★ recommended</span>' : ''}
        <details><summary>Why</summary><div class="small" style="margin-top:6px">${esc(o.assessment?.rationale || '')}</div>
        ${(o.assessment?.risks || []).length ? `<ul class="small">${o.assessment.risks.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
        ${(o.flags || []).length ? `<div class="small muted">Flags: ${o.flags.map(esc).join(' · ')}</div>` : ''}</details></td>
      <td>${esc(o.vendor)}</td>
      <td class="right num">${usd(o.landed_usd)}</td>
      <td class="right num"><b>${usd(o.cost_per_sold_unit)}</b></td>
      <td class="right num">${o.excess_cost_usd ? usd(o.excess_cost_usd) : '–'}</td>
      <td class="right num">${o.days_late ?? '–'}</td>
      <td>${o.meets_spec ? '<span class="pill p-good">meets</span>' : '<span class="pill p-bad">fails</span>'}</td>
      <td class="right score">${o.score ?? '–'}</td></tr>`).join('')}</tbody></table></div></div>`;
  return `<header class="top"><div><h1>Analysis &amp; decision</h1><div class="sub">Ranked by risk-adjusted cost per unit sold (landed cost + excess-MOQ cost + late-delivery margin loss). Weights: ${Object.entries(report.weights || {}).map(([k, v]) => `${esc(k)} ${v}`).join(' · ')}</div></div></header>
  <div class="card" style="margin-bottom:14px"><h2>Agent's summary</h2><ul style="margin:0;padding-left:18px">${(report.executive_summary || []).map(s => `<li style="margin-bottom:6px">${esc(s)}</li>`).join('')}</ul></div>
  ${Object.entries(report.ranking_by_item || {}).map(([sku, ids]) => table(sku, ids)).join('')}
  <div class="grid g2" style="align-items:start">
    <div class="card"><h2>Risks &amp; open questions</h2><ul class="small" style="margin:0;padding-left:18px">${(report.risks_and_open_questions || []).map(r => `<li style="margin-bottom:4px">${esc(r)}</li>`).join('')}</ul></div>
    <div class="card">
      <h2>Your decision</h2>
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
