import React, { useState, useEffect } from 'react';

const API = '/api';

interface ResultItem {
  bet_id: number;
  signal_id: number;
  event_name?: string | null;
  selection: string;
  sport: string;
  market_type: string;
  bet_type?: string;
  is_live: boolean;
  placed_odds: number;
  betfair_odds?: number | null;
  pinnacle_odds?: number | null;
  stake: number;
  outcome: 'won' | 'lost' | 'void' | 'pending';
  final_score?: string | null;
  profit: number;
  profit_pct: number;
  edge: number;
  fair_prob: number;
  accuracy: number;
  event_start_time: string | null;
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
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

export const Results: React.FC = () => {
  const [results, setResults] = useState<ResultItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [outcomeFilter, setOutcomeFilter] = useState<'settled' | 'won' | 'lost' | 'pending'>('settled');
  const [sportFilter, setSportFilter] = useState<string>('all');

  const fetchResults = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (outcomeFilter === 'pending') {
        params.set('outcome', 'pending');
        params.set('include_pending', 'true');
      } else if (outcomeFilter === 'won' || outcomeFilter === 'lost') {
        params.set('outcome', outcomeFilter);
      }
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

  const handleAutoSync = async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const res = await fetch(`${API}/bets/settle-all`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setSyncMsg(data.message || `Synced official match scores (${data.settled ?? 0} settled).`);
        await fetchResults();
      } else {
        setSyncMsg('Failed to sync official results.');
      }
    } catch {
      setSyncMsg('Network error while syncing official scores.');
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => {
    fetchResults();
  }, [outcomeFilter, sportFilter]);

  const settledRows = results.filter((r) => r.outcome === 'won' || r.outcome === 'lost' || r.outcome === 'void');
  const totalSettled = settledRows.length;
  const wonCount = settledRows.filter((r) => r.outcome === 'won').length;
  const lostCount = settledRows.filter((r) => r.outcome === 'lost').length;
  const totalProfit = settledRows.reduce((acc, r) => acc + (r.profit || 0), 0);
  const totalStaked = settledRows.reduce((acc, r) => acc + (r.stake || 0), 0);
  const winRate = (wonCount + lostCount) > 0 ? ((wonCount / (wonCount + lostCount)) * 100).toFixed(1) : '0.0';
  const roi = totalStaked > 0 ? ((totalProfit / totalStaked) * 100).toFixed(1) : '0.0';

