import { useState, useEffect } from 'react';
import { Nav } from './components/Nav';
import { LiveFeed } from './pages/LiveFeed';
import { Accounts } from './pages/Accounts';
import { Reports } from './pages/Reports';
import { Settings } from './pages/Settings';
import './App.css';

const API = '/api';

interface PnlSummary {
  realised_pnl: number;
  roi: number;
  bets_settled: number;
  bets_total: number;
  open_exposure: number;
  paper_bets: number;
  unconfirmed_bets: number;
}

interface HealthData {
  env: string;
  dry_run: boolean;
  require_approval: boolean;
  demo_fallback: boolean;
  has_session_cookies: boolean;
  unconfirmed_bets?: number;
}

function App() {
  const [page, setPage] = useState('feed');
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);

  useEffect(() => {
    const fetchMeta = async () => {
      const [pnlRes, healthRes] = await Promise.all([
        fetch(`${API}/pnl`),
        fetch(`${API}/health`),
      ]);
      if (pnlRes.ok) setPnl(await pnlRes.json());
      if (healthRes.ok) setHealth(await healthRes.json());
    };
    fetchMeta();
    const interval = setInterval(fetchMeta, 30_000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="shell">
      <Nav active={page} onChange={setPage} />

      <div className="main-area">
        {/* Top status bar */}
        <div className="topbar glass-panel-solid">
          <div className="topbar-left">
            {health && (
              <>
                <span className={`badge-dot ${health.dry_run ? 'neutral' : 'positive'}`}>
                  {health.dry_run ? 'Paper Mode' : 'Live Mode'}
                </span>
                <span className={`badge-dot ${health.has_session_cookies ? 'positive' : 'negative'}`}>
                  {health.has_session_cookies ? 'Session OK' : 'No Session'}
                </span>
                {(health.unconfirmed_bets ?? 0) > 0 && (
                  <span className="badge-dot" style={{ color: 'var(--status-warning)' }}>
                    {health.unconfirmed_bets} unconfirmed
                  </span>
                )}
              </>
            )}
          </div>

          {pnl && (
            <div className="topbar-pnl">
              <div className="topbar-stat">
                <span className="topbar-stat__label">P&amp;L</span>
                <span className={`topbar-stat__value ${pnl.realised_pnl >= 0 ? 'positive' : 'negative'}`}>
                  {pnl.realised_pnl >= 0 ? '+' : ''}€{pnl.realised_pnl.toFixed(2)}
                </span>
              </div>
              <div className="topbar-divider" />
              <div className="topbar-stat">
                <span className="topbar-stat__label">ROI</span>
                <span className={`topbar-stat__value ${pnl.roi >= 0 ? 'positive' : 'negative'}`}>
                  {(pnl.roi * 100).toFixed(2)}%
                </span>
              </div>
              <div className="topbar-divider" />
              <div className="topbar-stat">
                <span className="topbar-stat__label">Settled</span>
                <span className="topbar-stat__value">{pnl.bets_settled}/{pnl.bets_total}</span>
              </div>
              <div className="topbar-divider" />
              <div className="topbar-stat">
                <span className="topbar-stat__label">Exposure</span>
                <span className="topbar-stat__value">€{pnl.open_exposure.toFixed(2)}</span>
              </div>
            </div>
          )}
        </div>

        {/* Page Content */}
        <div className="content-area">
          {page === 'feed' && <LiveFeed />}
          {page === 'accounts' && <Accounts />}
          {page === 'reports' && <Reports />}
          {page === 'settings' && <Settings />}
        </div>
      </div>
    </div>
  );
}

export default App;
