/* Brilliant PD Partner — frontend. Plain JS, hash routing, talks to the FastAPI JSON API. */

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, opts = {}) {
  const res = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2200);
}
async function act(fn, okMsg) {
  try { await fn(); if (okMsg) toast(okMsg); await refreshSidebar(); await render(); }
  catch (e) { toast('⚠ ' + e.message); }
}
const fmtTime = ts => ts ? new Date(ts).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
const money = (v, cur = 'USD') => v == null ? '–' : `${cur === 'USD' ? '$' : cur + ' '}${Number(v).toFixed(2)}`;

const STATUS = {
  complete:       ['p-good', 'Complete'],
  needs_check:    ['p-blue', 'Needs your check'],
  needs_followup: ['p-warn', 'Missing info'],
  followup_sent:  ['p-warn', 'Follow-up sent'],
  waiting:        ['p-grey', 'No reply'],
};
const statusPill = s => s ? `<span class="pill ${STATUS[s][0]}">${STATUS[s][1]}</span>` : '<span class="muted small">not invited</span>';
const DECISION = {accept: ['p-good', 'Accept'], modify: ['p-warn', 'Request changes'], reject: ['p-bad', 'Decline'], pending: ['p-grey', 'Waiting for info']};
const decPill = d => `<span class="pill ${DECISION[d][0]}">${DECISION[d][1]}</span>`;
const KIND = {rfq: 'Request (RFQ)', followup: 'Follow-up', reminder: 'Reminder', decision: 'Decision', reply: 'Vendor reply'};

// ------------------------------------------------------------------ state
let META = null;
let ROUND = null;
const state = {
  vendor: 'V7',
  weights: JSON.parse(localStorage.getItem('weights') || 'null'),
  selectedRank: null,
  catFilter: '',
};
const weightsQuery = () => new URLSearchParams(state.weights).toString();

// ------------------------------------------------------------------ approvals component (shared)
function approvalList(msgs, {open = false, empty = 'Nothing waiting for approval.'} = {}) {
  if (!msgs.length) return `<div class="empty">${empty}</div>`;
  return msgs.map(m => `
    <div class="approval ${open ? 'open' : ''}" data-mid="${m.id}">
      <div class="row">
        <span class="pill ${m.kind === 'decision' ? 'p-blue' : m.kind === 'followup' ? 'p-warn' : 'p-grey'}">${KIND[m.kind]}</span>
        <div style="min-width:0"><b>${esc(m.vendor_name)}</b> <span class="muted small">· ${esc(m.subject)}</span></div>
        <span class="spacer"></span>
        <button class="btn sm" data-toggle>${open ? 'Hide' : 'Review'}</button>
        <button class="btn sm primary" data-approve>Approve &amp; send</button>
      </div>
      <div class="ab">
        <div class="small muted" style="margin-bottom:4px">To: ${esc(m.to)}</div>
        <textarea class="mono" data-body>${esc(m.body)}</textarea>
        <div class="row" style="margin-top:6px"><button class="btn sm ghost" data-discard>Discard</button><span class="spacer"></span>
          <button class="btn sm" data-save>Save edits</button></div>
      </div>
    </div>`).join('');
}
function bindApprovals(root) {
  root.querySelectorAll('.approval').forEach(el => {
    const id = el.dataset.mid;
    $('[data-toggle]', el).onclick = () => { el.classList.toggle('open'); $('[data-toggle]', el).textContent = el.classList.contains('open') ? 'Hide' : 'Review'; };
    $('[data-approve]', el).onclick = () => act(async () => {
      if (el.classList.contains('open')) await api(`/api/messages/${id}`, {method: 'PATCH', body: {body: $('[data-body]', el).value}});
      await api(`/api/messages/${id}/approve`, {method: 'POST'});
    }, 'Email sent');
    $('[data-save]', el).onclick = () => act(() => api(`/api/messages/${id}`, {method: 'PATCH', body: {body: $('[data-body]', el).value}}), 'Saved');
    $('[data-discard]', el).onclick = () => act(() => api(`/api/messages/${id}/discard`, {method: 'POST'}), 'Discarded');
  });
}

// ------------------------------------------------------------------ pages
const pages = {};

