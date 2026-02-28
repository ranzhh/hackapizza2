/* =========================================================================
   Hackapizza 2.0 Dashboard — Client Application (Events-centric)
   ========================================================================= */

// State
let allRestaurants = [];
let allRecipes = [];
let ws = null;
let currentPanel = 'events';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const $ = id => document.getElementById(id);
const $$ = sel => document.querySelectorAll(sel);

function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleTimeString('it-IT', { hour:'2-digit', minute:'2-digit', second:'2-digit' })
    + '.' + String(d.getMilliseconds()).padStart(3,'0');
}

function formatDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1) return '<1ms';
  if (ms < 1000) return Math.round(ms) + 'ms';
  return (ms / 1000).toFixed(2) + 's';
}

function eventTypeBadge(t) {
  const m = {
    game_phase_changed: 'badge-accent',
    game_started: 'badge-ok',
    client_spawned: 'badge-info',
    preparation_complete: 'badge-ok',
    new_message: 'badge-info',
    game_reset: 'badge-error',
  };
  return `<span class="badge ${m[t]||'badge-muted'}">${t}</span>`;
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function prettyJson(o) {
  try { if (typeof o === 'string') o = JSON.parse(o); return JSON.stringify(o,null,2); }
  catch { return String(o); }
}

let _dt = {};
function debounce(fn, ms) {
  return function(...a) { clearTimeout(_dt[fn.name]); _dt[fn.name] = setTimeout(()=>fn.apply(this,a), ms); };
}

let _jtId = 0;
function jsonCell(payload) {
  const id = 'j'+(++_jtId);
  const prev = typeof payload === 'object' ? JSON.stringify(payload).slice(0,100) : String(payload).slice(0,100);
  return `<span class="mono" style="font-size:11px;color:var(--text-2)">${esc(prev)}${prev.length>=100?'…':''}</span>
    <span class="json-toggle" onclick="$('${id}').classList.toggle('open')">show</span>
    <pre class="json-content" id="${id}">${esc(prettyJson(payload))}</pre>`;
}

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
function initTheme() {
  const saved = localStorage.getItem('hp2-theme') || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcons(saved);
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('hp2-theme', next);
  updateThemeIcons(next);
}

function updateThemeIcons(theme) {
  $('iconSun').style.display = theme === 'dark' ? 'none' : 'block';
  $('iconMoon').style.display = theme === 'dark' ? 'block' : 'none';
}

$('themeToggle').addEventListener('click', toggleTheme);
initTheme();

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
$$('.nav-item').forEach(item => {
  item.addEventListener('click', () => switchPanel(item.dataset.panel));
});

function switchPanel(panel) {
  currentPanel = panel;
  $$('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelector(`.nav-item[data-panel="${panel}"]`).classList.add('active');
  $$('.panel').forEach(p => p.classList.remove('active'));
  $(`panel-${panel}`).classList.add('active');

  const titles = {
    events:'Events', clients:'Clients', preparations:'Preparations',
    messages:'Messages', restaurants:'Restaurants', recipes:'Recipes', stats:'Stats'
  };
  $('panelTitle').textContent = titles[panel] || panel;
  $('liveIndicator').style.display = panel === 'events' ? 'flex' : 'none';

  if (panel === 'events') loadEvents();
  if (panel === 'clients') loadClients();
  if (panel === 'preparations') loadPreparations();
  if (panel === 'messages') loadMessages();
  if (panel === 'restaurants') loadRestaurants();
  if (panel === 'recipes') loadRecipes();
  if (panel === 'stats') loadStats();
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// Phase
// ---------------------------------------------------------------------------
async function loadPhase() {
  try {
    const data = await api('/api/phase');
    updatePhaseBadge(data.phase);
  } catch {}
}

function updatePhaseBadge(phase) {
  const el = $('phaseBadge');
  el.textContent = phase || '—';
  el.className = 'phase-badge phase-' + (phase || 'unknown');
}

// ---------------------------------------------------------------------------
// EVENTS (primary panel)
// ---------------------------------------------------------------------------
async function loadEvents() {
  const t = $('filterEventType').value;
  const params = t ? `?event_type=${t}` : '';
  const data = await api(`/api/events${params}`);
  const tbody = $('eventsBody');
  const empty = $('eventsEmpty');
  $('eventCount').textContent = data.events.length;

  if (data.events.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = data.events.map(e => `<tr>
    <td class="mono">${e.id}</td>
    <td class="mono">${formatTime(e.timestamp_utc)}</td>
    <td class="mono">${e.turn_id||'—'}</td>
    <td>${eventTypeBadge(e.event_type)}</td>
    <td>${jsonCell(e.data)}</td>
  </tr>`).join('');
}

function appendNewEvents(events) {
  let phaseChanged = false;
  for (const e of events) {
    if (e.event_type === 'game_phase_changed' && e.data && e.data.phase) {
      updatePhaseBadge(e.data.phase);
      phaseChanged = true;
    }
  }
  // Append to events table if on events panel
  if (currentPanel === 'events') {
    const tbody = $('eventsBody');
    const empty = $('eventsEmpty');
    empty.style.display = 'none';
    for (const e of events) {
      const tr = document.createElement('tr');
      tr.className = 'new-row';
      tr.innerHTML = `<td class="mono">${e.id}</td>
        <td class="mono">${formatTime(e.timestamp_utc)}</td>
        <td class="mono">${e.turn_id||'—'}</td>
        <td>${eventTypeBadge(e.event_type)}</td>
        <td>${jsonCell(e.data)}</td>`;
      tbody.insertBefore(tr, tbody.firstChild);
    }
    const cnt = $('eventCount');
    cnt.textContent = parseInt(cnt.textContent) + events.length;
    if ($('autoScroll').checked) $('eventsTableWrap').scrollTop = 0;
  }
  // Auto-refresh sub-panels if active
  if (currentPanel === 'clients') loadClients();
  if (currentPanel === 'preparations') loadPreparations();
  if (currentPanel === 'messages') loadMessages();
  if (phaseChanged && currentPanel === 'restaurants') loadRestaurants();
}

// ---------------------------------------------------------------------------
// CLIENTS (event_client_spawned)
// ---------------------------------------------------------------------------
async function loadClients() {
  const data = await api('/api/clients');
  const tbody = $('clientsBody');
  const empty = $('clientsEmpty');
  $('clientCount').textContent = data.clients.length;

  if (data.clients.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = data.clients.map(c => `<tr>
    <td class="mono">${c.id}</td>
    <td class="mono">${formatTime(c.timestamp_utc)}</td>
    <td class="mono">${c.turn_id||'—'}</td>
    <td class="name-col">${esc(c.client_name)}</td>
    <td>${esc(c.order_text)}</td>
  </tr>`).join('');
}

// ---------------------------------------------------------------------------
// PREPARATIONS (event_preparation_complete)
// ---------------------------------------------------------------------------
async function loadPreparations() {
  const data = await api('/api/preparations');
  const tbody = $('prepsBody');
  const empty = $('prepsEmpty');
  $('prepCount').textContent = data.preparations.length;

  if (data.preparations.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = data.preparations.map(p => `<tr>
    <td class="mono">${p.id}</td>
    <td class="mono">${formatTime(p.timestamp_utc)}</td>
    <td class="mono">${p.turn_id||'—'}</td>
    <td class="name-col">${esc(p.dish_name)}</td>
  </tr>`).join('');
}

// ---------------------------------------------------------------------------
// MESSAGES (event_new_message)
// ---------------------------------------------------------------------------
async function loadMessages() {
  const sender = $('filterSender').value;
  const params = sender ? `?sender_name=${encodeURIComponent(sender)}` : '';
  const data = await api(`/api/messages${params}`);
  const tbody = $('messagesBody');
  const empty = $('messagesEmpty');
  $('msgCount').textContent = data.messages.length;

  if (data.messages.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = data.messages.map(m => `<tr>
    <td class="mono">${m.id}</td>
    <td class="mono">${formatTime(m.timestamp_utc)}</td>
    <td class="mono">${m.turn_id||'—'}</td>
    <td class="name-col">${esc(m.sender_name)}</td>
    <td>${esc(m.text)}</td>
  </tr>`).join('');
}

// ---------------------------------------------------------------------------
// RESTAURANTS
// ---------------------------------------------------------------------------
async function loadRestaurants() {
  try {
    const data = await api('/api/restaurants');
    allRestaurants = data.restaurants;
    $('restCount').textContent = allRestaurants.length;
    renderRestaurants();
  } catch { $('restCount').textContent = '0'; }
}

function renderRestaurants() {
  const sort = $('sortRestaurants').value;
  const search = $('searchRestaurant').value.toLowerCase();
  let list = [...allRestaurants];
  if (search) list = list.filter(r => r.name.toLowerCase().includes(search));
  if (sort === 'balance') list.sort((a,b) => b.balance - a.balance);
  else if (sort === 'reputation') list.sort((a,b) => b.reputation - a.reputation);
  else if (sort === 'name') list.sort((a,b) => a.name.localeCompare(b.name));

  $('restaurantGrid').innerHTML = list.map((r,i) => {
    const inv = Object.entries(r.inventory || {});
    const menu = r.menu || [];
    const openBadge = r.is_open ? '<span class="badge badge-ok">open</span>' : '<span class="badge badge-error">closed</span>';
    return `<div class="card">
      <div class="card-row">
        <div><span style="color:var(--text-2);font-size:11px;font-family:var(--mono)">#${i+1}</span> <span class="card-title" style="margin:0">${esc(r.name)}</span></div>
        <div style="display:flex;align-items:center;gap:6px">${openBadge} <span style="font-family:var(--mono);font-size:10px;color:var(--text-2)">ID ${r.restaurant_id}</span></div>
      </div>
      <div class="card-stats">
        <div class="card-stat"><div class="card-stat-label">Balance</div><div class="card-stat-value" style="color:${r.balance>=0?'var(--green)':'var(--red)'}">${r.balance.toLocaleString()}</div></div>
        <div class="card-stat"><div class="card-stat-label">Reputation</div><div class="card-stat-value">${r.reputation}</div></div>
      </div>
      ${inv.length > 0 ? `<div class="card-section"><div class="card-section-title">Inventory (${inv.length})</div>${inv.map(([n,q])=>`<span class="pill">${esc(n)} ×${q}</span>`).join('')}</div>` : ''}
      ${menu.length > 0 ? `<div class="card-section"><div class="card-section-title">Menu (${menu.length})</div>${menu.map(m=>`<span class="pill">${esc(m.name)} — ${m.price}</span>`).join('')}</div>` : ''}
    </div>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// RECIPES
// ---------------------------------------------------------------------------
async function loadRecipes() {
  if (allRecipes.length > 0) { renderRecipes(); return; }
  const data = await api('/api/recipes');
  allRecipes = data.recipes;
  $('recipeCount').textContent = allRecipes.length;
  renderRecipes();
}

function renderRecipes() {
  const search = $('searchRecipe').value.toLowerCase();
  const sort = $('sortRecipes').value;
  let list = [...allRecipes];
  if (search) list = list.filter(r =>
    r.name.toLowerCase().includes(search) ||
    r.ingredients.some(i => i.ingredient_name.toLowerCase().includes(search))
  );
  if (sort === 'prestige') list.sort((a,b) => b.prestige - a.prestige);
  else if (sort === 'time') list.sort((a,b) => a.preparation_time_ms - b.preparation_time_ms);
  else if (sort === 'name') list.sort((a,b) => a.name.localeCompare(b.name));
  else if (sort === 'ingredients') list.sort((a,b) => a.ingredients.length - b.ingredients.length);

  $('recipeGrid').innerHTML = list.map(r => `<div class="card">
    <div class="card-title">${esc(r.name)}</div>
    <div style="display:flex;gap:6px;margin-bottom:8px">
      <span class="badge badge-accent">${r.prestige} prestige</span>
      <span class="badge badge-info">${formatDuration(r.preparation_time_ms)}</span>
      <span class="badge badge-muted">${r.ingredients.length} ing.</span>
    </div>
    <div>${r.ingredients.map(i=>`<span class="pill">${esc(i.ingredient_name)} ×${i.quantity}</span>`).join('')}</div>
  </div>`).join('');
}

// ---------------------------------------------------------------------------
// STATS
// ---------------------------------------------------------------------------
async function loadStats() {
  const data = await api('/api/stats');

  $('statsGrid').innerHTML = `
    <div class="stat-card"><div class="stat-label">Total events</div><div class="stat-value">${data.total_events.toLocaleString()}</div></div>
    <div class="stat-card"><div class="stat-label">Clients spawned</div><div class="stat-value" style="color:var(--blue)">${data.client_spawned_count}</div></div>
    <div class="stat-card"><div class="stat-label">Preparations</div><div class="stat-value" style="color:var(--green)">${data.preparation_complete_count}</div></div>
    <div class="stat-card"><div class="stat-label">Messages</div><div class="stat-value" style="color:var(--purple)">${data.new_message_count}</div></div>
    <div class="stat-card"><div class="stat-label">Last activity</div><div class="stat-value" style="font-size:13px">${formatTime(data.last_event_at)}</div></div>`;

  $('statsByEvent').innerHTML = (data.by_event_type||[]).map(r => `<tr><td>${eventTypeBadge(r.event_type)}</td><td class="mono">${r.cnt}</td></tr>`).join('');
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------
function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { $('wsDot').classList.add('on'); $('wsLabel').textContent = 'Connected'; };
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'new_events') appendNewEvents(msg.data);
    } catch {}
  };
  ws.onclose = () => { $('wsDot').classList.remove('on'); $('wsLabel').textContent = 'Reconnecting'; setTimeout(connectWs, 3000); };
  ws.onerror = () => ws.close();
}

// ---------------------------------------------------------------------------
// Refresh all
// ---------------------------------------------------------------------------
async function refreshAll() {
  await loadEvents();
  await loadPhase();
  if (currentPanel === 'clients') await loadClients();
  if (currentPanel === 'preparations') await loadPreparations();
  if (currentPanel === 'messages') await loadMessages();
  if (currentPanel === 'restaurants') await loadRestaurants();
  if (currentPanel === 'stats') await loadStats();
  if (currentPanel === 'recipes') { allRecipes=[]; await loadRecipes(); }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async function init() {
  await loadEvents();
  await loadPhase();
  connectWs();
  try { await loadStats(); } catch {}
})();