  return (
    <div className="results-page" style={{ maxWidth: '1450px', margin: '0 auto' }}>
      {/* Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">🏆 Bet Results &amp; Official Scores</h1>
          <p className="page-subtitle">
            Won &amp; Lost outcomes update automatically from official match scores every 15 minutes — view actual bet, match kickoff time, and final score
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn btn-primary btn-sm" onClick={handleAutoSync} disabled={syncing}>
            {syncing ? '⏳ Checking Official Scores…' : '⚡ Auto-Sync Match Scores Now'}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={fetchResults} disabled={loading}>
            {loading ? 'Refreshing…' : '🔄 Refresh'}
          </button>
        </div>
      </div>

      {syncMsg && (
        <div className="glass-panel" style={{
          padding: '10px 14px',
          marginBottom: 'var(--space-4)',
          background: 'rgba(56, 189, 248, 0.12)',
          border: '1px solid rgba(56, 189, 248, 0.3)',
          color: '#7dd3fc',
          fontSize: '0.85rem',
        }}>
          ℹ️ {syncMsg}
        </div>
      )}

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
            Auto-verified finished matches
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
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>View:</span>
          <button
            className={`btn btn-sm ${outcomeFilter === 'settled' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setOutcomeFilter('settled')}
          >
            All Settled
          </button>
          <button
            className={`btn btn-sm ${outcomeFilter === 'won' ? 'btn-success' : 'btn-ghost'}`}
            style={{ color: outcomeFilter === 'won' ? '#fff' : '#10b981' }}
            onClick={() => setOutcomeFilter('won')}
          >
            🏆 Won
          </button>
          <button
            className={`btn btn-sm ${outcomeFilter === 'lost' ? 'btn-danger' : 'btn-ghost'}`}
            style={{ color: outcomeFilter === 'lost' ? '#fff' : '#ef4444' }}
            onClick={() => setOutcomeFilter('lost')}
          >
            ❌ Lost
          </button>
          <button
            className={`btn btn-sm ${outcomeFilter === 'pending' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setOutcomeFilter('pending')}
          >
            ⏳ Pending (Awaiting Final Score)
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
          ⚡ No bets match this filter yet. Once matches finish, official scores &amp; Won/Lost results populate here automatically.
        </div>
      ) : (
        <div className="glass-panel" style={{ padding: 'var(--space-5)' }}>
          <div className="section-title">Matches &amp; Bets Log ({results.length})</div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th>Match &amp; Actual Bet</th>
                  <th>Final Score</th>
                  <th>Sport</th>
                  <th>Win Accuracy</th>
                  <th>Odds</th>
                  <th>Stake</th>
                  <th>Net P&amp;L</th>
                  <th>⚽ Match Played At</th>
                  <th>🎯 Placed / Settled</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => {
                  const isWon = r.outcome === 'won';
                  const isLost = r.outcome === 'lost';
                  const isPending = r.outcome === 'pending';
                  return (
                    <tr key={r.bet_id}>
                      <td>
                        <span
                          className="pill"
                          style={{
                            background: isWon
                              ? 'rgba(16, 185, 129, 0.2)'
                              : isLost
                                ? 'rgba(239, 68, 68, 0.2)'
                                : 'rgba(245, 158, 11, 0.16)',
                            color: isWon ? '#34d399' : isLost ? '#f87171' : '#fbbf24',
                            fontWeight: 700,
                            padding: '4px 10px',
                            borderRadius: '6px',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                          }}
                        >
                          {isWon ? '✓ WON' : isLost ? '✗ LOST' : isPending ? '⏳ PENDING' : 'VOID'}
                        </span>
                      </td>
                      <td>
                        {r.event_name && (
                          <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: '2px' }}>
                            🏟 {r.event_name}
                          </div>
                        )}
                        <div style={{ fontWeight: 600, color: '#38bdf8' }}>
                          Bet: {r.selection}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', gap: '6px', alignItems: 'center', marginTop: '2px', flexWrap: 'wrap' }}>
                          {r.bet_type && (
                            <span className="pill" style={{ background: 'rgba(99, 102, 241, 0.12)', color: '#a5b4fc', fontSize: '0.65rem' }}>
                              {r.bet_type}
                            </span>
                          )}
                          <span>• {r.bookmaker ? r.bookmaker.toUpperCase() : 'STOIXIMAN'}</span>
                          {r.dry_run ? '• Paper Bet' : ''}
                        </div>
                      </td>
                      <td>
                        {r.final_score ? (
                          <span style={{
                            fontFamily: 'monospace',
                            fontWeight: 700,
                            fontSize: '0.95rem',
                            padding: '3px 8px',
                            borderRadius: '6px',
                            background: 'rgba(255,255,255,0.07)',
                            border: '1px solid rgba(255,255,255,0.14)',
                            color: '#fff',
                          }}>
                            {r.final_score}
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>—</span>
                        )}
                      </td>
                      <td>
                        <span className="bet-card__sport">{r.sport.replace('_', ' ')}</span>
                      </td>
                      <td>
                        <span style={{ color: '#60a5fa', fontWeight: 600 }}>
                          {r.accuracy.toFixed(1).replace('.', ',')}%
                        </span>
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                          Edge: +{(r.edge * 100).toFixed(2).replace('.', ',')}%
                        </div>
                      </td>
                      <td style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                        {r.placed_odds.toFixed(2).replace('.', ',')}
                      </td>
                      <td>€{r.stake.toFixed(2).replace('.', ',')}</td>
                      <td>
                        {isPending ? (
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>Awaiting score</span>
                        ) : (
                          <>
                            <div style={{ fontWeight: 700, color: isWon ? '#34d399' : isLost ? '#f87171' : 'var(--text-muted)' }}>
                              {r.profit >= 0 ? `+€${r.profit.toFixed(2).replace('.', ',')}` : `-€${Math.abs(r.profit).toFixed(2).replace('.', ',')}`}
                            </div>
                            <div style={{ fontSize: '0.72rem', color: isWon ? '#10b981' : isLost ? '#ef4444' : 'var(--text-muted)' }}>
                              {r.profit_pct >= 0 ? `+${r.profit_pct.toFixed(0)}%` : `${r.profit_pct.toFixed(0)}%`}
                            </div>
                          </>
                        )}
                      </td>
                      <td>
                        <div style={{ fontSize: '0.8125rem', color: 'var(--text-primary)', fontWeight: 600 }}>
                          {formatDateTime(r.event_start_time)}
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          {r.is_live ? 'In-Play match' : 'Scheduled kickoff'}
                        </div>
                      </td>
                      <td>
                        <div style={{ fontSize: '0.78rem', color: 'var(--text-primary)' }}>
                          Placed: {formatDateTime(r.bet_placed_at)}
                        </div>
                        {r.end_time && (
                          <div style={{ fontSize: '0.74rem', color: '#34d399', marginTop: '2px' }}>
                            Settled: {formatDateTime(r.end_time)}
                          </div>
                        )}
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
