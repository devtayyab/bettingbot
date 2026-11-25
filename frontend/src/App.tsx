import { useState, useEffect } from 'react';
import { Nav } from './components/Nav';
import { LiveFeed } from './pages/LiveFeed';
import { Results } from './pages/Results';
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
  paper_bets_settled?: number;
  paper_realised_pnl?: number;
  paper_roi?: number;
  paper_exposure?: number;
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

  // /pnl headline fields are real money only. In paper mode show the paper
  // portfolio instead, labelled as such so it is never read as real profit.
  const paper = !!health?.dry_run;
  const view = pnl && {
    label: paper ? 'Paper ' : '',
    pnl: paper ? pnl.paper_realised_pnl ?? 0 : pnl.realised_pnl,
    roi: paper ? pnl.paper_roi ?? 0 : pnl.roi,
    settled: paper ? pnl.paper_bets_settled ?? 0 : pnl.bets_settled,
    total: paper ? pnl.paper_bets : pnl.bets_total,
    exposure: paper ? pnl.paper_exposure ?? 0 : pnl.open_exposure,
  };

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

          {view && (
            <div className="topbar-pnl">
              <div className="topbar-stat">
                <span className="topbar-stat__label">{view.label}P&amp;L</span>
                <span className={`topbar-stat__value ${view.pnl >= 0 ? 'positive' : 'negative'}`}>
                  {view.pnl >= 0 ? '+' : ''}€{view.pnl.toFixed(2)}
                </span>
              </div>
              <div className="topbar-divider" />
              <div className="topbar-stat">
                <span className="topbar-stat__label">{view.label}ROI</span>
                <span className={`topbar-stat__value ${view.roi >= 0 ? 'positive' : 'negative'}`}>
                  {(view.roi * 100).toFixed(2)}%
                </span>
              </div>
              <div className="topbar-divider" />
              <div className="topbar-stat">
                <span className="topbar-stat__label">Settled</span>
                <span className="topbar-stat__value">{view.settled}/{view.total}</span>
              </div>
              <div className="topbar-divider" />
              <div className="topbar-stat">
                <span className="topbar-stat__label">Exposure</span>
                <span className="topbar-stat__value">€{view.exposure.toFixed(2)}</span>
              </div>
            </div>
          )}
        </div>

        {/* Page Content */}
        <div className="content-area">
          {page === 'feed' && <LiveFeed />}
          {page === 'results' && <Results />}
          {page === 'accounts' && <Accounts />}
          {page === 'reports' && <Reports />}
          {page === 'settings' && <Settings />}
        </div>
      </div>
    </div>
  );
}

export default App;
