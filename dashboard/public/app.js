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

// Render structured detail for an event row — no raw JSON toggle needed
const DETAIL_LABELS = {
  // value → { label, colour-class }
};

function detailCell(eventType, detail) {
  if (!detail || !Object.keys(detail).length) return '<span style="color:var(--text-2);font-size:11px;">—</span>';

  const pairs = Object.entries(detail);

  // Special formatting per event type
  if (eventType === 'game_phase_changed') {
    const cls = `phase-${detail.phase || 'unknown'}`;
    return `<span class="phase-badge ${cls}">${esc(detail.phase || '?')}</span>`;
  }

  if (eventType === 'new_message' || eventType === 'mcp_send_message') {
    const sender = detail.sender ? `<span class="badge badge-info" style="margin-right:4px">${esc(detail.sender)}</span>` : '';
    const rid = detail.recipient_id != null ? `<span class="badge badge-muted" style="margin-right:4px">→ #${detail.recipient_id}</span>` : '';
    const text = detail.text ? `<span style="color:var(--text-1);font-size:11px">${esc(detail.text)}</span>` : '';
    return sender + rid + text;
  }

  if (eventType === 'mcp_create_market_entry') {
    const sideClass = detail.side === 'ask' ? 'badge-error' : 'badge-ok';
    return `<span class="badge ${sideClass}">${esc(detail.side)}</span> `
      + `<span class="pill">${esc(detail.ingredient)}</span> `
      + `<span class="mono" style="font-size:11px">×${detail.qty} @ ${detail.price}</span>`;
  }

  if (eventType === 'mcp_set_open_status') {
    return detail.is_open
      ? '<span class="badge badge-ok">open</span>'
      : '<span class="badge badge-error">closed</span>';
  }

  if (eventType === 'mcp_closed_bid') {
    const bids = detail.bids;
    if (Array.isArray(bids)) {
      const id = 'j'+(++_jtId);
      return `<span style="font-size:11px;color:var(--text-2)">${bids.length} bids</span>
        <span class="json-toggle" onclick="$('${id}').classList.toggle('open')">show</span>
        <pre class="json-content" id="${id}">${esc(JSON.stringify(bids, null, 2))}</pre>`;
    }
    return `<span style="font-size:11px;color:var(--text-2)">${esc(String(detail.bids_json || ''))}</span>`;
  }

  if (eventType === 'mcp_save_menu') {
    const items = detail.items;
    if (Array.isArray(items)) {
      const id = 'j'+(++_jtId);
      return `<span style="font-size:11px;color:var(--text-2)">${items.length} items</span>
        <span class="json-toggle" onclick="$('${id}').classList.toggle('open')">show</span>
        <pre class="json-content" id="${id}">${esc(JSON.stringify(items, null, 2))}</pre>`;
    }
  }

  // Generic fallback: render key: value chips inline
  return pairs.map(([k, v]) => {
    const val = v == null ? '—' : (typeof v === 'object' ? JSON.stringify(v).slice(0, 60) : String(v));
    return `<span style="font-size:11px;color:var(--text-2)">${esc(k)}:</span>`
      + `<span class="mono" style="font-size:11px;margin-right:8px;color:var(--text-0)"> ${esc(val)}</span>`;
  }).join('');
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
    messages:'Messages', restaurants:'Restaurants', recipes:'Recipes',
    stats:'Stats', bids:'Market Bids'
  };
  $('panelTitle').textContent = titles[panel] || panel;
  $('liveIndicator').style.display = panel === 'events' ? 'flex' : 'none';

  if (panel === 'events')        loadEvents();
  if (panel === 'clients')       loadClients();
  if (panel === 'preparations')  loadPreparations();
  if (panel === 'messages')      loadMessages();
  if (panel === 'restaurants')   loadRestaurants();
  if (panel === 'recipes')       loadRecipes();
  if (panel === 'stats')         loadStats();
  if (panel === 'bids')          initBids();
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
    <td>${detailCell(e.event_type, e.detail)}</td>
  </tr>`).join('');
}

function appendNewEvents(events) {
  let phaseChanged = false;
  for (const e of events) {
    if (e.event_type === 'game_phase_changed' && e.detail && e.detail.phase) {
      updatePhaseBadge(e.detail.phase);
      phaseChanged = true;
    }
  }
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
        <td>${detailCell(e.event_type, e.detail)}</td>`;
      tbody.insertBefore(tr, tbody.firstChild);
    }
    const cnt = $('eventCount');
    cnt.textContent = parseInt(cnt.textContent) + events.length;
    if ($('autoScroll').checked) $('eventsTableWrap').scrollTop = 0;
  }
  if (currentPanel === 'clients')       loadClients();
  if (currentPanel === 'preparations')  loadPreparations();
  if (currentPanel === 'messages')      loadMessages();
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
// MARKET BIDS
// ---------------------------------------------------------------------------

