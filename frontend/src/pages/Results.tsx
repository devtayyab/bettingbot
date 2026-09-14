import React, { useState, useEffect } from 'react';

const API = '/api';

interface ResultItem {
  bet_id: number;
  signal_id: number;
  selection: string;
  sport: string;
  market_type: string;
  is_live: boolean;
  placed_odds: number;
  stake: number;
  outcome: 'won' | 'lost' | 'void';
  profit: number;
  profit_pct: number;
  edge: number;
  fair_prob: number;
  accuracy: number;
  signal_generated_at: string | null;
  bet_placed_at: string | null;
  end_time: string | null;
  bookmaker: string;
  dry_run: boolean;
}

const SPORTS = [
  'soccer', 'tennis', 'basketball', 'american_football', 'baseball',
  'ice_hockey', 'cricket', 'rugby_league', 'rugby_union', 'golf',
  'mma', 'boxing', 'volleyball', 'handball', 'darts', 'esports', 'table_tennis',
];

function formatDateTime(iso: string | null) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

export const Results: React.FC = () => {
  const [results, setResults] = useState<ResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [outcomeFilter, setOutcomeFilter] = useState<'all' | 'won' | 'lost'>('all');
  const [sportFilter, setSportFilter] = useState<string>('all');

  const fetchResults = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (outcomeFilter !== 'all') params.set('outcome', outcomeFilter);
      if (sportFilter !== 'all') params.set('sport', sportFilter);

      const res = await fetch(`${API}/results?${params.toString()}`);
      if (res.ok) {
        setResults(await res.json());
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchResults();
  }, [outcomeFilter, sportFilter]);

  // Overall KPIs
  const totalSettled = results.length;
  const wonCount = results.filter((r) => r.outcome === 'won').length;
  const lostCount = results.filter((r) => r.outcome === 'lost').length;
  const totalProfit = results.reduce((acc, r) => acc + (r.profit || 0), 0);
  const totalStaked = results.reduce((acc, r) => acc + (r.stake || 0), 0);
  const winRate = (wonCount + lostCount) > 0 ? ((wonCount / (wonCount + lostCount)) * 100).toFixed(1) : '0.0';
  const roi = totalStaked > 0 ? ((totalProfit / totalStaked) * 100).toFixed(1) : '0.0';

  return (
    <div className="results-page" style={{ maxWidth: '1400px', margin: '0 auto' }}>
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">🏆 Bet Results & History</h1>
          <p className="page-subtitle">
            Verified Won and Lost bets — track detection time, final match end time, and realized profit
          </p>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={fetchResults} disabled={loading}>
          {loading ? 'Refreshing…' : '🔄 Refresh Results'}
        </button>
      </div>

      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 'var(--space-4)', marginBottom: 'var(--space-5)' }}>
        <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>
            Settled Bets
          </div>
          <div style={{ fontSize: '1.8rem', fontWeight: 700, color: 'var(--brand-accent)' }}>
            {totalSettled}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
            Finished matches
          </div>
        </div>

        <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>
            Won / Lost Ratio
          </div>
          <div style={{ fontSize: '1.8rem', fontWeight: 700 }}>
            <span style={{ color: '#10b981' }}>{wonCount} Won</span>
            {' / '}
            <span style={{ color: '#ef4444' }}>{lostCount} Lost</span>
          </div>
          <div style={{ fontSize: '0.75rem', color: '#10b981', marginTop: '4px' }}>
            Win Rate: {winRate}%
          </div>
        </div>

        <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>
            Net Realized Profit
          </div>
          <div style={{ fontSize: '1.8rem', fontWeight: 700, color: totalProfit >= 0 ? '#10b981' : '#ef4444' }}>
            {totalProfit >= 0 ? `+€${totalProfit.toFixed(2)}` : `-€${Math.abs(totalProfit).toFixed(2)}`}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
            Total Staked: €{totalStaked.toFixed(2)}
          </div>
        </div>

        <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
          <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: '4px' }}>
            Return on Investment (ROI)
          </div>
          <div style={{ fontSize: '1.8rem', fontWeight: 700, color: Number(roi) >= 0 ? '#10b981' : '#ef4444' }}>
            {Number(roi) >= 0 ? `+${roi}%` : `${roi}%`}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>
            Cumulative performance
          </div>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="glass-panel" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-5)', display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Filter Outcome:</span>
          <button
            className={`btn btn-sm ${outcomeFilter === 'all' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setOutcomeFilter('all')}
          >
            All Results ({totalSettled})
          </button>
          <button
            className={`btn btn-sm ${outcomeFilter === 'won' ? 'btn-success' : 'btn-ghost'}`}
            style={{ color: outcomeFilter === 'won' ? '#fff' : '#10b981' }}
            onClick={() => setOutcomeFilter('won')}
          >
            🏆 Only Won ({wonCount})
          </button>
          <button
            className={`btn btn-sm ${outcomeFilter === 'lost' ? 'btn-danger' : 'btn-ghost'}`}
            style={{ color: outcomeFilter === 'lost' ? '#fff' : '#ef4444' }}
            onClick={() => setOutcomeFilter('lost')}
          >
            ❌ Only Lost ({lostCount})
          </button>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Sport:</span>
          <select
            value={sportFilter}
            onChange={(e) => setSportFilter(e.target.value)}
            style={{ padding: '6px 12px', borderRadius: 'var(--radius-md)', background: 'var(--bg-elevated)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff' }}
          >
            <option value="all">All Sports</option>
            {SPORTS.map((s) => (
              <option key={s} value={s}>{s.replace('_', ' ')}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Results Table */}
      {results.length === 0 && !loading ? (
        <div className="glass-panel empty-state">
          ⚡ No completed bets found yet. When matches finish and bets settle, they will show here.
        </div>
      ) : (
        <div className="glass-panel" style={{ padding: 'var(--space-5)' }}>
          <div className="section-title">Settled Matches Log ({results.length})</div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th>Event / Selection</th>
                  <th>Sport</th>
                  <th>Win Accuracy</th>
                  <th>Odds</th>
                  <th>Stake</th>
                  <th>Net P&L</th>
                  <th>⚡ Signal Generated Time</th>
                  <th>🏁 End / Settled Time</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => {
                  const isWon = r.outcome === 'won';
                  return (
                    <tr key={r.bet_id}>
                      <td>
                        <span
                          className="pill"
                          style={{
                            background: isWon ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                            color: isWon ? '#34d399' : '#f87171',
                            fontWeight: 700,
                            padding: '4px 10px',
                            borderRadius: '6px',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                        >
                          {isWon ? '✓ WON' : '✗ LOST'}
                        </span>
                      </td>
                      <td>
                        <div style={{ fontWeight: 600 }}>{r.selection}</div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {r.market_type} {r.dry_run ? '• Paper Bet' : ''}
                        </div>
                      </td>
                      <td>
                        <span className="bet-card__sport">{r.sport.replace('_', ' ')}</span>
                      </td>
                      <td>
                        <span style={{ color: '#60a5fa', fontWeight: 600 }}>
                          {r.accuracy}%
                        </span>
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                          Edge: +{(r.edge * 100).toFixed(2)}%
                        </div>
                      </td>
                      <td style={{ fontWeight: 600 }}>{r.placed_odds.toFixed(2)}</td>
                      <td>€{r.stake.toFixed(2)}</td>
                      <td>
                        <div style={{ fontWeight: 700, color: isWon ? '#34d399' : '#f87171' }}>
                          {isWon ? `+€${r.profit.toFixed(2)}` : `-€${Math.abs(r.profit).toFixed(2)}`}
                        </div>
                        <div style={{ fontSize: '0.72rem', color: isWon ? '#10b981' : '#ef4444' }}>
                          {isWon ? `+${r.profit_pct}%` : `${r.profit_pct}%`}
                        </div>
                      </td>
                      <td>
                        <div style={{ fontSize: '0.8125rem', color: 'var(--text-primary)', fontWeight: 500 }}>
                          {formatDateTime(r.signal_generated_at)}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          Opportunity detected
                        </div>
                      </td>
                      <td>
                        <div style={{ fontSize: '0.8125rem', color: 'var(--text-primary)', fontWeight: 500 }}>
                          {formatDateTime(r.end_time)}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          Match finished / settled
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
