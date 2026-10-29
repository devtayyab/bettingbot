import React, { useState, useEffect } from 'react';
import { useFeed, type SignalData } from '../hooks/useFeed';
import { EditBetModal } from '../components/EditBetModal';

const API = '/api';

const SPORTS = [
  'soccer', 'tennis', 'basketball', 'american_football', 'baseball',
  'ice_hockey', 'cricket', 'rugby_league', 'rugby_union', 'golf',
  'mma', 'boxing', 'volleyball', 'handball', 'darts', 'esports', 'table_tennis',
];

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
    return { label: betType || 'Penalties', icon: '🎯', color: '#f43f5e', bg: 'rgba(244,63,94,0.14)', border: 'rgba(244,63,94,0.3)' };
  }
  if (t.includes('card') || t.includes('booking') || t.includes('red') || t.includes('yellow')) {
    return { label: betType || 'Cards / Bookings', icon: '🟨', color: '#f59e0b', bg: 'rgba(245,158,11,0.14)', border: 'rgba(245,158,11,0.3)' };
  }
  if (t.includes('corner')) {
    return { label: betType || 'Corners', icon: '⛳', color: '#06b6d4', bg: 'rgba(6,182,212,0.14)', border: 'rgba(6,182,212,0.3)' };
  }
  if (t.includes('over') || t.includes('under') || t.includes('total')) {
    return { label: betType || 'Totals (Over/Under)', icon: '📈', color: '#a855f7', bg: 'rgba(168,85,247,0.14)', border: 'rgba(168,85,247,0.3)' };
  }
  if (t.includes('handicap') || t.includes('asian') || t.includes('spread')) {
    return { label: betType || 'Handicap', icon: '⚖️', color: '#fb923c', bg: 'rgba(251,146,60,0.14)', border: 'rgba(251,146,60,0.3)' };
  }
  if (t.includes('btts') || t.includes('both')) {
    return { label: betType || 'Both Teams To Score', icon: '🤝', color: '#10b981', bg: 'rgba(16,185,129,0.14)', border: 'rgba(16,185,129,0.3)' };
  }
  return { label: betType || 'Match Winner (1X2)', icon: '⚽', color: '#38bdf8', bg: 'rgba(56,189,248,0.14)', border: 'rgba(56,189,248,0.3)' };
}

interface OddsBoxesProps {
  allMarketOdds: Record<string, number> | null | undefined;
  homeOdds: number | null | undefined;
  drawOdds: number | null | undefined;
  awayOdds: number | null | undefined;
  selectedSelection: string;
  marketType: string;
}