// Populated once, then reused when switching turns
let _bidsInitialised = false;
let _bidsChart = null;       // Chart.js bubble chart instance
let _historyChart = null;    // Chart.js history line chart instance

// Per-turn bid data cache: turn_id (string) → API response object
const _bidsCache = {};

// Last rendered data — used by CSV exporters
let _lastBidsData   = null;  // { turn_id, restaurants, ingredients, bids }
let _lastHistoryData = null; // { ingredient, points }

// Ordered list of all known turn_ids (ascending numbers, for history fetching)
let _knownTurns = [];

// Distinct colours for up to 30 restaurants; cycles if more
const BUBBLE_PALETTE = [
  '#a78bfa','#60a5fa','#34d399','#fbbf24','#f87171',
  '#c084fc','#38bdf8','#4ade80','#fb923c','#f472b6',
  '#818cf8','#22d3ee','#a3e635','#facc15','#e879f9',
  '#6ee7b7','#93c5fd','#fca5a5','#fdba74','#d8b4fe',
  '#67e8f9','#86efac','#fde68a','#fca5e0','#a5b4fc',
  '#5eead4','#bef264','#fed7aa','#c4b5fd','#7dd3fc',
];

async function initBids() {
  if (!_bidsInitialised) {
    try {
      const data = await api('/api/turns');

      // Store turns ascending for history chart (fetch oldest→newest)
      _knownTurns = [...data.turn_ids].sort((a, b) => {
        const na = Number(a), nb = Number(b);
        return (!isNaN(na) && !isNaN(nb)) ? na - nb : String(a).localeCompare(String(b));
      });

      const sel = $('bidTurnSelect');
      sel.innerHTML = data.turn_ids.length === 0
        ? '<option value="">No turns available</option>'
        : [..._knownTurns]
            .reverse()                               // descending for display
            .map(t => `<option value="${esc(String(t))}">${esc(String(t))}</option>`).join('');

      if (data.turn_ids.length > 0) sel.selectedIndex = 0;
      _bidsInitialised = true;
    } catch (err) {
      const s = $('bidsStatus');
      s.textContent = '✕ Could not load turns: ' + String(err);
      s.style.color = 'var(--red)';
      return;
    }
  }
  await loadBids();
}

