import React, { useState } from 'react';
import { useFeed, type SignalData } from '../hooks/useFeed';
import { EditBetModal } from '../components/EditBetModal';

const API = '/api';

const SPORTS = [
  'soccer', 'tennis', 'basketball', 'american_football', 'baseball',
  'ice_hockey', 'cricket', 'rugby_league', 'rugby_union', 'golf',
  'mma', 'boxing', 'volleyball', 'handball', 'darts', 'esports', 'table_tennis',
];

// European style decimal formatting (uses comma separator: 2,25)
function formatEuropeanOdds(odds: number | null | undefined): string {
  if (odds == null || isNaN(odds) || odds <= 0) return '—';
  return odds.toFixed(2).replace('.', ',');
}

function formatEuropeanCurrency(val: number | null | undefined): string {
  if (val == null || isNaN(val)) return '—';
  return '€' + val.toFixed(2).replace('.', ',');
}

function formatEdge(edge: number) {
  const pct = (edge * 100).toFixed(2).replace('.', ',');
  return edge >= 0 ? `+${pct}%` : `${pct}%`;
}

function formatDelta(fair: number, confirm: number | null) {
  if (confirm == null) return null;
  const diff = (confirm - fair) * 100;
  const formatted = diff.toFixed(1).replace('.', ',');
  return diff >= 0 ? `+${formatted}%` : `${formatted}%`;
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

function getBetTypeBadge(betType?: string | null, marketType?: string, selection?: string) {
  const t = (betType || marketType || selection || '').toLowerCase();
  if (t.includes('penalty') || t.includes('penalties')) {
    return { label: 'Penalties', icon: '🎯', color: '#f43f5e', bg: 'rgba(244,63,94,0.14)', border: 'rgba(244,63,94,0.3)' };
  }
  if (t.includes('card') || t.includes('booking') || t.includes('red') || t.includes('yellow')) {
    return { label: 'Cards / Bookings', icon: '🟨', color: '#f59e0b', bg: 'rgba(245,158,11,0.14)', border: 'rgba(245,158,11,0.3)' };
  }
  if (t.includes('corner')) {
    return { label: 'Corners', icon: '⛳', color: '#06b6d4', bg: 'rgba(6,182,212,0.14)', border: 'rgba(6,182,212,0.3)' };
  }
  if (t.includes('over') || t.includes('under') || t.includes('total')) {
    return { label: 'Totals (Over/Under)', icon: '📈', color: '#a855f7', bg: 'rgba(168,85,247,0.14)', border: 'rgba(168,85,247,0.3)' };
  }
  if (t.includes('handicap') || t.includes('asian')) {
    return { label: 'Handicap', icon: '⚖️', color: '#fb923c', bg: 'rgba(251,146,60,0.14)', border: 'rgba(251,146,60,0.3)' };
  }
  if (t.includes('btts') || t.includes('both')) {
    return { label: 'Both Teams To Score', icon: '🤝', color: '#10b981', bg: 'rgba(16,185,129,0.14)', border: 'rgba(16,185,129,0.3)' };
  }
  return { label: betType || 'Match Winner (1X2)', icon: '⚽', color: '#38bdf8', bg: 'rgba(56,189,248,0.14)', border: 'rgba(56,189,248,0.3)' };
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
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
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

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const expandAll = () => {
    setExpandedIds(new Set(filtered.map((s) => s.id)));
  };

  const collapseAll = () => {
    setExpandedIds(new Set());
  };

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
            Real-time value signals — European style odds &amp; multi-bookmaker benchmark comparison
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

      {/* Toolbar / Filters */}
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

          <button
            className="btn btn-ghost btn-sm"
            onClick={expandedIds.size === filtered.length && filtered.length > 0 ? collapseAll : expandAll}
            title="Toggle between compact summary and detailed stake/risk view"
            style={{ marginLeft: 'auto' }}
          >
            {expandedIds.size === filtered.length && filtered.length > 0
              ? '▲ Collapse All Details'
              : '▼ Expand All Details'}
          </button>
        </div>

        <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <span className="pill pill-live" style={{ fontSize: '0.7rem' }}>LIVE</span>
          <span>= In-play</span>
          <span style={{ marginLeft: '8px' }}>
            <span className="pill pill-prematch" style={{ fontSize: '0.7rem' }}>PRE-MATCH</span>
          </span>
          <span>= Pre-match</span>
          <span style={{ marginLeft: '12px', color: 'var(--text-secondary)' }}>
            💡 Tip: Click any bet card to reveal recommended stake, profit/loss risk and action controls.
          </span>
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
            const isExpanded = expandedIds.has(s.id);
            const edgeClass = s.edge > 0.08 ? 'edge-high' : s.edge > 0.03 ? 'edge-medium' : 'edge-low';
            const delta = formatDelta(s.fair_prob, s.confirm_prob);
            const winAccuracy = (s.fair_prob * 100).toFixed(1).replace('.', ',');
            const potProfitVal = (s.recommended_stake * (s.target_odds - 1));
            const potProfitPct = ((s.target_odds - 1) * 100).toFixed(0);
            const betTypeInfo = getBetTypeBadge(s.bet_type, s.market_type, s.selection);

            // Compute Betfair and Pinnacle odds
            const bfOddsVal = s.betfair_odds ?? (s.fair_prob > 0 ? 1.0 / s.fair_prob : null);
            const pinOddsVal = s.pinnacle_odds ?? (s.confirm_prob && s.confirm_prob > 0 ? 1.0 / s.confirm_prob : null);

            return (
              <div
                key={s.id}
                className={`bet-card glass-panel bet-card--interactive ${s.is_live ? 'bet-card--live' : 'bet-card--prematch'}`}
                onClick={() => toggleExpand(s.id)}
              >
                {/* Header: Badges & Edge */}
                <div className="bet-card__header">
                  <div className="bet-card__badges">
                    {s.is_live
                      ? <span className="pill pill-live">LIVE</span>
                      : <span className="pill pill-prematch">PRE-MATCH</span>
                    }
                    {/* Bet Type Badge */}
                    <span
                      className="pill"
                      style={{
                        background: betTypeInfo.bg,
                        color: betTypeInfo.color,
                        border: `1px solid ${betTypeInfo.border}`,
                        fontWeight: 600,
                      }}
                      title={`Market Category: ${betTypeInfo.label}`}
                    >
                      {betTypeInfo.icon} {betTypeInfo.label}
                    </span>

                    <span className={`pill pill-status-${s.status}`}>{s.status}</span>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span className={`stat-value ${edgeClass}`} style={{ fontSize: '1.05rem', fontWeight: 700 }}>
                      {formatEdge(s.edge)}
                    </span>
                    <button
                      className="btn btn-ghost btn-sm btn-icon"
                      style={{ padding: '2px 6px', fontSize: '0.75rem', height: '26px' }}
                      title={isExpanded ? 'Collapse details' : 'Expand details'}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleExpand(s.id);
                      }}
                    >
                      {isExpanded ? '▲' : '▼'}
                    </button>
                  </div>
                </div>

                {/* Actual Bet Selection */}
                <div className="bet-card__selection" style={{ fontSize: '1.15rem' }}>
                  {s.selection}
                </div>

                <div className="bet-card__meta" style={{ marginBottom: '8px' }}>
                  <span className="bet-card__sport">{s.sport.replace('_', ' ')}</span>
                  <span className="bet-card__market">{s.market_type}</span>
                </div>

                {/* Bookmaker European Odds Comparison Bar */}
                <div className="bookmaker-bar">
                  <div className="bm-badge bm-badge--stoiximan" title="Target Placement Platform">
                    <span>🎯 Stoiximan:</span>
                    <span style={{ fontSize: '0.95rem' }}>{formatEuropeanOdds(s.target_odds)}</span>
                  </div>
                  <div className="bm-badge bm-badge--ref" title="Betfair Exchange Fair Odds">
                    <span>Betfair:</span>
                    <strong>{formatEuropeanOdds(bfOddsVal)}</strong>
                  </div>
                  <div className="bm-badge bm-badge--ref" title="Pinnacle Sharp Bookmaker Odds">
                    <span>Pinnacle:</span>
                    <strong>{formatEuropeanOdds(pinOddsVal)}</strong>
                  </div>
                </div>

                {/* Timestamps */}
                <div className="bet-card__timestamps" style={{ margin: '6px 0 0 0', padding: '6px 0 0 0' }}>
                  <span title="When the bet opportunity was identified">
                    🔍 Found: {formatDateTime(s.detected_at)}
                  </span>
                  {s.event_start_time && (
                    <span title="Event start time">
                      ⏱ Event: {formatTime(s.event_start_time)}
                    </span>
                  )}
                </div>

                {/* Click to expand/collapse prompt */}
                <div className="expand-toggle-indicator">
                  <span>{isExpanded ? '▲ Click to hide details' : '▼ Click to view stake, risk & actions'}</span>
                </div>

                {/* Expandable Details Drawer */}
                {isExpanded && (
                  <div
                    className="bet-card__details-drawer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {/* Key Stats Grid */}
                    <div className="bet-card__stats">
                      <div className="stat-item">
                        <span className="stat-label">Recommended Stake</span>
                        <span className="stat-value" style={{ color: 'var(--brand-accent)', fontSize: '1.1rem' }}>
                          {formatEuropeanCurrency(s.recommended_stake)}
                        </span>
                        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                          Kelly sizing
                        </span>
                      </div>

                      <div className="stat-item">
                        <span className="stat-label">Win Accuracy</span>
                        <span className="stat-value" style={{ color: '#60a5fa', fontSize: '1.1rem' }}>
                          {winAccuracy}%
                        </span>
                        {delta && (
                          <span style={{ fontSize: '0.75rem', color: parseFloat(delta) >= 0 ? 'var(--status-success)' : 'var(--status-danger)' }}>
                            Conf Δ {delta}
                          </span>
                        )}
                      </div>

                      <div className="stat-item">
                        <span className="stat-label">Max Bet Cap</span>
                        <span className="stat-value" style={{ color: s.max_bet ? 'var(--text-primary)' : 'var(--text-muted)', fontSize: '1.1rem' }}>
                          {s.max_bet ? formatEuropeanCurrency(s.max_bet) : '—'}
                        </span>
                      </div>
                    </div>

                    {/* Risk / Reward Breakdown */}
                    <div style={{
                      background: 'rgba(255, 255, 255, 0.03)',
                      border: '1px solid rgba(255, 255, 255, 0.08)',
                      borderRadius: 'var(--radius-md)',
                      padding: '10px 14px',
                      marginBottom: '14px',
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
                      gap: '12px',
                    }}>
                      <div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '2px' }}>
                          🎯 Win Chance / Model
                        </div>
                        <div style={{ color: '#60a5fa', fontWeight: 700, fontSize: '1.05rem' }}>
                          {winAccuracy}%
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          De-vigged fair probability
                        </div>
                      </div>

                      <div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '2px' }}>
                          🏆 Profit If Won
                        </div>
                        <div style={{ color: '#34d399', fontWeight: 700, fontSize: '1.05rem' }}>
                          +{potProfitPct}% (+{formatEuropeanCurrency(potProfitVal)})
                        </div>
                        <div style={{ fontSize: '0.7rem', color: '#10b981' }}>
                          Net return on stake
                        </div>
                      </div>

                      <div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '2px' }}>
                          ⚠️ Loss If Lost
                        </div>
                        <div style={{ color: '#f87171', fontWeight: 700, fontSize: '1.05rem' }}>
                          -100% (-{formatEuropeanCurrency(s.recommended_stake)})
                        </div>
                        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                          Max risk = stake amount
                        </div>
                      </div>
                    </div>

                    {/* Quality Badges */}
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '12px', flexWrap: 'wrap' }}>
                      {!s.variables_complete && (
                        <span className="pill" style={{ background: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.25)' }}>
                          ⚠️ Missing Data
                        </span>
                      )}
                      {s.variables_complete && (
                        <span className="pill" style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981', border: '1px solid rgba(16,185,129,0.2)', fontSize: '0.7rem' }}>
                          ✅ All Variables Complete
                        </span>
                      )}
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ marginLeft: 'auto' }}
                        title="Edit stake, cap, or variables"
                        onClick={() => setEditSignal(s)}
                      >
                        ✏️ Edit Bet
                      </button>
                    </div>

                    {/* Action Buttons */}
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
                          💰 Place Bet on Stoiximan
                        </button>
                      )}
                      {(s.status === 'detected' || s.status === 'approved') && (
                        <button
                          className="btn btn-danger btn-sm"
                          style={{ marginLeft: 'auto' }}
                          title="Cancel this bet"
                          onClick={() => doCancel(s.id)}
                        >
                          ✖ Cancel
                        </button>
                      )}
                    </div>
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
