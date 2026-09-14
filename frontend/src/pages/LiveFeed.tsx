import React, { useState } from 'react';
import { useFeed, type SignalData } from '../hooks/useFeed';
import { EditBetModal } from '../components/EditBetModal';

const API = '/api';

const SPORTS = [
  'soccer', 'tennis', 'basketball', 'american_football', 'baseball',
  'ice_hockey', 'cricket', 'rugby_league', 'rugby_union', 'golf',
  'mma', 'boxing', 'volleyball', 'handball', 'darts', 'esports', 'table_tennis',
];

function formatEdge(edge: number) {
  const pct = (edge * 100).toFixed(2);
  return edge >= 0 ? `+${pct}%` : `${pct}%`;
}

function formatProb(p: number) {
  return (p * 100).toFixed(1) + '%';
}

function formatDelta(fair: number, confirm: number | null) {
  if (confirm == null) return null;
  const diff = (confirm - fair) * 100;
  return diff >= 0 ? `+${diff.toFixed(1)}%` : `${diff.toFixed(1)}%`;
}

function formatTime(iso: string | null) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
}

function formatDateTime(iso: string | null) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch {
    return iso;
  }
}

function useToast() {
  const [toasts, setToasts] = useState<{ id: number; msg: string; type: string }[]>([]);
  const add = (msg: string, type = 'info') => {
    const id = Date.now();
    setToasts((t) => [...t, { id, msg, type }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  };
  return { toasts, toast: add };
}

export const LiveFeed: React.FC = () => {
  const { signals, connected, error } = useFeed();
  const [editSignal, setEditSignal] = useState<SignalData | null>(null);
  const [localSignals, setLocalSignals] = useState<Map<number, Partial<SignalData>>>(new Map());
  const [sportFilter, setSportFilter] = useState('');
  const [marketFilter, setMarketFilter] = useState('');
  const { toasts, toast } = useToast();

  const mergedSignals = signals.map((s) => ({
    ...s,
    ...(localSignals.get(s.id) ?? {}),
  }));

  const filtered = mergedSignals.filter((s) => {
    if (sportFilter && s.sport !== sportFilter) return false;
    if (marketFilter === 'live' && !s.is_live) return false;
    if (marketFilter === 'prematch' && s.is_live) return false;
    return true;
  });

  const doAction = async (id: number, action: string) => {
    try {
      const options: RequestInit = { method: 'POST' };
      if (action === 'place') {
        options.headers = { 'Content-Type': 'application/json' };
        options.body = JSON.stringify({ slippage: 0.02 });
      }
      const res = await fetch(`${API}/signals/${id}/${action}`, options);
      if (res.ok) {
        const data = await res.json().catch(() => null);
        if (action === 'place' && data && data.message) {
          toast(`Signal ${id}: ${data.message}`, data.success ? 'success' : 'info');
        } else {
          toast(`Signal ${id} ${action}d`, 'success');
        }
      } else {
        const err = await res.json().catch(() => ({ detail: 'Unknown error' }));
        const detailMsg = typeof err.detail === 'string'
          ? err.detail
          : Array.isArray(err.detail)
            ? err.detail.map((d: any) => d.msg || JSON.stringify(d)).join(', ')
            : JSON.stringify(err.detail ?? err);
        toast(`${action} failed: ${detailMsg}`, 'error');
      }
    } catch {
      toast('Network error', 'error');
    }
  };

  const doCancel = async (id: number) => {
    if (!confirm('Cancel this bet? This will void any associated bet.')) return;
    try {
      const res = await fetch(`${API}/signals/${id}/cancel`, { method: 'POST' });
      if (res.ok) toast('Bet cancelled', 'success');
      else toast('Cancel failed', 'error');
    } catch {
      toast('Network error', 'error');
    }
  };

  const onEditSave = (id: number, updates: Partial<SignalData>) => {
    setLocalSignals((prev) => new Map(prev).set(id, { ...(prev.get(id) ?? {}), ...updates }));
    toast('Bet updated', 'success');
  };

  return (
    <div className="feed-page">
      {/* Toast notifications */}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.type}`}>{t.msg}</div>
        ))}
      </div>

      {/* Page Header */}
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Live Betting Feed</h1>
          <p className="page-subtitle">
            Real-time value signals — new bets appear automatically
          </p>
        </div>
        <div className="feed-status">
          <span className={`badge-dot ${connected ? 'positive' : 'negative'}`}>
            {connected ? 'Stream Active' : error ?? 'Reconnecting…'}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
            {filtered.length} signal{filtered.length !== 1 ? 's' : ''}
          </span>
        </div>
      </div>

      {/* Filters */}
      <div className="feed-toolbar glass-panel">
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', flexWrap: 'wrap' }}>
          <select
            value={marketFilter}
            onChange={(e) => setMarketFilter(e.target.value)}
            style={{ minWidth: '150px' }}
          >
            <option value="">All Markets</option>
            <option value="live">🔴 Live Only</option>
            <option value="prematch">🔵 Pre-Match Only</option>
          </select>

          <select
            value={sportFilter}
            onChange={(e) => setSportFilter(e.target.value)}
            style={{ minWidth: '150px' }}
          >
            <option value="">All Sports</option>
            {SPORTS.map((s) => (
              <option key={s} value={s}>{s.replace('_', ' ')}</option>
            ))}
          </select>
        </div>

          <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="pill pill-live" style={{ fontSize: '0.7rem' }}>LIVE</span>
            <span>= In-play bet</span>
            <span style={{ marginLeft: '12px' }}>
              <span className="pill pill-prematch" style={{ fontSize: '0.7rem' }}>PRE-MATCH</span>
            </span>
            <span>= Pre-market bet</span>
          </div>
      </div>

      {/* Signal Cards */}
      {filtered.length === 0 ? (
        <div className="glass-panel empty-state">
          {connected
            ? '⚡ Watching for value signals… New bets will appear here automatically.'
            : '📡 Connecting to live feed…'}
        </div>
      ) : (
        <div className="bet-cards">
          {filtered.map((s) => {
            const edgeClass = s.edge > 0.08 ? 'edge-high' : s.edge > 0.03 ? 'edge-medium' : 'edge-low';
            const delta = formatDelta(s.fair_prob, s.confirm_prob);

            return (
              <div key={s.id} className={`bet-card glass-panel ${s.is_live ? 'bet-card--live' : 'bet-card--prematch'}`}>
                {/* Card Top Row */}
                <div className="bet-card__header">
                  <div className="bet-card__badges">
                    {s.is_live
                      ? <span className="pill pill-live">LIVE</span>
                      : <span className="pill pill-prematch">PRE-MATCH</span>
                    }
                    <span className={`pill pill-status-${s.status}`}>{s.status}</span>
                    {!s.variables_complete && (
                      <span className="pill" style={{ background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)' }}>
                        ⚠️ Missing Data
                      </span>
                    )}
                    {s.variables_complete && (
                      <span className="pill" style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '1px solid rgba(16,185,129,0.2)', fontSize: '0.7rem' }}>
                        ✅ All Variables
                      </span>
                    )}
                  </div>

                  <div className="bet-card__actions">
                    <button
                      className="btn btn-ghost btn-sm btn-icon"
                      title="Edit bet variables"
                      onClick={() => setEditSignal(s)}
                    >✏️</button>
                    {(s.status === 'detected' || s.status === 'approved') && (
                      <button
                        className="btn btn-danger btn-sm"
                        title="Cancel this bet"
                        onClick={() => doCancel(s.id)}
                      >✖ Cancel</button>
                    )}
                  </div>
                </div>

                {/* Selection */}
                <div className="bet-card__selection">
                  {s.selection}
                </div>
                <div className="bet-card__meta">
                  <span className="bet-card__sport">{s.sport.replace('_', ' ')}</span>
                  <span className="bet-card__market">{s.market_type}</span>
                </div>

                {/* Key Stats Grid */}
                <div className="bet-card__stats">
                  <div className="stat-item">
                    <span className="stat-label">Edge</span>
                    <span className={`stat-value ${edgeClass}`} style={{ fontSize: '1.4rem', fontWeight: 700 }}>
                      {formatEdge(s.edge)}
                    </span>
                  </div>

                  <div className="stat-item">
                    <span className="stat-label">Target Odds</span>
                    <span className="stat-value">{s.target_odds.toFixed(2)}</span>
                  </div>

                  <div className="stat-item">
                    <span className="stat-label">Fair Prob</span>
                    <span className="stat-value">{formatProb(s.fair_prob)}</span>
                    {delta && (
                      <span style={{ fontSize: '0.75rem', color: parseFloat(delta) >= 0 ? 'var(--status-success)' : 'var(--status-danger)' }}>
                        Conf Δ {delta}
                      </span>
                    )}
                  </div>

                  <div className="stat-item">
                    <span className="stat-label">Recommended Stake</span>
                    <span className="stat-value" style={{ color: 'var(--brand-accent)' }}>
                      €{s.recommended_stake.toFixed(2)}
                    </span>
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                      Stake {s.recommended_stake.toFixed(2)} on this selection
                    </span>
                  </div>

                  <div className="stat-item">
                    <span className="stat-label">Max Bet</span>
                    <span className="stat-value" style={{ color: s.max_bet ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                      {s.max_bet ? `€${s.max_bet.toFixed(2)}` : '—'}
                    </span>
                  </div>
                </div>

                {/* Timestamps */}
                <div className="bet-card__timestamps">
                  <span title="When the bet opportunity was identified">
                    🔍 Identified: {formatDateTime(s.detected_at)}
                  </span>
                  {s.event_start_time && (
                    <span title="Event start time">
                      ⏱ Event: {formatTime(s.event_start_time)}
                    </span>
                  )}
                </div>

                {/* Action Buttons */}
                {(s.status === 'detected' || s.status === 'approved') && (
                  <div className="bet-card__footer">
                    {s.status === 'detected' && (
                      <>
                        <button className="btn btn-success btn-sm" onClick={() => doAction(s.id, 'approve')}>
                          ✓ Approve
                        </button>
                        <button className="btn btn-danger btn-sm" onClick={() => doAction(s.id, 'reject')}>
                          ✗ Reject
                        </button>
                      </>
                    )}
                    {s.status === 'approved' && (
                      <button className="btn btn-primary btn-sm" onClick={() => doAction(s.id, 'place')}>
                        💰 Place Bet
                      </button>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {editSignal && (
        <EditBetModal
          signal={editSignal}
          onClose={() => setEditSignal(null)}
          onSave={(updates) => { onEditSave(editSignal.id, updates); }}
        />
      )}
    </div>
  );
};