pages.overview = async () => {
  const [approvals, activity] = await Promise.all([api('/api/approvals'), api('/api/activity')]);
  const r = ROUND, pct = Math.round(r.complete_ratio * 100), th = Math.round(r.threshold * 100);
  const stage = r.ready ? 2 : 1;
  const steps = ['Outreach sent', 'Collecting replies', 'Ready to analyze', 'Ranked', 'Decisions sent'];
  return `
  <header class="top">
    <div><h1>${esc(r.name)}</h1><div class="sub">Reply deadline ${esc(r.deadline)} · Launch ${esc(r.launch_date)} · Analysis unlocks at ${th}% complete replies</div></div>
    <a class="btn ${r.ready ? 'primary' : ''}" href="#analysis">${r.ready ? 'Start analysis →' : 'Preview analysis'}</a>
  </header>
  <div class="card" style="margin-bottom:14px"><div class="stepper">
    ${steps.map((s, i) => `<div class="${i < stage ? 'done' : i === stage ? 'now' : ''}">${i + 1} · ${s}</div>`).join('')}
  </div></div>
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card kpi"><div class="v">${r.counts.complete} / ${r.invited}</div><div class="l">Vendors with complete info</div></div>
    <div class="card kpi"><div class="v">${r.counts.needs_check}</div><div class="l">Replies waiting for your check</div></div>
    <div class="card kpi"><div class="v">${r.counts.needs_followup + r.counts.followup_sent}</div><div class="l">Missing info (follow-up)</div></div>
    <div class="card kpi"><div class="v">${r.counts.waiting}</div><div class="l">No reply yet</div></div>
  </div>
  <div class="grid g-main" style="align-items:start">
    <div class="card">
      <h2>Reply progress</h2>
      <div class="threshold" style="margin:14px 0 6px">
        <div class="bar" style="height:14px"><i style="width:${pct}%"></i></div>
        <div class="mark" style="left:${th}%"></div>
      </div>
      <div class="row muted small"><span>${pct}% complete</span><span class="spacer"></span><span>${th}% threshold</span></div>
      <div class="callout ${r.ready ? '' : 'warn'}" style="margin-top:12px">${r.ready
        ? `✅ <b>Threshold reached.</b> You can start the analysis now. Late replies are added and re-ranked automatically.`
        : `⏳ <b>${Math.max(0, Math.ceil(r.threshold * r.invited) - r.counts.complete)} more complete vendor(s) needed.</b> Check replies and approve follow-ups to get there faster.`}</div>
      <h2 style="margin-top:18px">Needs your approval (${approvals.length})</h2>
      <div id="apr">${approvalList(approvals)}</div>
    </div>
    <div class="card">
      <h2>Activity</h2>
      <ul class="feed">${activity.map(a => `<li><span class="t">${fmtTime(a.ts)}</span><span class="dot ${a.level}"></span><div>${esc(a.text)}</div></li>`).join('')}</ul>
    </div>
  </div>`;
};
pages.overview.after = root => bindApprovals(root);

