import React, { useState, useEffect } from 'react';

const API = '/api';

const SPORTS = [
  'soccer', 'tennis', 'basketball', 'american_football', 'baseball',
  'ice_hockey', 'cricket', 'rugby_league', 'rugby_union', 'golf',
  'mma', 'boxing', 'volleyball', 'handball', 'darts', 'esports', 'table_tennis',
];

const STATUSES = ['detected', 'approved', 'placed', 'rejected', 'paper', 'unconfirmed', 'cancelled'];

const COLUMN_LABELS: Record<string, string> = {
  signal_id: 'Signal ID',
  bet_id: 'Bet ID',
  selection: 'Bet Selection',
  sport: 'Sport',
  bet_type: 'Bet Type',
  market_type: 'Market Type',
  is_live: 'Live / Pre-Match',
  target_bookmaker: 'Platform (Stoiximan)',
  target_odds: 'Target Odds (Stoiximan)',
  betfair_odds: 'Betfair Exchange Odds',
  pinnacle_odds: 'Pinnacle Odds',
  edge: 'Edge %',
  fair_prob: 'Fair Probability',
  confirm_prob: 'Confirmation Probability',
  placed_odds: 'Odds at Placement',
  recommended_stake: 'Recommended Stake',
  requested_stake: 'Stake Requested',
  stake: 'Stake Accepted',
  account_name: 'Client / Player',
  allocated_stake: 'Player Stake Share',
  player_profit: 'Player Net Profit / Loss',
  potential_profit: 'Profit If Won',
  potential_loss: 'Loss If Lost',
  outcome: 'Outcome',
  profit: 'Net Profit / Loss',
  actual_edge: 'Actual Edge %',
  clv: 'Closing Line Value',
  dry_run: 'Paper Bet',
  detected_at: 'Bet Identified At',
  placed_at: 'Bet Placed At',
  status: 'Signal Status',
  max_bet: 'Max Bet Available',
  variables_complete: 'All Variables Available',
};

interface ReportRow {
  [key: string]: string | number | boolean | null | undefined;
}

interface AccountOption {
  id: number;
  name: string;
  bookmaker: string;
  percentage_share: number;
}

function formatEuropeanOdds(odds: unknown): string {
  if (odds == null || odds === '' || isNaN(Number(odds))) return '—';
  return Number(odds).toFixed(2).replace('.', ',');
}

function formatEuropeanCurrency(val: unknown): string {
  if (val == null || val === '' || isNaN(Number(val))) return '—';
  return '€' + Number(val).toFixed(2).replace('.', ',');
}