async function loadBids() {
  const sel = $('bidTurnSelect');
  const turnId = sel.value;
  const status = $('bidsStatus');
  const wrap = $('bidsWrap');
  const empty = $('bidsEmpty');

  status.textContent = 'Loading…';
  status.style.color = 'var(--text-2)';
  wrap.innerHTML = '';
  empty.style.display = 'none';
  _hideBidsChart();

  try {
    const url = turnId ? `/api/bids?turn_id=${encodeURIComponent(turnId)}` : '/api/bids';
    const data = await api(url);

    // Cache this turn's data for the history chart
    const cacheKey = String(data.turn_id ?? turnId);
    if (cacheKey && data.ingredients && data.ingredients.length) {
      _bidsCache[cacheKey] = data;
    }

    const { restaurants, ingredients, bids, error } = data;

    // Persist for CSV export
    _lastBidsData = { turn_id: data.turn_id ?? turnId, restaurants, ingredients, bids };

    if (error) {
      status.textContent = '⚠ ' + error;
      status.style.color = 'var(--amber)';
    }

    if (!restaurants || !restaurants.length || !ingredients || !ingredients.length) {
      wrap.innerHTML = '';
      empty.style.display = 'block';
      $('tableSizeControls').style.display = 'none';
      if (!error) status.textContent = 'No data';
      return;
    }

    status.textContent =
      `${ingredients.length} ingredient${ingredients.length !== 1 ? 's' : ''}` +
      ` × ${restaurants.length} restaurant${restaurants.length !== 1 ? 's' : ''}`;
    status.style.color = 'var(--text-2)';

    // ---- Populate ingredient history selector (merge with any already seen) ----
    _populateIngredientSelector(ingredients);

    // ---- Bubble chart ----
    _renderBidsChart(restaurants, ingredients, bids);

    // ---- Matrix table ----
    const headerCells = restaurants.map(r => {
      const short = r.replace(/^Restaurant\s+/, 'R.');
      return `<th title="${esc(r)}">${esc(short)}</th>`;
    }).join('');

    const rows = ingredients.map(ing => {
      const cells = restaurants.map(rest => {
        const bid = bids[ing] && bids[ing][rest];
        if (!bid) return `<td class="bid-cell"><span class="bid-empty">·</span></td>`;
        return `<td class="bid-cell">
          <div class="bid-inner">
            <span class="bid-price">${bid.unit_price}</span>
            <span class="bid-qty">×${bid.quantity}</span>
          </div>
        </td>`;
      }).join('');
      return `<tr><td class="ing-col">${esc(ing)}</td>${cells}</tr>`;
    }).join('');

    wrap.innerHTML = `
      <table class="bids-table">
        <thead><tr>
          <th class="ing-col" style="text-align:left">Ingredient</th>
          ${headerCells}
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;

    // Reveal the table size control strip
    $('tableSizeControls').style.display = 'flex';

  } catch (err) {
    status.textContent = '✕ ' + String(err);
    status.style.color = 'var(--red)';
    wrap.innerHTML = `<div class="empty"><p style="color:var(--red)">${esc(String(err))}</p></div>`;
  }
}

// Keep the ingredient <select> populated with union of all seen ingredients
function _populateIngredientSelector(ingredients) {
  const sel = $('ingHistorySelect');
  const current = new Set(Array.from(sel.options).map(o => o.value).filter(Boolean));
  const prev = sel.value;          // preserve selection across turns

  let added = false;
  ingredients.forEach(ing => {
    if (!current.has(ing)) {
      const opt = document.createElement('option');
      opt.value = ing;
      opt.textContent = ing;
      sel.appendChild(opt);
      added = true;
    }
  });

  // Restore selection if it still exists, otherwise leave placeholder
  if (prev && current.has(prev)) sel.value = prev;

  // Show history panel as soon as we have something to pick
  if (sel.options.length > 1) $('bidsHistoryWrap').style.display = 'block';
}

// ---------------------------------------------------------------------------
// Size controls
// ---------------------------------------------------------------------------

function resizeBubbleChart(px, btn) {
  // Update CSS custom property on the panel so both container + canvas resize
  document.getElementById('panel-bids').style.setProperty('--bubble-h', px + 'px');

  // Highlight active button
  document.querySelectorAll('#chartSizeBtns .size-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  // Tell Chart.js to recalculate — use a short timeout so the CSS transition
  // has time to commit before Chart.js measures the canvas parent
  setTimeout(() => { if (_bidsChart) _bidsChart.resize(); }, 220);
}

function resizeTable(px, btn) {
  document.getElementById('panel-bids').style.setProperty('--table-h', px + 'px');

  document.querySelectorAll('#tableSizeBtns .size-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

// ---------------------------------------------------------------------------
// Bubble chart helpers
// ---------------------------------------------------------------------------

function _hideBidsChart() {
  $('bidsChartWrap').style.display = 'none';
  if (_bidsChart) { _bidsChart.destroy(); _bidsChart = null; }
}

function _hideHistoryChart() {
  if (_historyChart) { _historyChart.destroy(); _historyChart = null; }
  // Don't hide the whole panel — just clear the canvas
}

// ---------------------------------------------------------------------------
// Ingredient weighted-average history chart
// ---------------------------------------------------------------------------

async function loadIngredientHistory() {
  const ingredient = $('ingHistorySelect').value;
  const statusEl = $('historyStatus');

  if (!ingredient) {
    _hideHistoryChart();
    statusEl.textContent = '';
    return;
  }

  statusEl.textContent = 'Fetching all turns…';
  statusEl.style.color = 'var(--text-2)';

  // Fetch any turns we haven't cached yet, in parallel batches of 5
  const missing = _knownTurns.filter(t => !_bidsCache[String(t)]);
  const BATCH = 5;
  for (let i = 0; i < missing.length; i += BATCH) {
    const batch = missing.slice(i, i + BATCH);
    await Promise.all(batch.map(async t => {
      try {
        const d = await api(`/api/bids?turn_id=${encodeURIComponent(t)}`);
        if (d && d.ingredients && d.ingredients.length) {
          _bidsCache[String(t)] = d;
          _populateIngredientSelector(d.ingredients); // expand selector if needed
        }
      } catch { /* skip bad turns silently */ }
    }));
    statusEl.textContent = `Fetching… ${Math.min(i + BATCH, missing.length)}/${missing.length}`;
  }

  // Compute weighted average price per turn for the selected ingredient
  const points = []; // {turn, wavg, totalQty}

  for (const t of _knownTurns) {
    const d = _bidsCache[String(t)];
    if (!d || !d.bids || !d.bids[ingredient]) continue;

    let totalCost = 0, totalQty = 0;
    for (const rest of d.restaurants) {
      const bid = d.bids[ingredient][rest];
      if (!bid) continue;
      totalCost += bid.unit_price * bid.quantity;
      totalQty  += bid.quantity;
    }
    if (totalQty === 0) continue;

    points.push({
      turn: t,
      wavg: parseFloat((totalCost / totalQty).toFixed(2)),
      totalQty,
    });
  }

  if (!points.length) {
    statusEl.textContent = `No bids found for "${ingredient}"`;
    statusEl.style.color = 'var(--amber)';
    _hideHistoryChart();
    return;
  }

  statusEl.textContent = `${points.length} turn${points.length !== 1 ? 's' : ''} with data`;
  statusEl.style.color = 'var(--text-2)';
  _lastHistoryData = { ingredient, points };
  _renderHistoryChart(ingredient, points);
}

function _renderHistoryChart(ingredient, points) {
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor  = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.07)';
  const labelColor = isDark ? '#a0a0a8' : '#52525b';
  const tooltipBg  = isDark ? '#19191c' : '#ffffff';
  const tooltipFg  = isDark ? '#ececef' : '#18181b';
  const lineColor  = '#a78bfa';  // purple — matches the accent colour

  _hideHistoryChart();

  const ctx = $('bidsHistoryChart').getContext('2d');

  _historyChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: points.map(p => `T${p.turn}`),
      datasets: [{
        label: 'Weighted avg price',
        data: points.map(p => p.wavg),
        borderColor: lineColor,
        backgroundColor: lineColor + '22',
        borderWidth: 2,
        pointRadius: points.map(p => {
          // scale point radius by quantity (4–14 px)
          const maxQ = Math.max(...points.map(x => x.totalQty));
          return 4 + ((p.totalQty / maxQ) * 10);
        }),
        pointHoverRadius: points.map(p => {
          const maxQ = Math.max(...points.map(x => x.totalQty));
          return 6 + ((p.totalQty / maxQ) * 10);
        }),
        pointBackgroundColor: lineColor + 'cc',
        pointBorderColor: lineColor,
        pointBorderWidth: 1.5,
        fill: true,
        tension: 0.35,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      layout: { padding: { top: 8, right: 8 } },
      scales: {
        x: {
          ticks: {
            color: labelColor,
            font: { family: "'JetBrains Mono', monospace", size: 10 },
            maxRotation: 0,
          },
          grid: { color: gridColor },
          title: {
            display: true,
            text: 'Turn',
            color: labelColor,
            font: { family: "'Inter', sans-serif", size: 11, weight: '500' },
          },
        },
        y: {
          beginAtZero: false,
          ticks: {
            color: labelColor,
            font: { family: "'JetBrains Mono', monospace", size: 10 },
          },
          grid: { color: gridColor },
          title: {
            display: true,
            text: 'Weighted Avg Price',
            color: labelColor,
            font: { family: "'Inter', sans-serif", size: 11, weight: '500' },
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: tooltipBg,
          borderColor: isDark ? '#2a2a2e' : '#d4d4d8',
          borderWidth: 1,
          titleColor: tooltipFg,
          bodyColor: labelColor,
          titleFont: { family: "'Inter', sans-serif", size: 12, weight: '600' },
          bodyFont:  { family: "'JetBrains Mono', monospace", size: 11 },
          padding: 10,
          callbacks: {
            title: (items) => `Turn ${points[items[0].dataIndex].turn}`,
            label: (item) => {
              const p = points[item.dataIndex];
              return [
                `Wtd avg price : ${p.wavg}`,
                `Total qty     : ${p.totalQty}`,
              ];
            },
          },
        },
      },
    },
  });
}

function _renderBidsChart(restaurants, ingredients, bids) {
  // Detect theme for grid/label colours
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const gridColor  = isDark ? 'rgba(255,255,255,0.07)' : 'rgba(0,0,0,0.07)';
  const labelColor = isDark ? '#a0a0a8' : '#52525b';
  const tooltipBg  = isDark ? '#19191c' : '#ffffff';
  const tooltipFg  = isDark ? '#ececef' : '#18181b';

  // Find max quantity for bubble radius scaling
  let maxQty = 1;
  for (const ing of ingredients) {
    for (const rest of restaurants) {
      const bid = bids[ing] && bids[ing][rest];
      if (bid && bid.quantity > maxQty) maxQty = bid.quantity;
    }
  }

  // One dataset per restaurant — each point: {x: ingIndex, y: price, r: scaled}
  const MIN_R = 4, MAX_R = 22;
  const datasets = restaurants.map((rest, ri) => {
    const colour = BUBBLE_PALETTE[ri % BUBBLE_PALETTE.length];
    const points = [];
    ingredients.forEach((ing, ii) => {
      const bid = bids[ing] && bids[ing][rest];
      if (!bid) return;
      const r = MIN_R + ((bid.quantity / maxQty) * (MAX_R - MIN_R));
      points.push({ x: ii, y: bid.unit_price, r, _qty: bid.quantity, _ing: ing });
    });
    return {
      label: rest,
      data: points,
      backgroundColor: colour + 'bb', // ~73% opacity
      borderColor: colour,
      borderWidth: 1.5,
    };
  }).filter(ds => ds.data.length > 0);

  if (_bidsChart) { _bidsChart.destroy(); _bidsChart = null; }

  $('bidsChartWrap').style.display = 'block';
  const ctx = $('bidsChart').getContext('2d');

  _bidsChart = new Chart(ctx, {
    type: 'bubble',
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      layout: { padding: { top: 24, right: 16, bottom: 8, left: 8 } },
      scales: {
        x: {
          type: 'linear',
          min: -0.5,
          max: ingredients.length - 0.5,
          ticks: {
            stepSize: 1,
            color: labelColor,
            font: { family: "'JetBrains Mono', monospace", size: 10 },
            callback: (val) => {
              const idx = Math.round(val);
              if (idx < 0 || idx >= ingredients.length) return '';
              // Truncate long ingredient names
              const name = ingredients[idx];
              return name.length > 18 ? name.slice(0, 16) + '…' : name;
            },
            maxRotation: 35,
            minRotation: 25,
          },
          grid: { color: gridColor },
        },
        y: {
          beginAtZero: true,
          grace: '8%',
          ticks: {
            color: labelColor,
            font: { family: "'JetBrains Mono', monospace", size: 10 },
          },
          grid: { color: gridColor },
          title: {
            display: true,
            text: 'Unit Price',
            color: labelColor,
            font: { family: "'Inter', sans-serif", size: 11, weight: '500' },
          },
        },
      },
      plugins: {
        legend: {
          display: true,
          position: 'right',
          labels: {
            color: labelColor,
            font: { family: "'Inter', sans-serif", size: 11 },
            boxWidth: 10,
            boxHeight: 10,
            padding: 8,
            usePointStyle: true,
            pointStyle: 'circle',
          },
        },
        tooltip: {
          backgroundColor: tooltipBg,
          borderColor: isDark ? '#2a2a2e' : '#d4d4d8',
          borderWidth: 1,
          titleColor: tooltipFg,
          bodyColor: labelColor,
          titleFont: { family: "'Inter', sans-serif", size: 12, weight: '600' },
          bodyFont:  { family: "'JetBrains Mono', monospace", size: 11 },
          padding: 10,
          callbacks: {
            title: (items) => items[0]?.dataset?.label || '',
            label: (item) => {
              const d = item.raw;
              return [
                `Ingredient : ${d._ing}`,
                `Unit price : ${d.y}`,
                `Quantity   : ${d._qty}`,
              ];
            },
          },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// CSV Export helpers
// ---------------------------------------------------------------------------

function _downloadCSV(filename, csvContent) {
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function _csvRow(fields) {
  return fields.map(f => {
    const s = f == null ? '' : String(f);
    // Quote fields that contain commas, quotes, or newlines
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(',');
}

// 1. Bubble chart — flat bid rows for current turn
function exportBubbleCSV() {
  if (!_lastBidsData || !_lastBidsData.ingredients) {
    alert('No bid data to export. Load a turn first.');
    return;
  }
  const { turn_id, restaurants, ingredients, bids } = _lastBidsData;
  const rows = [_csvRow(['turn_id', 'ingredient', 'restaurant', 'unit_price', 'quantity'])];
  for (const ing of ingredients) {
    for (const rest of restaurants) {
      const bid = bids[ing] && bids[ing][rest];
      if (!bid) continue;
      rows.push(_csvRow([turn_id, ing, rest, bid.unit_price, bid.quantity]));
    }
  }
  _downloadCSV(`bids_turn_${turn_id}.csv`, rows.join('\n'));
}

// 2. History chart — weighted avg price per turn for selected ingredient
function exportHistoryCSV() {
  if (!_lastHistoryData || !_lastHistoryData.points || !_lastHistoryData.points.length) {
    alert('No history data to export. Select an ingredient first.');
    return;
  }
  const { ingredient, points } = _lastHistoryData;
  const rows = [_csvRow(['turn_id', 'ingredient', 'weighted_avg_price', 'total_quantity'])];
  for (const p of points) {
    rows.push(_csvRow([p.turn, ingredient, p.wavg, p.totalQty]));
  }
  const safeName = ingredient.replace(/[^a-z0-9_\-]/gi, '_');
  _downloadCSV(`history_${safeName}.csv`, rows.join('\n'));
}

// 3. Matrix table — ingredients × restaurants (wide format)
function exportMatrixCSV() {
  if (!_lastBidsData || !_lastBidsData.ingredients) {
    alert('No bid data to export. Load a turn first.');
    return;
  }
  const { turn_id, restaurants, ingredients, bids } = _lastBidsData;
  // Wide format: ingredient, then one col per restaurant with "price × qty"
  const headerFields = ['ingredient', ...restaurants.map(r => r + '_price'), ...restaurants.map(r => r + '_qty')];
  const rows = [_csvRow(headerFields)];
  for (const ing of ingredients) {
    const prices = restaurants.map(rest => {
      const bid = bids[ing] && bids[ing][rest];
      return bid ? bid.unit_price : '';
    });
    const qtys = restaurants.map(rest => {
      const bid = bids[ing] && bids[ing][rest];
      return bid ? bid.quantity : '';
    });
    rows.push(_csvRow([ing, ...prices, ...qtys]));
  }
  _downloadCSV(`matrix_turn_${turn_id}.csv`, rows.join('\n'));
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
  if (currentPanel === 'clients')       await loadClients();
  if (currentPanel === 'preparations')  await loadPreparations();
  if (currentPanel === 'messages')      await loadMessages();
  if (currentPanel === 'restaurants')   await loadRestaurants();
  if (currentPanel === 'stats')         await loadStats();
  if (currentPanel === 'recipes')       { allRecipes=[]; await loadRecipes(); }
  if (currentPanel === 'bids') {
    _bidsInitialised = false;
    _hideBidsChart();
    _hideHistoryChart();
    Object.keys(_bidsCache).forEach(k => delete _bidsCache[k]);
    // Reset ingredient selector to placeholder
    const sel = $('ingHistorySelect');
    sel.innerHTML = '<option value="">Select an ingredient…</option>';
    $('bidsHistoryWrap').style.display = 'none';
    $('tableSizeControls').style.display = 'none';
    $('historyStatus').textContent = '';
    await initBids();
  }
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