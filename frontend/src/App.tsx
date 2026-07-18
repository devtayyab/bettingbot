import { useState, useEffect } from 'react';
import './App.css';

interface Signal {
  id: number;
  selection: string;
  sport: string;
  fair_prob: number;
  confirm_prob: number | null;
  target_odds: number;
  edge: number;
  recommended_stake: number;
  status: string;
}

interface PnlSummary {
  realised_pnl: number;
  roi: number;
  bets_settled: number;
  bets_total: number;
  open_exposure: number;
}

function App() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);

  // Vite proxy ensures /api routes to FastAPI
  const API_URL = '/api';

  const fetchData = async () => {
    try {
      const sigsRes = await fetch(`${API_URL}/signals${filter ? `?status=${filter}` : ''}`);
      if (sigsRes.ok) {
        setSignals(await sigsRes.json());
      }
      
      const pnlRes = await fetch(`${API_URL}/pnl`);
      if (pnlRes.ok) {
        setPnl(await pnlRes.json());
      }
    } catch (err) {
      console.error("Failed to fetch data", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [filter]);

  const handleAction = async (id: number, action: 'approve' | 'reject' | 'place') => {
    try {
      const res = await fetch(`${API_URL}/signals/${id}/${action}`, {
        method: 'POST',
        headers: action === 'place' ? { 'Content-Type': 'application/json' } : {},
        body: action === 'place' ? JSON.stringify({}) : undefined,
      });
      if (res.ok) {
        fetchData();
      } else {
        const err = await res.json();
        alert(`Action failed: ${err.detail || 'Unknown error'}`);
      }
    } catch (err) {
      alert("Network error");
    }
  };

  const pct = (x: number) => (x * 100).toFixed(2) + '%';

  return (
    <div className="app-container">
      <header className="header glass-panel">
        <div className="header-title">
          <span>⚡</span>
          <h1>ValueBet Pilot</h1>
        </div>
        <div className="status-badges">
          <span className="badge active">API Connected</span>
          <span className="badge active">Stream Active</span>
        </div>
      </header>

      {pnl && (
        <div className="metrics-grid">
          <div className="metric-card glass-panel">
            <span className="metric-label">Realised P&L</span>
            <span className={`metric-value ${pnl.realised_pnl >= 0 ? 'positive' : 'negative'}`}>
              €{pnl.realised_pnl.toFixed(2)}
            </span>
          </div>
          <div className="metric-card glass-panel">
            <span className="metric-label">ROI (Settled)</span>
            <span className="metric-value">{pct(pnl.roi)}</span>
          </div>
          <div className="metric-card glass-panel">
            <span className="metric-label">Settled / Total</span>
            <span className="metric-value">{pnl.bets_settled} / {pnl.bets_total}</span>
          </div>
          <div className="metric-card glass-panel">
            <span className="metric-label">Open Exposure</span>
            <span className="metric-value">€{pnl.open_exposure.toFixed(2)}</span>
          </div>
        </div>
      )}

      <div className="glass-panel" style={{ padding: '24px' }}>
        <div className="toolbar">
          <select 
            className="toolbtn" 
            value={filter} 
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="">All Signals</option>
            <option value="detected">Detected</option>
            <option value="approved">Approved</option>
            <option value="placed">Placed</option>
            <option value="rejected">Rejected</option>
          </select>
          <button className="btn btn-primary" onClick={fetchData}>
            {loading ? <span className="loader">↻</span> : 'Refresh'}
          </button>
        </div>

        <div className="table-container">
          <table className="signals-table">
            <thead>
              <tr>
                <th>Selection</th>
                <th>Sport</th>
                <th>Fair P</th>
                <th>Conf P</th>
                <th>Odds</th>
                <th>Edge</th>
                <th>Stake</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {signals.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                    No signals found.
                  </td>
                </tr>
              ) : (
                signals.map(s => (
                  <tr key={s.id}>
                    <td style={{ fontWeight: 500 }}>{s.selection}</td>
                    <td>{s.sport}</td>
                    <td>{pct(s.fair_prob)}</td>
                    <td>{s.confirm_prob ? pct(s.confirm_prob) : '—'}</td>
                    <td>{s.target_odds.toFixed(2)}</td>
                    <td className={s.edge > 0.08 ? 'edge-high' : 'edge-medium'}>
                      {pct(s.edge)}
                    </td>
                    <td>€{s.recommended_stake.toFixed(2)}</td>
                    <td>
                      <span className={`status-pill status-${s.status}`}>
                        {s.status}
                      </span>
                    </td>
                    <td>
                      <div className="action-group">
                        {s.status === 'detected' && (
                          <>
                            <button className="btn btn-success" onClick={() => handleAction(s.id, 'approve')}>Approve</button>
                            <button className="btn btn-danger" onClick={() => handleAction(s.id, 'reject')}>Reject</button>
                          </>
                        )}
                        {s.status === 'approved' && (
                          <button className="btn btn-primary" onClick={() => handleAction(s.id, 'place')}>Place</button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default App;
