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

interface TickerSignal {
  id: number;
  event_name: string | null;
  selection: string;
  edge: number;
  target_odds: number;
  status: string;
  sport: string;
}

function App() {
  const [page, setPage] = useState('feed');
  const [pnl, setPnl] = useState<PnlSummary | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [tickerSignals, setTickerSignals] = useState<TickerSignal[]>([]);

  useEffect(() => {
    const fetchMeta = async () => {
      const [pnlRes, healthRes, sigRes] = await Promise.all([
        fetch(`${API}/pnl`),
        fetch(`${API}/health`),
        fetch(`${API}/signals?limit=25`),
      ]);
      if (pnlRes.ok) setPnl(await pnlRes.json());
      if (healthRes.ok) setHealth(await healthRes.json());
      if (sigRes.ok) {
        const list: TickerSignal[] = await sigRes.json().catch(() => []);
        if (Array.isArray(list)) {
          const active = list.filter((s) => s.status === 'detected' || s.status === 'approved');
          const pool = active.length > 0 ? active : list;
          setTickerSignals([...pool].sort((a, b) => b.edge - a.edge).slice(0, 3));
        }
      }
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
    <div className="shell shell--topnav">
      {/* GoldEdge Horizontal Top Navbar */}
      <header className="ge-navbar">
        <div className="ge-navbar-inner">
          <div className="ge-navbar-left">
            <button
              type="button"
              className="ge-brand-btn"
              onClick={() => setPage('feed')}
              title="ValueBet GoldEdge Terminal"
            >
              <span className="sidebar-logo" aria-hidden="true">VB</span>
              <span className="ge-brand-wordmark">
                Value<span className="ge-brand-gold">Bet</span>
              </span>
            </button>

            {health && (
              <div className="ge-status-pills">
                <span className="ge-bookies-pill" title={health.dry_run ? 'Paper mode' : 'Real betting active'}>
                  <span className={`ge-dot ${health.dry_run ? 'ge-dot--gold' : 'ge-dot--green'}`} />
                  <span>{health.dry_run ? 'Paper' : 'Live'}</span>
                </span>
                <span
                  className="ge-bookies-pill"
                  title={health.has_session_cookies ? 'Stoiximan session active' : 'No Stoiximan cookies'}
                >
                  <span className={`ge-dot ${health.has_session_cookies ? 'ge-dot--green' : 'ge-dot--red'}`} />
                  <span>{health.has_session_cookies ? 'Session OK' : 'No Session'}</span>
                </span>
                {(health.unconfirmed_bets ?? 0) > 0 && (
                  <span className="ge-bookies-pill" style={{ color: '#f59e0b' }}>
                    <span>{health.unconfirmed_bets} unconfirmed</span>
                  </span>
                )}
              </div>
            )}
          </div>

          <Nav active={page} onChange={setPage} />

          {view && (
            <div className="topbar-pnl ge-navbar-pnl">
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
      </header>

      {/* GoldEdge Warm Notice Ribbon */}
      <div className="ge-notice-ribbon">
        <span>⚡ Sharp no-vig EV detection active — PropLine / Odds API live scanning &amp; automatic match settlement enabled</span>
      </div>

      {/* GoldEdge Full-Width Multi-Edge Ticker Strip */}
      <div className="ge-global-ticker" onClick={() => setPage('feed')} role="button" tabIndex={0}>
        <div className="ge-global-ticker-inner">
          <span className="ge-ticker-tag">
            <span className="ge-ticker-dot" />
            EDGE
          </span>
          {tickerSignals.length > 0 ? (
            <div className="ge-ticker-stream">
              {tickerSignals.map((ts, idx) => (
                <div key={ts.id} className="ge-ticker-item">
                  {idx > 0 && <span className="ge-ticker-sep">|</span>}
                  <span className="ge-ticker-ev">
                    +{(ts.edge * 100).toFixed(2).replace('.', ',')}%
                  </span>
                  <span className="ge-ticker-book">@Stoiximan</span>
                  <span className="ge-ticker-match">{ts.event_name || `Match #${ts.id}`}</span>
                  <span className="ge-ticker-sel">{ts.selection}</span>
                  <span className="ge-ticker-odds">{ts.target_odds.toFixed(2).replace('.', ',')}</span>
                </div>
              ))}
            </div>
          ) : (
            <span className="ge-ticker-empty">Scanning for value across sharp markets…</span>
          )}
        </div>
      </div>

      {/* Main Content Container */}
      <main className="main-area ge-main-wrap">
        <div className="content-area ge-content-container">
          {page === 'feed' && <LiveFeed />}
          {page === 'results' && <Results />}
          {page === 'accounts' && <Accounts />}
          {page === 'reports' && <Reports />}
          {page === 'settings' && <Settings />}
        </div>
      </main>
    </div>
  );
}

export default App;
