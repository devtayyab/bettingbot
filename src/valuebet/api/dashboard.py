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
  main { padding: 24px; max-width: 1100px; margin:0 auto; }
  .cards { display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px; }
  .card { background:#161a22; border:1px solid #232836; border-radius:10px; padding:16px 20px; min-width:150px; }
  .card .v { font-size:22px; font-weight:600; }
  .card .l { font-size:12px; color:#8a93a6; }
  .pos { color:#4ade80; } .neg { color:#f87171; }
  table { width:100%; border-collapse: collapse; font-size:14px; }
  th, td { text-align:left; padding:10px 8px; border-bottom:1px solid #232836; }
  th { color:#8a93a6; font-weight:500; }
  button { cursor:pointer; border:none; border-radius:6px; padding:6px 10px; font-size:13px; margin-right:6px; }
  .approve { background:#1f6f43; color:#fff; } .reject { background:#7a2230; color:#fff; }
  .place { background:#2b5cb8; color:#fff; }
  .bar { display:flex; gap:12px; align-items:center; margin-bottom:16px; flex-wrap:wrap;}
  select, .toolbtn { background:#161a22; color:#e6e6e6; border:1px solid #232836; border-radius:6px; padding:6px 10px; }
  .edge { font-weight:600; color:#4ade80; }
  .status { font-size:12px; padding:2px 8px; border-radius:999px; background:#232836; }
</style>
</head>
<body>
<header>
  <h1>⚡ ValueBet Pilot</h1>
  <span class="pill" id="env">env: …</span>
  <span class="pill warn" id="mode">…</span>
</header>
<main>
  <div class="cards" id="pnl"></div>

  <div class="bar">
    <button class="toolbtn" onclick="scan('soccer',false)">Scan soccer (pre-match)</button>
    <button class="toolbtn" onclick="scan('soccer',true)">Scan soccer (live)</button>
    <select id="filter" onchange="loadSignals()">
      <option value="">all signals</option>
      <option value="detected">detected</option>
      <option value="approved">approved</option>
      <option value="placed">placed</option>
      <option value="rejected">rejected</option>
    </select>
    <button class="toolbtn" onclick="loadAll()">Refresh</button>
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
async function j(url, opts){ const r = await fetch(url, opts); if(!r.ok){ const t=await r.text(); alert(t); throw new Error(t);} return r.json(); }
function pct(x){ return (x*100).toFixed(2)+'%'; }

async function loadHealth(){
  const h = await j('/health');
  document.getElementById('env').textContent = 'env: '+h.env;
  document.getElementById('mode').textContent = h.dry_run ? 'DRY-RUN (no real bets)' : 'LIVE PLACEMENT';
}
async function loadPnl(){
  const p = await j('/pnl');
  const cls = p.realised_pnl >= 0 ? 'pos':'neg';
  document.getElementById('pnl').innerHTML = `
    <div class="card"><div class="v ${cls}">${p.realised_pnl.toFixed(2)}</div><div class="l">Realised P&L</div></div>
    <div class="card"><div class="v">${pct(p.roi)}</div><div class="l">ROI (settled)</div></div>
    <div class="card"><div class="v">${p.bets_settled}/${p.bets_total}</div><div class="l">Settled / Total bets</div></div>
    <div class="card"><div class="v">${p.open_exposure.toFixed(2)}</div><div class="l">Open exposure</div></div>`;
}
async function loadSignals(){
  const f = document.getElementById('filter').value;
  const sigs = await j('/signals'+(f?('?status='+f):''));
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
        ${s.status==='approved'?`<button class="place" onclick="place(${s.id})">Place</button>`:''}
      </td>
    </tr>`).join('');
  document.getElementById('rows').innerHTML = rows || '<tr><td colspan="9">No signals.</td></tr>';
}
async function act(id, what){ await j('/signals/'+id+'/'+what, {method:'POST'}); loadSignals(); }
async function place(id){
  const r = await j('/signals/'+id+'/place', {method:'POST', headers:{'content-type':'application/json'}, body:'{}'});
  alert((r.dry_run?'[DRY-RUN] ':'')+r.message+(r.placed_odds?(' @ '+r.placed_odds):'')); loadAll();
}
async function scan(sport, live){ const r = await j('/scan?sport='+sport+'&live='+live, {method:'POST'}); alert('New signals: '+r.new_signals); loadAll(); }
async function loadAll(){ await loadHealth(); await loadPnl(); await loadSignals(); }
loadAll();
setInterval(loadPnl, 15000);
</script>
</body>
</html>
"""