pages.outreach = async () => {
  const vendors = await api('/api/vendors');
  const r = ROUND;
  return `
  <header class="top"><div><h1>1 · Brief &amp; outreach</h1><div class="sub">Write the request once. The agent drafts a personalized email for each vendor; you approve before anything is sent.</div></div></header>
  <div class="grid g2" style="align-items:start">
    <div class="card">
      <h2>Request brief</h2>
      <label class="lbl">Theme</label><input type="text" id="fTheme" value="${esc(r.theme)}">
      <label class="lbl">Product categories</label><input type="text" id="fCats" value="${esc(r.categories)}">
      <label class="lbl">Requirements &amp; notes</label><textarea id="fReq" style="min-height:150px">${esc(r.requirements)}</textarea>
      <div class="grid g2" style="gap:10px">
        <div><label class="lbl">Reply deadline</label><input type="text" id="fDeadline" value="${esc(r.deadline)}"></div>
        <div><label class="lbl">Start analysis when</label><select id="fThreshold">
          ${[[0.7, '70% of vendors complete'], [0.8, '80% of vendors complete'], [1, 'All vendors complete']].map(([v, l]) =>
            `<option value="${v}" ${Number(r.threshold) === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
      </div>
      <div class="callout" style="margin-top:12px">Vendors are asked for every required field in <a href="#fields">Data fields</a>, so replies are easier to read.</div>
      <div class="row" style="margin-top:12px"><span class="spacer"></span><button class="btn" id="saveBrief">Save brief</button></div>
    </div>
    <div class="card">
      <div class="row"><h2 style="margin:0">Recipients</h2><span class="spacer"></span><button class="link small" id="selAll">select all</button></div>
      <table style="margin-top:8px">
        <thead><tr><th></th><th>Vendor</th><th>Country</th><th>Categories</th><th>Rating</th><th>This round</th></tr></thead>
        <tbody>${vendors.map(v => `<tr><td><input type="checkbox" value="${v.id}" ${v.status ? '' : 'checked'}></td>
          <td><b>${esc(v.name)}</b><div class="muted small">${esc(v.email)}</div></td><td>${v.country}</td>
          <td class="muted">${esc(v.categories)}</td><td>★ ${v.rating}</td><td>${v.status ? '<span class="pill p-grey">Invited</span>' : ''}</td></tr>`).join('')}</tbody>
      </table>
      <div class="row" style="margin-top:12px"><span class="muted small" id="selCount"></span><span class="spacer"></span>
        <button class="btn primary" id="draftRfq">Draft emails for selected →</button></div>
    </div>
  </div>`;
};
pages.outreach.after = root => {
  const boxes = () => [...root.querySelectorAll('tbody input[type=checkbox]')];
  const count = () => $('#selCount').textContent = `${boxes().filter(b => b.checked).length} selected`;
  boxes().forEach(b => b.onchange = count); count();
  $('#selAll').onclick = () => { boxes().forEach(b => b.checked = true); count(); };
  $('#saveBrief').onclick = () => act(() => api('/api/round', {method: 'PUT', body: {
    theme: $('#fTheme').value, categories: $('#fCats').value, requirements: $('#fReq').value,
    deadline: $('#fDeadline').value, threshold: Number($('#fThreshold').value)}}), 'Brief saved');
  $('#draftRfq').onclick = () => {
    const ids = boxes().filter(b => b.checked).map(b => b.value);
    if (!ids.length) return toast('Select at least one vendor');
    act(async () => { await api('/api/outreach', {method: 'POST', body: {vendor_ids: ids}}); location.hash = 'overview'; },
      `${ids.length} draft(s) ready for approval`);
  };
};

pages.responses = async () => {
  const vendors = (await api('/api/vendors')).filter(v => v.status);
  if (!vendors.find(v => v.id === state.vendor)) state.vendor = vendors[0]?.id;
  const d = state.vendor ? await api(`/api/responses/${state.vendor}`) : null;
  const order = ['needs_followup', 'needs_check', 'followup_sent', 'waiting', 'complete'];
  vendors.sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return `
  <header class="top"><div><h1>2 · Vendor responses</h1><div class="sub">The agent reads each reply and fills in the fields. You check, fix anything wrong, and save. Missing info → follow-up email.</div></div></header>
  <div class="grid" style="grid-template-columns:300px 1fr;align-items:start">
    <div class="card flush"><table>
      <thead><tr><th>Vendor</th><th>Status</th></tr></thead>
      <tbody>${vendors.map(v => `<tr class="click ${v.id === state.vendor ? 'sel' : ''}" data-vid="${v.id}">
        <td><b>${esc(v.name)}</b><div class="muted small">${v.country} · ${v.product_count || 'no'} product${v.product_count === 1 ? '' : 's'}</div></td>
        <td>${statusPill(v.status)}</td></tr>`).join('')}</tbody>
    </table></div>
    <div>${d ? vendorDetail(d) : ''}</div>
  </div>`;
};
function vendorDetail(d) {
  const v = d.vendor, inbound = d.messages.filter(m => m.direction === 'in');
  const pending = d.messages.filter(m => m.status === 'pending_approval').map(m => ({...m, vendor_name: v.name, to: v.email}));
  const sent = d.messages.filter(m => m.direction === 'out' && m.status === 'sent');
  const anyMissing = d.products.some(p => p.missing.length);
  const thread = [...inbound, ...sent].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  const head = `<div class="row"><h2 style="margin:0">${esc(v.name)}</h2>${statusPill(d.status)}<span class="spacer"></span>
    ${d.status === 'waiting' || d.status === 'followup_sent' ? `<button class="btn sm" id="simulate" title="Demo only">▶ Simulate vendor reply</button>` : ''}</div>
    <div class="muted small" style="margin:2px 0 8px">${esc(v.contact_name)} · ${esc(v.email)} · ${v.country} · ★ ${v.rating} · ${esc(v.history)}</div>`;

  if (!inbound.length) {
    return `<div class="card">${head}
      <div class="callout warn">No reply yet. ${pending.length ? 'A reminder is drafted below.' : ''}</div>
      ${pending.length ? `<h2 style="margin-top:14px">Waiting for approval</h2>${approvalList(pending, {open: true})}` : ''}
      <h2 style="margin-top:14px">Sent</h2>${sent.map(msgBlock).join('')}</div>`;
  }
  return `
  <div class="card" style="margin-bottom:14px">${head}
    ${pending.length ? `<div style="margin-bottom:10px">${approvalList(pending, {open: true})}</div>` : ''}
    ${anyMissing && !pending.some(m => m.kind === 'followup') ? `<div class="callout warn row" style="margin-bottom:10px">
      <span>Some required fields are missing.</span><span class="spacer"></span><button class="btn sm primary" id="draftFollowup">Draft follow-up email</button></div>` : ''}
    <div class="grid g2" style="align-items:start">
      <div>
        <h2>Email thread</h2>
        ${thread.map(msgBlock).join('')}
      </div>
      <div>${d.products.map(productForm).join('')}</div>
    </div>
  </div>`;
}
function msgBlock(m) {
  return `<div class="msg-head"><span class="pill ${m.direction === 'in' ? 'p-blue' : 'p-grey'}">${m.direction === 'in' ? '← from vendor' : '→ ' + KIND[m.kind]}</span>
    <span>${fmtTime(m.sent_at || m.created_at)}</span></div><pre class="email">${esc(m.body)}</pre>`;
}
function productForm(p) {
  const low = p.low_confidence || [];
  const rows = META.fields.map(f => {
    const val = p.fields[f.key];
    const miss = p.missing.includes(f.key), lo = low.includes(f.key);
    const tag = miss ? '<span class="pill p-bad">missing</span>' : lo ? '<span class="pill p-blue">check</span>' : '';
    const input = f.type === 'bool'
      ? `<select data-k="${f.key}"><option value="">–</option><option ${val === true ? 'selected' : ''} value="true">Yes</option><option ${val === false ? 'selected' : ''} value="false">No</option></select>`
      : `<input type="${f.type === 'number' ? 'number' : 'text'}" step="any" data-k="${f.key}" value="${esc(val)}" placeholder="${miss ? 'Ask vendor' : ''}">`;
    return `<div class="field ${miss ? 'missing' : ''} ${lo ? 'low' : ''}"><span class="k">${esc(f.label)}${f.required ? ' *' : ''}</span>${input}<span class="right">${tag}</span></div>`;
  }).join('');
  const saved = p.status === 'saved';
  return `<div class="card" style="margin-bottom:12px;background:#fcfcfd" data-pid="${p.id}">
    <div class="row"><h2 style="margin:0">${esc(p.fields.product_name || 'Product')}</h2>
      ${saved ? '<span class="pill p-good">Saved</span>' : '<span class="pill p-blue">Extracted — please check</span>'}</div>
    <div class="muted small" style="margin:2px 0 8px">* required · blue = agent unsure · red = missing</div>
    ${rows}
    <div class="row" style="margin-top:10px"><span class="spacer"></span>
      <button class="btn" data-save-fields>Save edits</button>
      ${saved ? '' : `<button class="btn primary" data-confirm ${p.missing.length ? 'disabled title="Fill or follow up on missing fields first"' : ''}>Looks right — save to database</button>`}
    </div></div>`;
}
function readFields(card) {
  const out = {};
  card.querySelectorAll('[data-k]').forEach(i => {
    let v = i.value;
    if (i.tagName === 'SELECT') v = v === '' ? null : v === 'true';
    out[i.dataset.k] = v;
  });
  return out;
}
pages.responses.after = root => {
  root.querySelectorAll('tr[data-vid]').forEach(tr => tr.onclick = () => { state.vendor = tr.dataset.vid; render(); });
  bindApprovals(root);
  const sim = $('#simulate', root);
  if (sim) sim.onclick = () => act(async () => {
    const r = await api(`/api/demo/simulate-reply/${state.vendor}`, {method: 'POST'});
    if (!r.ok) throw new Error(r.message);
  }, 'Vendor reply received and extracted');
  const fu = $('#draftFollowup', root);
  if (fu) fu.onclick = () => act(() => api(`/api/responses/${state.vendor}/followup`, {method: 'POST'}), 'Follow-up drafted');
  root.querySelectorAll('[data-pid]').forEach(card => {
    const id = card.dataset.pid;
    $('[data-save-fields]', card).onclick = () => act(() => api(`/api/products/${id}`, {method: 'PATCH', body: {fields: readFields(card)}}), 'Saved');
    const c = $('[data-confirm]', card);
    if (c) c.onclick = () => act(async () => {
      await api(`/api/products/${id}`, {method: 'PATCH', body: {fields: readFields(card)}});
      await api(`/api/products/${id}/confirm`, {method: 'POST'});
    }, 'Saved to database');
  });
};

pages.database = async () => {
  const prods = await api('/api/products');
  const cats = [...new Set(prods.map(p => p.fields.category))];
  const shown = prods.filter(p => !state.catFilter || p.fields.category === state.catFilter);
  const cols = ['product_name', 'category', 'capacity_oz', 'material', 'lid_type', 'colors', 'fob_price', 'moq', 'lead_time_days', 'origin', 'certifications'];
  const label = Object.fromEntries(META.fields.map(f => [f.key, f.label]));
  let lastV = null;
  return `
  <header class="top">
    <div><h1>3 · Product database</h1><div class="sub">One row per vendor × product. ${prods.length} products from ${new Set(prods.map(p => p.vendor_id)).size} vendors. Click a row to open it.</div></div>
    <div class="row"><select id="catF" style="width:auto"><option value="">All categories</option>${cats.map(c => `<option ${c === state.catFilter ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
      <button class="btn" id="csv">Export CSV</button></div>
  </header>
  <div class="card flush scroll-x"><table>
    <thead><tr><th>Vendor</th>${cols.map(k => `<th>${esc(label[k])}</th>`).join('')}<th class="right">Landed (USD)</th><th>Status</th></tr></thead>
    <tbody>${shown.map(p => {
      const first = p.vendor_id !== lastV; lastV = p.vendor_id;
      return `<tr class="click" data-vid="${p.vendor_id}"><td>${first ? `<b>${esc(p.vendor_name)}</b>` : ''}</td>${cols.map(k => {
        const v = p.fields[k];
        if (p.missing.includes(k)) return '<td><span class="pill p-bad">missing</span></td>';
        if (k === 'fob_price') return `<td class="num right">${money(v, p.fields.currency)}</td>`;
        if (typeof v === 'number') return `<td class="num right">${v.toLocaleString()}</td>`;
        return `<td>${esc(v)}</td>`;
      }).join('')}<td class="num right">${money(p.landed_usd)}</td>
      <td>${p.status === 'saved' ? '<span class="pill p-good">Saved</span>' : p.missing.length ? '<span class="pill p-warn">Incomplete</span>' : '<span class="pill p-blue">To check</span>'}</td></tr>`;
    }).join('')}</tbody></table></div>`;
};
pages.database.after = root => {
  $('#catF').onchange = e => { state.catFilter = e.target.value; render(); };
  root.querySelectorAll('tr[data-vid]').forEach(tr => tr.onclick = () => { state.vendor = tr.dataset.vid; location.hash = 'responses'; });
  $('#csv').onclick = async () => {
    const prods = await api('/api/products');
    const keys = META.fields.map(f => f.key);
    const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const csv = [['vendor', ...keys, 'landed_usd', 'status'].join(','),
      ...prods.map(p => [q(p.vendor_name), ...keys.map(k => q(p.fields[k])), p.landed_usd ?? '', p.status].join(','))].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv'})); a.download = 'products.csv'; a.click();
  };
};

pages.analysis = async () => {
  const rows = await api('/api/ranking?' + weightsQuery());
  const shown = rows.filter(p => !state.catFilter || p.fields.category === state.catFilter);
  const cats = [...new Set(rows.map(p => p.fields.category))];
  const mb = x => x == null ? '<span class="muted">–</span>' : `<span class="minibar"><i style="width:${x}%"></i></span><span class="num">${x}</span>`;
  const sel = rows.find(p => p.id === state.selectedRank) || shown[0];
  let rank = 0;
  return `
  <header class="top">
    <div><h1>4 · Analysis &amp; ranking</h1><div class="sub">Each product gets a 0–100 score per dimension. Move the sliders to change what matters; the ranking updates instantly.</div></div>
  </header>
  ${ROUND.ready ? '' : `<div class="callout warn" style="margin-bottom:14px">⏳ Only ${ROUND.counts.complete}/${ROUND.invited} vendors are complete (threshold ${Math.round(ROUND.threshold * 100)}%). This is a preview; the ranking will change as more replies come in.</div>`}
  <div class="grid" style="grid-template-columns:280px 1fr;align-items:start">
    <div class="grid">
      <div class="card">
        <h2>What matters most?</h2>
        ${META.dimensions.map(d => `<div class="slider"><span>${esc(d.label)}</span><input type="range" min="0" max="50" value="${state.weights[d.key]}" data-w="${d.key}"><span class="num right" data-wv="${d.key}">${state.weights[d.key]}</span></div>`).join('')}
        <div class="muted small" style="margin-top:8px">Presets: ${Object.keys(META.presets).map(k => `<button class="link" data-preset="${k}">${{balanced: 'Balanced', margin: 'Margin first', speed: 'Speed to market'}[k] || k}</button>`).join(' · ')}</div>
      </div>
      <div class="card">
        <h2>Trend signals <span class="muted small" style="font-weight:400">· sample data</span></h2>
        ${META.trends.map(t => `<div class="trend"><span>${esc(t.name)}</span><span class="${t.direction}">${t.direction === 'up' ? '▲' : '▼'} ${esc(t.label)}</span></div>`).join('')}
      </div>
    </div>
    <div class="grid">
      <div class="card flush">
        <div class="row" style="padding:12px 14px"><h2 style="margin:0">Ranked products</h2><span class="spacer"></span>
          <select id="catF" style="width:auto"><option value="">All categories</option>${cats.map(c => `<option ${c === state.catFilter ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>
        <div class="scroll-x"><table>
          <thead><tr><th>#</th><th>Product</th><th class="right">Landed</th>${META.dimensions.map(d => `<th>${esc(d.label)}</th>`).join('')}<th class="right">Overall</th><th>Suggested</th></tr></thead>
          <tbody>${shown.map(p => `<tr class="click ${sel && p.id === sel.id ? 'sel' : ''}" data-rid="${p.id}">
            <td>${p.overall == null ? '–' : ++rank}</td>
            <td><b>${esc(p.fields.product_name)}</b><div class="muted small">${esc(p.vendor_name)} · ${esc(p.fields.category)} · ${p.fields.capacity_oz} oz</div></td>
            <td class="right num">${money(p.landed_usd)}</td>
            ${META.dimensions.map(d => `<td>${mb(p.scores?.[d.key])}</td>`).join('')}
            <td class="right score">${p.overall ?? '–'}</td><td>${decPill(p.suggested)}</td></tr>`).join('')}</tbody>
        </table></div>
      </div>
      ${sel ? explainCard(sel) : ''}
    </div>
  </div>`;
};
function explainCard(p) {
  if (p.overall == null) return `<div class="card"><h2>${esc(p.fields.product_name)}</h2><div class="callout warn">Can't score yet: ${p.missing.length ? 'missing ' + p.missing.join(', ') : 'waiting for your check in Vendor responses'}.</div></div>`;
  const dims = META.dimensions.map(d => ({...d, v: p.scores[d.key]}));
  const best = [...dims].sort((a, b) => b.v - a.v).slice(0, 2), worst = [...dims].sort((a, b) => a.v - b.v).slice(0, 2);
  return `<div class="card">
    <div class="row"><h2 style="margin:0">Why ${esc(p.fields.product_name)} scores ${p.overall}</h2><span class="spacer"></span>${decPill(p.suggested)}</div>
    <div class="grid g2" style="margin-top:10px">
      <div><div class="small muted">Strengths</div>${best.map(d => `<div>✔ ${esc(d.label)} <b>${d.v}</b></div>`).join('')}</div>
      <div><div class="small muted">Weak spots</div>${worst.map(d => `<div>✖ ${esc(d.label)} <b>${d.v}</b></div>`).join('')}</div>
    </div>
    <div class="small" style="margin-top:10px">FOB ${money(p.fields.fob_price, p.fields.currency)} → landed ${money(p.landed_usd)} · MOQ ${Number(p.fields.moq).toLocaleString()} · ${p.fields.lead_time_days} days · ${esc(p.fields.origin)} · ${esc(p.fields.certifications)}</div>
    ${p.change_request ? `<div class="callout warn" style="margin-top:10px"><b>Suggested change request:</b> ${esc(p.change_request)}</div>` : ''}
  </div>`;
}
pages.analysis.after = root => {
  root.querySelectorAll('[data-w]').forEach(r => {
    r.oninput = () => { $(`[data-wv="${r.dataset.w}"]`).textContent = r.value; };
    r.onchange = () => { state.weights[r.dataset.w] = Number(r.value); saveWeights(); render(); };
  });
  root.querySelectorAll('[data-preset]').forEach(a => a.onclick = () => { state.weights = {...META.presets[a.dataset.preset]}; saveWeights(); render(); });
  root.querySelectorAll('tr[data-rid]').forEach(tr => tr.onclick = () => { state.selectedRank = Number(tr.dataset.rid); render(); });
  $('#catF').onchange = e => { state.catFilter = e.target.value; render(); };
};
const saveWeights = () => localStorage.setItem('weights', JSON.stringify(state.weights));

pages.decisions = async () => {
  const [rows, approvals] = await Promise.all([api('/api/ranking?' + weightsQuery()), api('/api/approvals')]);
  const drafts = approvals.filter(m => m.kind === 'decision');
  const n = k => rows.filter(p => p.final_decision === k).length;
  return `
  <header class="top">
    <div><h1>5 · Decisions</h1><div class="sub">The agent's suggestion is pre-selected. Change anything, then draft one email per vendor and approve.</div></div>
    <button class="btn primary" id="draftDec">Draft decision emails →</button>
  </header>
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card kpi"><div class="v" style="color:var(--good)">${n('accept')}</div><div class="l">Accept as is</div></div>
    <div class="card kpi"><div class="v" style="color:var(--warn)">${n('modify')}</div><div class="l">Request changes</div></div>
    <div class="card kpi"><div class="v" style="color:var(--bad)">${n('reject')}</div><div class="l">Decline</div></div>
    <div class="card kpi"><div class="v" style="color:var(--muted)">${n('pending')}</div><div class="l">Waiting for info</div></div>
  </div>
  <div class="card flush" style="margin-bottom:14px"><table>
    <thead><tr><th>Product</th><th class="right">Score</th><th style="width:190px">Decision</th><th>Change request (sent to vendor)</th></tr></thead>
    <tbody>${rows.map(p => `<tr data-pid="${p.id}">
      <td><b>${esc(p.fields.product_name)}</b><div class="muted small">${esc(p.vendor_name)}</div></td>
      <td class="right score">${p.overall ?? '–'}</td>
      <td>${p.final_decision === 'pending' ? decPill('pending') : `<select data-dec>
        <option value="">Suggested: ${DECISION[p.suggested][1]}</option>
        ${['accept', 'modify', 'reject'].map(k => `<option value="${k}" ${p.decision === k ? 'selected' : ''}>${DECISION[k][1]}</option>`).join('')}</select>`}</td>
      <td>${p.final_decision === 'modify' ? `<input type="text" data-cr value="${esc(p.change_request)}" placeholder="What should the vendor change?">`
        : `<span class="muted small">${p.final_decision === 'accept' ? 'Proceed to sampling' : p.final_decision === 'reject' ? 'Polite decline' : 'Missing required info'}</span>`}</td>
    </tr>`).join('')}</tbody></table></div>
  <div class="card"><h2>Decision emails waiting for approval (${drafts.length})</h2>
    ${approvalList(drafts, {empty: 'No drafts yet. Click "Draft decision emails" when your decisions are ready.'})}</div>`;
};
pages.decisions.after = root => {
  root.querySelectorAll('tr[data-pid]').forEach(tr => {
    const id = tr.dataset.pid, sel = $('[data-dec]', tr), cr = $('[data-cr]', tr);
    if (sel) sel.onchange = () => act(() => api(`/api/products/${id}/decision`, {method: 'PUT', body: {decision: sel.value || null}}));
    if (cr) cr.onchange = () => act(() => api(`/api/products/${id}/decision`, {method: 'PUT',
      body: {decision: sel.value || null, change_request: cr.value}}), 'Saved');
  });
  $('#draftDec').onclick = () => act(() => api('/api/decisions/draft?' + weightsQuery(), {method: 'POST'}), 'Decision emails drafted');
  bindApprovals(root);
};

pages.fields = async () => `
  <header class="top"><div><h1>Data fields</h1><div class="sub">What the agent extracts for every vendor × product. Missing <b>required</b> fields trigger a follow-up email.</div></div></header>
  <div class="card flush"><table>
    <thead><tr><th>Field</th><th>Key</th><th>Type</th><th>Required</th><th>Notes</th></tr></thead>
    <tbody>${META.fields.map(f => `<tr><td><b>${esc(f.label)}</b></td><td><code>${f.key}</code></td><td>${f.type}</td>
      <td>${f.required ? '<span class="pill p-good">Required</span>' : '<span class="pill p-grey">Optional</span>'}</td><td class="muted">${esc(f.notes)}</td></tr>`).join('')}</tbody>
  </table></div>
  <p class="muted small">Fields are defined in <code>app/fields.py</code>.</p>`;

pages.vendors = async () => {
  const vendors = await api('/api/vendors');
  return `
  <header class="top"><div><h1>Vendor directory</h1><div class="sub">Contacts and history. Rating feeds the "Vendor reliability" score.</div></div></header>
  <div class="card flush"><table>
    <thead><tr><th>Vendor</th><th>Country</th><th>Contact</th><th>Categories</th><th>History</th><th>Rating</th><th>This round</th></tr></thead>
    <tbody>${vendors.map(v => `<tr class="click" data-vid="${v.id}"><td><b>${esc(v.name)}</b></td><td>${v.country}</td>
      <td>${esc(v.contact_name)}<div class="muted small">${esc(v.email)}</div></td><td>${esc(v.categories)}</td>
      <td>${esc(v.history)}</td><td>★ ${v.rating}</td><td>${statusPill(v.status)}</td></tr>`).join('')}</tbody>
  </table></div>`;
};
pages.vendors.after = root => root.querySelectorAll('tr[data-vid]').forEach(tr =>
  tr.onclick = () => { state.vendor = tr.dataset.vid; location.hash = 'responses'; });

// ------------------------------------------------------------------ shell
async function refreshSidebar() {
  ROUND = await api('/api/round');
  $('#roundPick').innerHTML = `<span class="muted">Sourcing round</span><b>${esc(ROUND.name)}</b>Launch ${esc(ROUND.launch_date)} · ${ROUND.invited} vendors`;
  $('#bApprovals').textContent = ROUND.pending_approvals || '';
  $('#bResponses').textContent = (ROUND.counts.needs_check + ROUND.counts.needs_followup) || '';
}
async function render() {
  const page = (location.hash.slice(1) || 'overview');
  const fn = pages[page] || pages.overview;
  document.querySelectorAll('.nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + page));
  const main = $('#main');
  try { main.innerHTML = await fn(); fn.after?.(main); }
  catch (e) { main.innerHTML = `<div class="callout warn">Couldn't load this page: ${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); });
$('#resetDemo').onclick = () => { if (confirm('Reset all demo data?')) act(() => api('/api/demo/reset', {method: 'POST'}), 'Demo data reset'); };

(async () => {
  META = await api('/api/meta');
  if (!state.weights) state.weights = {...META.presets.balanced};
  await refreshSidebar();
  await render();
})();