function OddsBoxes({ allMarketOdds, homeOdds, drawOdds, awayOdds, selectedSelection, marketType }: OddsBoxesProps) {
  type OddsEntry = { label: string; odds: number; isSelected: boolean; key: string; boxLabel: string };
  let entries: OddsEntry[] = [];

  const isDraw = (label: string) => {
    const l = label.trim().toLowerCase();
    return l === 'draw' || l === 'tie' || l === 'x' || l === 'empate' || l === 'isopalia';
  };

  const mtLower = (marketType || '').toLowerCase();
  const isTotals = mtLower.startsWith('totals') || mtLower.includes('over_under');
  const isHandicap = mtLower.startsWith('handicap') || mtLower.includes('spread');

  if (allMarketOdds && Object.keys(allMarketOdds).length > 0) {
    const raw = Object.entries(allMarketOdds).map(([sel, odds]) => ({
      key: sel,
      label: sel,
      odds,
      isSelected: sel.toLowerCase() === selectedSelection.toLowerCase(),
    }));

    if (isTotals) {
      entries = raw.map((e) => ({
        ...e,
        boxLabel: e.label.toLowerCase().startsWith('over') ? 'OVER' : e.label.toLowerCase().startsWith('under') ? 'UNDER' : e.label,
      }));
    } else if (isHandicap) {
      entries = raw.map((e, idx) => ({
        ...e,
        boxLabel: idx === 0 ? 'HOME HCAP' : 'AWAY HCAP',
      }));
    } else if (raw.length === 3) {
      const drawIdx = raw.findIndex((e) => isDraw(e.label));
      if (drawIdx !== -1) {
        const drawEntry = { ...raw[drawIdx], boxLabel: 'X' };
        const teamEntries = raw.filter((_, idx) => idx !== drawIdx);
        entries = [
          { ...teamEntries[0], boxLabel: '1' },
          drawEntry,
          { ...teamEntries[1], boxLabel: '2' },
        ];
      } else {
        entries = raw.map((e, idx) => ({ ...e, boxLabel: idx === 0 ? '1' : idx === 1 ? 'X' : '2' }));
      }
    } else if (raw.length === 2) {
      entries = raw.map((e, idx) => ({ ...e, boxLabel: idx === 0 ? '1' : '2' }));
    } else {
      entries = raw.map((e) => ({ ...e, boxLabel: e.label.length > 8 ? e.label.slice(0, 7) + '…' : e.label }));
    }
  } else if (homeOdds != null || drawOdds != null || awayOdds != null) {
    const selNorm = selectedSelection.trim().toLowerCase();
    if (homeOdds != null) entries.push({ key: '1', label: '1', boxLabel: '1', odds: homeOdds, isSelected: selNorm === '1' || selNorm === 'home' });
    if (drawOdds != null) entries.push({ key: 'X', label: 'X', boxLabel: 'X', odds: drawOdds, isSelected: isDraw(selNorm) });
    if (awayOdds != null) entries.push({ key: '2', label: '2', boxLabel: '2', odds: awayOdds, isSelected: selNorm === '2' || selNorm === 'away' });
  }

  if (entries.length === 0) return null;

  return (
    <div style={{ display: 'flex', gap: '6px', margin: '8px 0', flexWrap: 'wrap' }}>
      {entries.map((entry) => {
        const selected = entry.isSelected;
        return (
          <div
            key={entry.key}
            title={entry.label}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              minWidth: '68px',
              flex: '1',
              padding: '5px 8px',
              borderRadius: '6px',
              border: selected ? '1.5px solid #38bdf8' : '1px solid rgba(255,255,255,0.1)',
              background: selected ? 'rgba(56,189,248,0.18)' : 'rgba(255,255,255,0.04)',
              boxShadow: selected ? '0 0 8px rgba(56,189,248,0.35)' : 'none',
              cursor: 'default',
              transition: 'all 0.15s ease',
            }}
          >
            <span style={{
              fontSize: '0.62rem',
              color: selected ? '#38bdf8' : 'var(--text-muted)',
              fontWeight: 600,
              letterSpacing: '0.05em',
              textTransform: 'uppercase',
              marginBottom: '1px',
            }}>
              {entry.boxLabel}
            </span>
            <span style={{
              fontSize: '0.97rem',
              fontWeight: 700,
              color: selected ? '#38bdf8' : 'var(--text-primary)',
              letterSpacing: '0.01em',
            }}>
              {entry.odds.toFixed(2).replace('.', ',')}
            </span>
            <span style={{
              fontSize: '0.58rem',
              color: selected ? 'rgba(56,189,248,0.85)' : 'var(--text-muted)',
              marginTop: '1px',
              maxWidth: '110px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              textAlign: 'center',
            }}>
              {entry.label}
            </span>
          </div>
        );
      })}
    </div>
  );
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
  const [betTypeFilter, setBetTypeFilter] = useState('');
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [scanning, setScanning] = useState(false);
  const [quotaWarning, setQuotaWarning] = useState<string | null>(null);
  const { toasts, toast } = useToast();

  useEffect(() => {
    fetch(`${API}/odds-api/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && (data.quota_exhausted || data.valid === false)) {
          setQuotaWarning(data.message || 'The Odds API key has 0 requests remaining or is invalid.');
        } else {
          setQuotaWarning(null);
        }
      })
      .catch(() => {});
  }, []);

  const handleTriggerScan = async () => {
    if (scanning) return;
    setScanning(true);
    toast('Scanning markets for value signals...', 'info');
    try {
      const sportParam = sportFilter ? `sport=${encodeURIComponent(sportFilter)}` : 'sport=all';
      const liveParam = marketFilter === 'live' ? '&live=true' : '';
      const res = await fetch(`${API}/scan?${sportParam}${liveParam}`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        const count = data.new_signals ?? 0;
        if (count > 0) {
          toast(`Scan complete: ${count} signal${count !== 1 ? 's' : ''} found/updated!`, 'success');
        } else {
          toast('Scan complete: No new value changes (≥1.5%) found right now.', 'info');
        }
      } else {
        const err = await res.json().catch(() => ({}));
        toast(`Scan error: ${err.detail || 'Failed to scan'}`, 'error');
      }
    } catch (e: any) {
      toast(`Scan error: ${e.message}`, 'error');
    } finally {
      setScanning(false);
    }
  };

  const mergedSignals = signals.map((s) => ({
    ...s,
    ...(localSignals.get(s.id) ?? {}),
  }));

  const filtered = mergedSignals
    .filter((s) => {
      if (sportFilter && s.sport !== sportFilter) return false;
      if (marketFilter === 'live' && !s.is_live) return false;
      if (marketFilter === 'prematch' && s.is_live) return false;
      if (betTypeFilter) {
        const mt = (s.market_type || '').toLowerCase();
        if (betTypeFilter === 'h2h' && !mt.includes('match') && !mt.includes('h2h') && !mt.includes('1x2')) return false;
        if (betTypeFilter === 'totals' && !mt.startsWith('totals') && !mt.includes('over')) return false;
        if (betTypeFilter === 'spreads' && !mt.startsWith('handicap') && !mt.includes('spread')) return false;
      }
      return true;
    })
    .sort((a, b) => {
      const timeA = a.detected_at ? new Date(a.detected_at).getTime() : 0;
      const timeB = b.detected_at ? new Date(b.detected_at).getTime() : 0;
      return timeB - timeA;
    });

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const expandAll = () => setExpandedIds(new Set(filtered.map((s) => s.id)));
  const collapseAll = () => setExpandedIds(new Set());

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
            Real-time value signals across Match Winner, Over/Under &amp; Handicap — re-reads bets only on ≥1,5% value change
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

      {/* Quota Warning Banner */}
      {quotaWarning && (
        <div className="glass-panel" style={{
          padding: '12px 16px',
          marginBottom: 'var(--space-4)',
          background: 'rgba(239, 68, 68, 0.14)',
          border: '1px solid rgba(239, 68, 68, 0.4)',
          color: '#fca5a5',
          fontSize: '0.875rem',
        }}>
          <strong style={{ color: '#f87171' }}>⚠️ Odds API Feed Alert:</strong> {quotaWarning} Open <strong>Settings → The Odds API Key</strong> to paste an active key.
        </div>
      )}

      {/* Toolbar / Filters */}
      <div className="feed-toolbar glass-panel">
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', flexWrap: 'wrap' }}>
          <select
            value={marketFilter}
            onChange={(e) => setMarketFilter(e.target.value)}
            style={{ minWidth: '145px' }}
          >
            <option value="">All Timings</option>
            <option value="live">🔴 Live Only</option>
            <option value="prematch">🔵 Pre-Match Only</option>
          </select>

          <select
            value={betTypeFilter}
            onChange={(e) => setBetTypeFilter(e.target.value)}
            style={{ minWidth: '165px' }}
          >
            <option value="">All Bet Markets</option>
            <option value="h2h">⚽ Match Winner (1X2)</option>
            <option value="totals">📈 Over / Under Totals</option>
            <option value="spreads">⚖️ Handicap / Spreads</option>
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
            onClick={handleTriggerScan}
            disabled={scanning}
            title="Trigger an immediate scan of available markets"
            style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}
          >
            {scanning ? '⏳ Scanning...' : '⚡ Scan Now'}
          </button>

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
            💡 Each card shows the full Match Fixture, Actual Bet Selection, Kickoff/Played Time, and Sharp Odds Comparison.
          </span>
        </div>
      </div>

      {/* Signal Cards */}
      {filtered.length === 0 ? (
        <div className="glass-panel empty-state" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-8)' }}>
          <div>
            {connected
              ? '⚡ Watching for real value signals… Qualifying bets appear here automatically.'
              : '📡 Connecting to live feed…'}
          </div>
          <button
            className="btn btn--primary"
            onClick={handleTriggerScan}
            disabled={scanning}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: 'var(--space-2)' }}
          >
            {scanning ? '⏳ Scanning markets...' : '⚡ Scan Markets Now'}
          </button>
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

                {/* Match Fixture Title */}
                {s.event_name && (
                  <div style={{ fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '4px' }}>
                    🏟 {s.event_name}
                  </div>
                )}

                {/* Actual Bet Pick */}
                <div style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '8px',
                  background: 'rgba(56, 189, 248, 0.12)',
                  border: '1px solid rgba(56, 189, 248, 0.3)',
                  borderRadius: '6px',
                  padding: '4px 10px',
                  marginBottom: '6px',
                }}>
                  <span style={{ fontSize: '0.72rem', textTransform: 'uppercase', color: 'var(--text-muted)', fontWeight: 600 }}>
                    Actual Bet:
                  </span>
                  <span style={{ fontSize: '0.98rem', fontWeight: 700, color: '#38bdf8' }}>
                    {s.selection}
                  </span>
                  <span style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
                    @ {formatEuropeanOdds(s.target_odds)}
                  </span>
                </div>

                <div className="bet-card__meta" style={{ marginBottom: '8px' }}>
                  <span className="bet-card__sport">{s.sport.replace('_', ' ')}</span>
                  <span className="bet-card__market">{betTypeInfo.label}</span>
                </div>

                {/* Market Odds Boxes */}
                <OddsBoxes
                  allMarketOdds={s.all_market_odds}
                  homeOdds={s.home_odds}
                  drawOdds={s.draw_odds}
                  awayOdds={s.away_odds}
                  selectedSelection={s.selection}
                  marketType={s.market_type}
                />

                {/* Bookmaker European Odds Comparison Bar */}
                <div className="bookmaker-bar">
                  <div className="bm-badge bm-badge--stoiximan" title="Target Placement Platform">
                    <span>🎯 Stoiximan:</span>
                    <span style={{ fontSize: '0.95rem' }}>{formatEuropeanOdds(s.target_odds)}</span>
                  </div>
                  <div className="bm-badge bm-badge--ref" title="Sharp Fair Reference Odds">
                    <span>Sharp Fair:</span>
                    <strong>{formatEuropeanOdds(bfOddsVal)}</strong>
                  </div>
                  <div className="bm-badge bm-badge--ref" title="Pinnacle Sharp Bookmaker Odds">
                    <span>Pinnacle:</span>
                    <strong>{formatEuropeanOdds(pinOddsVal)}</strong>
                  </div>
                </div>

                {/* Timestamps */}
                <div className="bet-card__timestamps" style={{ margin: '6px 0 0 0', padding: '6px 0 0 0', display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
                  {s.event_start_time && (
                    <span title="Date and time when the match is played">
                      ⚽ Match Time: <strong>{formatDateTime(s.event_start_time)}</strong>
                    </span>
                  )}
                  <span title="Date and time when this value bet was detected">
                    🔍 Found: {formatDateTime(s.detected_at)}
                  </span>
                </div>

                <div className="expand-toggle-indicator">
                  <span>{isExpanded ? '▲ Click to hide details' : '▼ Click to view stake, risk & actions'}</span>
                </div>

                {isExpanded && (
                  <div
                    className="bet-card__details-drawer"
                    onClick={(e) => e.stopPropagation()}
                  >
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
