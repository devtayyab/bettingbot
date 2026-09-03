"""Self-contained dashboard (no external assets) served at GET /."""

DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ValueBet Pilot</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; background:#0f1115; color:#e6e6e6; }
  header { padding: 16px 24px; background:#161a22; border-bottom:1px solid #232836; display:flex; gap:24px; align-items:center; flex-wrap:wrap;}
  h1 { font-size: 18px; margin:0; }
  .pill { font-size:12px; padding:3px 8px; border-radius:999px; background:#232836; }
  .pill.warn { background:#5a3a12; color:#ffce8a; }
  .pill.live { background:#1a4a2a; color:#4ade80; }
  main { padding: 24px; max-width: 1100px; margin:0 auto; }
  .cards { display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }
  .card { background:#161a22; border:1px solid #232836; border-radius:10px; padding:16px 20px; min-width:150px; }
  .card .v { font-size:22px; font-weight:600; }
  .card .l { font-size:12px; color:#8a93a6; }
  .pos { color:#4ade80; } .neg { color:#f87171; }
  table { width:100%; border-collapse: collapse; font-size:14px; }
  th, td { text-align:left; padding:10px 8px; border-bottom:1px solid #232836; }
  th { color:#8a93a6; font-weight:500; }
  button { cursor:pointer; border:none; border-radius:6px; padding:6px 10px; font-size:13px; margin-right:6px; transition: opacity 0.2s; }
  button:disabled { opacity:0.5; cursor:not-allowed; }
  .approve { background:#1f6f43; color:#fff; } .reject { background:#7a2230; color:#fff; }
  .place { background:#2b5cb8; color:#fff; }
  .bar { display:flex; gap:12px; align-items:center; margin-bottom:12px; flex-wrap:wrap;}
  select, .toolbtn { background:#161a22; color:#e6e6e6; border:1px solid #232836; border-radius:6px; padding:6px 10px; }
  .edge { font-weight:600; color:#4ade80; }
  .status { font-size:12px; padding:2px 8px; border-radius:999px; background:#232836; }
  /* Toast */
  #toast-container { position:fixed; bottom:24px; right:24px; display:flex; flex-direction:column; gap:8px; z-index:9999; }
  .toast { padding:12px 18px; border-radius:8px; font-size:13px; max-width:380px; box-shadow:0 4px 16px #0008;
           animation: slideIn 0.25s ease; line-height:1.5; }
  .toast.ok  { background:#1a4a2a; border:1px solid #2d7a48; color:#a3f0b8; }
  .toast.err { background:#4a1a1a; border:1px solid #7a2d2d; color:#f0a3a3; }
  .toast.inf { background:#1a2a4a; border:1px solid #2d4a7a; color:#a3c0f0; }
  @keyframes slideIn { from { opacity:0; transform:translateY(16px);} to { opacity:1; transform:translateY(0);} }
  /* Spinner */
  .spin { display:inline-block; width:12px; height:12px; border:2px solid #ffffff44; border-top-color:#fff;
          border-radius:50%; animation:spin 0.7s linear infinite; vertical-align:middle; margin-right:4px; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<header>
  <h1>⚡ ValueBet Pilot</h1>
  <span class="pill" id="env">env: …</span>
  <span class="pill warn" id="mode">…</span>
</header>
<div id="toast-container"></div>
<main>
  <div class="cards" id="pnl"></div>

  <div class="bar">
    <select id="sport-select">
      <option value="all">all sports</option>
      <option value="soccer">soccer</option>
      <option value="tennis">tennis</option>
      <option value="basketball">basketball</option>
      <option value="american_football">american_football</option>
      <option value="baseball">baseball</option>
      <option value="ice_hockey">ice_hockey</option>
      <option value="cricket">cricket</option>
      <option value="rugby_league">rugby_league</option>
      <option value="rugby_union">rugby_union</option>
      <option value="golf">golf</option>
      <option value="mma">mma</option>
      <option value="boxing">boxing</option>
      <option value="volleyball">volleyball</option>
      <option value="handball">handball</option>
      <option value="darts">darts</option>
      <option value="esports">esports</option>
      <option value="table_tennis">table_tennis</option>
    </select>
    <button id="btn-prematch" class="toolbtn" onclick="scanSelected(false)">Scan (pre-match)</button>
    <button id="btn-live" class="toolbtn" onclick="scanSelected(true)">Scan (live)</button>
    <select id="filter" onchange="loadSignals()">
      <option value="">all signals</option>
      <option value="detected">detected</option>
      <option value="approved">approved</option>
      <option value="placed">placed</option>
      <option value="rejected">rejected</option>
      <option value="failed">failed</option>
    </select>
    <button id="btn-refresh" class="toolbtn" onclick="loadAll()">↻ Refresh</button>
  </div>
  <div id="status-bar" style="font-size:12px; color:#8a93a6; margin-bottom:12px;">Ready.</div>

  <!-- Stoiximan Session Panel -->
  <div id="session-panel" style="background:#161a22; border:1px solid #232836; border-radius:10px; padding:16px 20px; margin-bottom:20px;">
    <div style="display:flex; align-items:center; gap:12px; margin-bottom:10px; flex-wrap:wrap;">
      <strong style="font-size:14px;">🔐 Stoiximan Session & Cookies JSON</strong>
      <span id="cookie-status-badge" class="pill">checking…</span>
      <div style="margin-left:auto; display:flex; gap:8px;">
        <button class="toolbtn" onclick="exportCookies()" style="font-size:12px;">📥 View / Export JSON</button>
        <button class="toolbtn" onclick="clearCookies()" style="font-size:12px; background:#4a1a1a; color:#f0a3a3;">🗑 Clear Session</button>
      </div>
    </div>
    <div id="cookie-import-area">
      <p style="font-size:12px; color:#8a93a6; margin:0 0 8px 0; line-height:1.4;">
        <b>Automatic Login:</b> Run in terminal <code>python -m valuebet.cli login-stoiximan</code> (browser will open and cookies will be saved).<br/>
        <b>Manual Import:</b> Login in browser → <i>Cookie-Editor extension</i> → Export as JSON → paste here:
      </p>
      <textarea id="cookie-input" rows="3" placeholder='[{"name":"session","value":"abc...","domain":".stoiximan.com.cy",...}]'
        style="width:100%; box-sizing:border-box; background:#0f1115; color:#e6e6e6; border:1px solid #232836; border-radius:6px; padding:8px; font-size:12px; font-family:monospace; resize:vertical;"></textarea>
      <button id="btn-import" class="toolbtn" onclick="importCookies()" style="margin-top:8px; background:#2b5cb8; color:#fff;">⬆ Import & Save JSON Cookies</button>
    </div>
  </div>

  <table>
    <thead><tr>
      <th>Selection</th><th>Sport</th><th>Fair p</th><th>Pinnacle p</th>
      <th>Odds</th><th>Edge</th><th>Stake</th><th>Status</th><th>Actions</th>
    </tr></thead>
    <tbody id="rows"></tbody>
  </table>
</main>
<script>
function toast(msg, type='inf', ms=5000){
  const c = document.getElementById('toast-container');
  const d = document.createElement('div');
  d.className = 'toast '+type;
  d.textContent = msg;
  c.appendChild(d);
  setTimeout(()=>{ d.style.opacity='0'; d.style.transition='opacity 0.4s'; setTimeout(()=>d.remove(), 400); }, ms);
}
function setStatus(msg){ document.getElementById('status-bar').textContent = msg; }

async function api(url, opts={}){
  try {
    const r = await fetch(url, opts);
    const data = await r.json();
    if(!r.ok){ toast('Error: ' + (data.detail || JSON.stringify(data)), 'err', 8000); throw new Error(JSON.stringify(data)); }
    return data;
  } catch(e) {
    if(!(e instanceof Error && e.message.startsWith('{'))) toast('Network error: '+e.message, 'err', 8000);
    throw e;
  }
}

function pct(x){ return (x*100).toFixed(2)+'%'; }
function setBtn(id, loading, label){ const b=document.getElementById(id); if(!b) return; b.disabled=loading; b.innerHTML=loading?`<span class="spin"></span>${label}…`:label; }

async function loadHealth(){
  const h = await api('/health');
  document.getElementById('env').textContent = 'env: '+h.env;
  const m = document.getElementById('mode');
  if(h.dry_run){ m.className='pill warn'; m.textContent='DRY-RUN (no real bets)'; }
  else { m.className='pill live'; m.textContent='🟢 LIVE PLACEMENT'; }
}

async function loadPnl(){
  const p = await api('/pnl');
  const cls = p.realised_pnl >= 0 ? 'pos':'neg';
  document.getElementById('pnl').innerHTML = `
    <div class="card"><div class="v ${cls}">${p.realised_pnl.toFixed(2)}</div><div class="l">Realised P&L</div></div>
    <div class="card"><div class="v">${pct(p.roi)}</div><div class="l">ROI (settled)</div></div>
    <div class="card"><div class="v">${p.bets_settled}/${p.bets_total}</div><div class="l">Settled / Total bets</div></div>
    <div class="card"><div class="v">${p.open_exposure.toFixed(2)}</div><div class="l">Open exposure</div></div>`;
}

async function loadSignals(){
  const f = document.getElementById('filter').value;
  const sigs = await api('/signals'+(f?('?status='+f):''));
  const rows = sigs.map(s => `
    <tr>
      <td>${s.selection}</td><td>${s.sport}</td>
      <td>${pct(s.fair_prob)}</td><td>${s.confirm_prob!=null?pct(s.confirm_prob):'—'}</td>
      <td>${s.target_odds.toFixed(2)}</td>
      <td class="edge">${pct(s.edge)}</td>
      <td>${s.recommended_stake.toFixed(2)}</td>
      <td><span class="status">${s.status}</span></td>
      <td>
        ${s.status==='detected'?`<button class="approve" onclick="act(${s.id},'approve')">Approve</button>
          <button class="reject" onclick="act(${s.id},'reject')">Reject</button>`:''}
        ${s.status==='approved'?`<button class="place" onclick="place(${s.id},this)">Place</button>`:''}
      </td>
    </tr>`).join('');
  document.getElementById('rows').innerHTML = rows || '<tr><td colspan="9" style="color:#8a93a6;">No signals.</td></tr>';
}

async function act(id, what){
  try {
    await api('/signals/'+id+'/'+what, {method:'POST'});
    toast('Signal '+id+' → '+what, 'ok');
    loadSignals();
  } catch(e){}
}

async function place(id, btn){
  btn.disabled=true; btn.innerHTML='<span class="spin"></span>Placing…';
  try {
    const r = await api('/signals/'+id+'/place', {method:'POST', headers:{'content-type':'application/json'}, body:'{}'});
    const prefix = r.dry_run ? '[DRY-RUN] ' : '';
    const msg = prefix + (r.message||'') + (r.placed_odds ? (' @ '+r.placed_odds) : '');
    toast(msg, r.success ? 'ok' : 'err', 8000);
    loadAll();
  } catch(e){ btn.disabled=false; btn.textContent='Place'; }
}

async function scan(sport, live){
  const btnId = live ? 'btn-live' : 'btn-prematch';
  const label = live ? 'Scan (live)' : 'Scan (pre-match)';
  setBtn(btnId, true, label);
  setStatus('Scanning '+sport+' ('+(live?'live':'pre-match')+')…');
  try {
    const r = await api('/scan?sport='+sport+'&live='+live, {method:'POST'});
    const msg = 'Scan complete — new signals: ' + r.new_signals + (r.error ? ' | ⚠ '+r.error : '');
    toast(msg, r.new_signals > 0 ? 'ok' : 'inf');
    setStatus(msg);
    loadAll();
  } catch(e){ setStatus('Scan failed. See error above.'); }
  finally { setBtn(btnId, false, label); }
}

function scanSelected(live){ const s = document.getElementById('sport-select').value; scan(s, live); }

async function loadCookieStatus(){
  try {
    const r = await api('/cookie-status');
    const badge = document.getElementById('cookie-status-badge');
    if(r.has_cookies){
      badge.style.background='#1a4a2a'; badge.style.color='#4ade80';
      badge.textContent = `✅ ${r.cookie_count} cookies saved · ${r.age_hours}h ago`;
    } else {
      badge.style.background='#5a3a12'; badge.style.color='#ffce8a';
      badge.textContent = '⚠ No session JSON found';
    }
  } catch(e){}
}

async function importCookies(){
  const raw = document.getElementById('cookie-input').value.trim();
  if(!raw){ toast('Paste cookies JSON array first!', 'err'); return; }
  let cookies;
  try { cookies = JSON.parse(raw); } catch(e){ toast('Invalid JSON! Check formatting and copy-paste again.', 'err', 8000); return; }
  if(!Array.isArray(cookies)){ toast('Must be a JSON array of cookies [ ... ]', 'err'); return; }
  setBtn('btn-import', true, '⬆ Import & Save JSON Cookies');
  try {
    const r = await api('/import-cookies', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({cookies})});
    toast(r.message, 'ok', 8000);
    document.getElementById('cookie-input').value = '';
    loadCookieStatus();
  } catch(e){}
  finally { setBtn('btn-import', false, '⬆ Import & Save JSON Cookies'); }
}

async function exportCookies(){
  try {
    const cookies = await api('/export-cookies');
    if(!cookies || cookies.length === 0){
      toast('No cookies currently stored in JSON file.', 'inf');
      return;
    }
    const formatted = JSON.stringify(cookies, null, 2);
    document.getElementById('cookie-input').value = formatted;
    if(navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(formatted);
      toast(`📋 ${cookies.length} cookies loaded into text box & copied to clipboard!`, 'ok');
    } else {
      toast(`📋 ${cookies.length} cookies loaded into text box!`, 'ok');
    }
  } catch(e){ toast('Failed to export cookies: ' + e.message, 'err'); }
}

async function clearCookies(){
  if(!confirm('Delete Stoiximan cookies JSON? You will need to login the bot again.')) return;
  try {
    const r = await api('/import-cookies', {method:'DELETE'});
    toast(r.message, 'inf');
    document.getElementById('cookie-input').value = '';
    loadCookieStatus();
  } catch(e){}
}

async function loadAll(){
  setBtn('btn-refresh', true, '↻ Refresh');
  try { await Promise.all([loadHealth(), loadPnl(), loadSignals(), loadCookieStatus()]); setStatus('Last updated: '+new Date().toLocaleTimeString()); }
  catch(e){ setStatus('Refresh failed.'); }
  finally { setBtn('btn-refresh', false, '↻ Refresh'); }
}

loadAll();
setInterval(loadPnl, 15000);
</script>
</body>
</html>
"""