export const Reports: React.FC = () => {
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [sport, setSport] = useState('');
  const [status, setStatus] = useState('');
  const [bookmaker, setBookmaker] = useState('');
  const [includeDryRun, setIncludeDryRun] = useState(true);
  const [selectedAccountId, setSelectedAccountId] = useState<string>('');
  const [accounts, setAccounts] = useState<AccountOption[]>([]);
  const [reportData, setReportData] = useState<ReportRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [settlingId, setSettlingId] = useState<number | null>(null);

  useEffect(() => {
    fetch(`${API}/accounts`)
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) setAccounts(data);
      })
      .catch(() => {});
  }, []);

  const buildParams = () => ({
    date_from: dateFrom || null,
    date_to: dateTo || null,
    sport: sport || null,
    status: status || null,
    bookmaker: bookmaker || null,
    include_dry_run: includeDryRun,
    account_id: selectedAccountId ? parseInt(selectedAccountId, 10) : null,
  });

  const generateReport = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/reports/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildParams()),
      });
      if (res.ok) {
        setReportData(await res.json());
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    generateReport();
  }, [selectedAccountId]);

  const handleSettle = async (betId: number, outcome: 'won' | 'lost' | 'void') => {
    setSettlingId(betId);
    try {
      const res = await fetch(`${API}/bets/${betId}/settle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ outcome }),
      });
      if (res.ok) {
        const data = await res.json();
        setReportData((prev) =>
          prev.map((row) =>
            row.bet_id === betId
              ? { ...row, outcome, profit: data.profit }
              : row
          )
        );
      }
    } finally {
      setSettlingId(null);
    }
  };

  const exportFile = async (format: 'csv' | 'xlsx') => {
    setExporting(true);
    try {
      const params = new URLSearchParams();
      params.set('format', format);
      if (dateFrom) params.set('date_from', dateFrom);
      if (dateTo) params.set('date_to', dateTo);
      if (sport) params.set('sport', sport);
      if (status) params.set('status', status);
      if (bookmaker) params.set('bookmaker', bookmaker);
      if (selectedAccountId) params.set('account_id', selectedAccountId);
      params.set('include_dry_run', String(includeDryRun));

      const res = await fetch(`${API}/reports/export?${params}`);
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const filename = res.headers.get('content-disposition')?.split('filename=')[1]?.replace(/"/g, '')
          ?? `betting_report.${format}`;
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
      }
    } finally {
      setExporting(false);
    }
  };

  const selectedAccountObj = accounts.find((a) => String(a.id) === selectedAccountId);
  const columns = reportData.length > 0 ? Object.keys(reportData[0]) : [];

  // Summary KPI Metrics
  const bets = reportData.filter((r) => r.bet_id != null);
  const totalBets = bets.length;
  const wonBets = bets.filter((r) => r.outcome === 'won').length;
  const lostBets = bets.filter((r) => r.outcome === 'lost').length;
  const pendingBets = bets.filter((r) => r.outcome === 'pending').length;

  // Staked and Profit: if a player/user is selected, show player-specific numbers
  const totalProfit = bets.reduce((acc, r) => {
    const val = selectedAccountObj && r.player_profit != null
      ? Number(r.player_profit)
      : (typeof r.profit === 'number' ? r.profit : 0);
    return acc + (isNaN(val) ? 0 : val);
  }, 0);

  const totalStaked = bets.reduce((acc, r) => {
    const val = selectedAccountObj && r.allocated_stake != null
      ? Number(r.allocated_stake)
      : (typeof r.stake === 'number' ? r.stake : 0);
    return acc + (isNaN(val) ? 0 : val);
  }, 0);

  const winRate = (wonBets + lostBets) > 0 ? ((wonBets / (wonBets + lostBets)) * 100).toFixed(1).replace('.', ',') : '0,0';
  const roi = totalStaked > 0 ? ((totalProfit / totalStaked) * 100).toFixed(1).replace('.', ',') : '0,0';

  return (
    <div className="reports-page">
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Reports &amp; Export</h1>
          <p className="page-subtitle">
            Client &amp; Player level reporting, European decimal odds, and CSV/Excel exports
          </p>
        </div>
      </div>

      {/* Filter Panel */}
      <div className="glass-panel" style={{ padding: 'var(--space-5)', marginBottom: 'var(--space-5)' }}>
        <div className="section-title">Filter Options</div>

        <div className="report-filters">
          {/* Client / Player Filter */}
          <div className="form-field" style={{ gridColumn: 'span 2' }}>
            <label style={{ color: 'var(--brand-accent)', fontWeight: 600 }}>
              👤 Client / Player (User Account)
            </label>
            <select
              value={selectedAccountId}
              onChange={(e) => setSelectedAccountId(e.target.value)}
              style={{
                borderColor: selectedAccountId ? 'var(--brand-accent)' : undefined,
                background: selectedAccountId ? 'rgba(99, 102, 241, 0.08)' : undefined,
              }}
            >
              <option value="">All Clients (Global Unified View)</option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>
                  👤 {acc.name} — ({acc.bookmaker.toUpperCase()} · {(acc.percentage_share * 100).toFixed(0)}% Stake Share)
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label>From Date</label>
            <input type="datetime-local" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
          </div>

          <div className="form-field">
            <label>To Date</label>
            <input type="datetime-local" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </div>

          <div className="form-field">
            <label>Sport</label>
            <select value={sport} onChange={(e) => setSport(e.target.value)}>
              <option value="">All Sports</option>
              {SPORTS.map((s) => (
                <option key={s} value={s}>{s.replace('_', ' ')}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label>Signal Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All Statuses</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label>Bookmaker</label>
            <select value={bookmaker} onChange={(e) => setBookmaker(e.target.value)}>
              <option value="">All Bookmakers</option>
              <option value="stoiximan">Stoiximan (Default)</option>
              <option value="betano">Betano</option>
              <option value="bet365">Bet365</option>
            </select>
          </div>

          <div className="form-field">
            <label>Include Paper Bets</label>
            <select value={includeDryRun ? 'yes' : 'no'} onChange={(e) => setIncludeDryRun(e.target.value === 'yes')}>
              <option value="yes">Yes (include dry-run)</option>
              <option value="no">No (real bets only)</option>
            </select>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 'var(--space-3)', marginTop: 'var(--space-4)', flexWrap: 'wrap' }}>
          <button className="btn btn-primary" onClick={generateReport} disabled={loading}>
            {loading ? <><span className="spinner" style={{ width: '14px', height: '14px' }} /> Generating…</> : '🔍 Generate Report'}
          </button>

          <button
            className="btn btn-ghost"
            onClick={() => exportFile('csv')}
            disabled={exporting || reportData.length === 0}
            title="Download CSV formatted file"
          >
            {exporting ? <span className="spinner" style={{ width: '14px', height: '14px' }} /> : '📥 Export CSV'}
          </button>

          <button
            className="btn btn-ghost"
            onClick={() => exportFile('xlsx')}
            disabled={exporting || reportData.length === 0}
            title="Download formatted Excel spreadsheet"
          >
            {exporting ? <span className="spinner" style={{ width: '14px', height: '14px' }} /> : '📊 Export Excel (.xlsx)'}
          </button>
        </div>
      </div>

      {/* Client Active Notice */}
      {selectedAccountObj && (
        <div
          className="glass-panel"
          style={{
            padding: '12px 18px',
            marginBottom: 'var(--space-5)',
            borderLeft: '4px solid var(--brand-accent)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '10px',
          }}
        >
          <div>
            <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Currently viewing report for Client:</span>{' '}
            <strong style={{ color: '#fff', fontSize: '1rem' }}>{selectedAccountObj.name}</strong>{' '}
            <span className="pill" style={{ background: 'rgba(99, 102, 241, 0.2)', color: '#a5b4fc', fontSize: '0.75rem' }}>
              Platform: {selectedAccountObj.bookmaker.toUpperCase()}
            </span>{' '}
            <span className="pill" style={{ background: 'rgba(16, 185, 129, 0.2)', color: '#34d399', fontSize: '0.75rem' }}>
              Stake Share: {(selectedAccountObj.percentage_share * 100).toFixed(0)}%
            </span>
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setSelectedAccountId('')}
            style={{ fontSize: '0.75rem' }}
          >
            Reset to Global View
          </button>
        </div>
      )}

      {/* KPI Metrics Summary Bar */}
      <div className="kpi-grid" style={{ marginBottom: 'var(--space-5)' }}>
        <div className="glass-panel kpi-card">
          <span className="kpi-label">Total Bets</span>
          <span className="kpi-value">{totalBets}</span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {wonBets} Won · {lostBets} Lost · {pendingBets} Open
          </span>
        </div>

        <div className="glass-panel kpi-card">
          <span className="kpi-label">Win Rate</span>
          <span className="kpi-value" style={{ color: Number(winRate.replace(',', '.')) >= 50 ? 'var(--status-success)' : 'var(--text-primary)' }}>
            {winRate}%
          </span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Settled selections</span>
        </div>

        <div className="glass-panel kpi-card">
          <span className="kpi-label">{selectedAccountObj ? 'Player Total Staked' : 'Total Staked'}</span>
          <span className="kpi-value">{formatEuropeanCurrency(totalStaked)}</span>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
            {selectedAccountObj ? `${selectedAccountObj.name} allocation` : 'All placed bets'}
          </span>
        </div>

        <div className="glass-panel kpi-card">
          <span className="kpi-label">{selectedAccountObj ? 'Player Net P&L' : 'Net Profit / Loss'}</span>
          <span
            className="kpi-value"
            style={{ color: totalProfit >= 0 ? 'var(--status-success)' : 'var(--status-danger)' }}
          >
            {totalProfit >= 0 ? '+' : ''}{formatEuropeanCurrency(totalProfit)}
          </span>
          <span style={{ fontSize: '0.75rem', color: totalProfit >= 0 ? 'var(--status-success)' : 'var(--status-danger)' }}>
            ROI: {Number(roi.replace(',', '.')) >= 0 ? '+' : ''}{roi}%
          </span>
        </div>
      </div>

      {/* Preview Table */}
      {reportData.length === 0 && !loading ? (
        <div className="glass-panel empty-state">
          Set filters and click <strong>Generate Report</strong> to preview results
        </div>
      ) : (
        <div className="glass-panel" style={{ padding: 'var(--space-5)' }}>
          <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>Preview ({reportData.length} rows) — European Style Odds &amp; Comparisons</span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              Values formatted in European comma decimal (e.g. 2,25)
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ minWidth: '130px' }}>Settle Bet</th>
                  {columns.map((col) => (
                    <th key={col} title={col}>
                      {COLUMN_LABELS[col] ?? col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {reportData.slice(0, 100).map((row, i) => (
                  <tr key={i}>
                    <td>
                      {row.bet_id && row.outcome === 'pending' ? (
                        <div style={{ display: 'flex', gap: '4px' }}>
                          <button
                            className="btn btn-sm"
                            style={{ background: 'rgba(16, 185, 129, 0.2)', color: '#34d399', border: '1px solid rgba(16, 185, 129, 0.4)', padding: '2px 6px', fontSize: '0.75rem', borderRadius: '4px' }}
                            disabled={settlingId === row.bet_id}
                            title="Mark Won"
                            onClick={() => handleSettle(row.bet_id as number, 'won')}
                          >
                            ✓ Won
                          </button>
                          <button
                            className="btn btn-sm"
                            style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#f87171', border: '1px solid rgba(239, 68, 68, 0.4)', padding: '2px 6px', fontSize: '0.75rem', borderRadius: '4px' }}
                            disabled={settlingId === row.bet_id}
                            title="Mark Lost"
                            onClick={() => handleSettle(row.bet_id as number, 'lost')}
                          >
                            ✗ Lost
                          </button>
                        </div>
                      ) : row.outcome === 'won' ? (
                        <span className="pill" style={{ background: 'rgba(16, 185, 129, 0.2)', color: '#34d399', fontSize: '0.7rem' }}>WON</span>
                      ) : row.outcome === 'lost' ? (
                        <span className="pill" style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#f87171', fontSize: '0.7rem' }}>LOST</span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>{String(row.outcome || '—')}</span>
                      )}
                    </td>
                    {columns.map((col) => {
                      const val = row[col];
                      let display = val == null ? '—' : String(val);
                      let cls = '';

                      // Format European odds
                      if (col === 'target_odds' || col === 'placed_odds' || col === 'betfair_odds' || col === 'pinnacle_odds') {
                        display = formatEuropeanOdds(val);
                      }

                      // Format Currency
                      if (col === 'recommended_stake' || col === 'stake' || col === 'allocated_stake' || col === 'max_bet') {
                        if (val != null && !isNaN(Number(val))) {
                          display = formatEuropeanCurrency(val);
                        }
                      }

                      if (col === 'profit' || col === 'player_profit') {
                        if (val != null && !isNaN(Number(val))) {
                          const num = Number(val);
                          cls = num >= 0 ? 'positive' : 'negative';
                          display = (num >= 0 ? '+' : '') + formatEuropeanCurrency(num);
                        }
                      }

                      if (col === 'bet_type') {
                        return (
                          <td key={col}>
                            <span className="pill" style={{ background: 'rgba(99, 102, 241, 0.12)', color: '#a5b4fc', fontSize: '0.75rem' }}>
                              {display}
                            </span>
                          </td>
                        );
                      }

                      if (col === 'target_bookmaker') {
                        return (
                          <td key={col}>
                            <span className="pill" style={{ background: 'rgba(245, 158, 11, 0.15)', color: '#fbbf24', fontSize: '0.75rem', fontWeight: 700 }}>
                              🎯 {display.toUpperCase()}
                            </span>
                          </td>
                        );
                      }

                      if (col === 'edge' || col === 'actual_edge') {
                        const num = parseFloat(display);
                        if (!isNaN(num)) cls = num > 5 ? 'edge-high' : num > 2 ? 'edge-medium' : 'edge-low';
                        display = display.replace('.', ',');
                      }

                      if (col === 'is_live') {
                        return (
                          <td key={col}>
                            {display === 'Live'
                              ? <span className="pill pill-live" style={{ fontSize: '0.65rem' }}>LIVE</span>
                              : <span className="pill pill-prematch" style={{ fontSize: '0.65rem' }}>PRE-MATCH</span>
                            }
                          </td>
                        );
                      }

                      return <td key={col} className={cls}>{display}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {reportData.length > 100 && (
              <div style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 'var(--space-4)', fontSize: '0.875rem' }}>
                Showing first 100 of {reportData.length} rows — export to see all
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
