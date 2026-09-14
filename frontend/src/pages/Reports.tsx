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
  market_type: 'Market Type',
  is_live: 'Live / Pre-Match',
  edge: 'Edge %',
  fair_prob: 'Fair Probability',
  confirm_prob: 'Confirmation Probability',
  target_odds: 'Target Odds',
  placed_odds: 'Odds at Placement',
  recommended_stake: 'Recommended Stake',
  requested_stake: 'Stake Requested',
  stake: 'Stake Accepted',
  outcome: 'Outcome',
  profit: 'Net Profit / Loss',
  actual_edge: 'Actual Edge %',
  clv: 'Closing Line Value',
  dry_run: 'Paper Bet',
  detected_at: 'Bet Identified At',
  placed_at: 'Bet Placed At',
  bookmaker: 'Bookmaker',
  status: 'Signal Status',
  max_bet: 'Max Bet Available',
  variables_complete: 'All Variables Available',
};

interface ReportRow {
  [key: string]: string | number | boolean | null | undefined;
}

export const Reports: React.FC = () => {
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [sport, setSport] = useState('');
  const [status, setStatus] = useState('');
  const [bookmaker, setBookmaker] = useState('');
  const [includeDryRun, setIncludeDryRun] = useState(true);
  const [reportData, setReportData] = useState<ReportRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [settlingId, setSettlingId] = useState<number | null>(null);

  const buildParams = () => ({
    date_from: dateFrom || null,
    date_to: dateTo || null,
    sport: sport || null,
    status: status || null,
    bookmaker: bookmaker || null,
    include_dry_run: includeDryRun,
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
  }, []);

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

  const columns = reportData.length > 0 ? Object.keys(reportData[0]) : [];

  // Summary KPI Metrics
  const bets = reportData.filter((r) => r.bet_id != null);
  const totalBets = bets.length;
  const wonBets = bets.filter((r) => r.outcome === 'won').length;
  const lostBets = bets.filter((r) => r.outcome === 'lost').length;
  const pendingBets = bets.filter((r) => r.outcome === 'pending').length;
  const totalProfit = bets.reduce((acc, r) => acc + (typeof r.profit === 'number' ? r.profit : 0), 0);
  const totalStaked = bets.reduce((acc, r) => acc + (typeof r.stake === 'number' ? r.stake : 0), 0);
  const winRate = (wonBets + lostBets) > 0 ? ((wonBets / (wonBets + lostBets)) * 100).toFixed(1) : '0.0';
  const roi = totalStaked > 0 ? ((totalProfit / totalStaked) * 100).toFixed(1) : '0.0';

  return (
    <div className="reports-page">
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Reports & Export</h1>
          <p className="page-subtitle">Generate, preview, and export bet history with custom filters</p>
        </div>
      </div>

      {/* Filter Panel */}
      <div className="glass-panel" style={{ padding: 'var(--space-5)', marginBottom: 'var(--space-5)' }}>
        <div className="section-title">Filter Options</div>

        <div className="report-filters">
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
              <option value="stoiximan">Stoiximan</option>
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
            className="btn btn-success"
            onClick={() => exportFile('csv')}
            disabled={exporting || reportData.length === 0}
            title="Export as CSV with user-friendly column names"
          >
            {exporting ? <><span className="spinner" style={{ width: '14px', height: '14px' }} /> Exporting…</> : '⬇ Export CSV'}
          </button>

          <button
            className="btn btn-success"
            onClick={() => exportFile('xlsx')}
            disabled={exporting || reportData.length === 0}
            title="Export as Excel (.xlsx) with user-friendly column names"
          >
            {exporting ? <><span className="spinner" style={{ width: '14px', height: '14px' }} /> Exporting…</> : '⬇ Export Excel (.xlsx)'}
          </button>

          {reportData.length > 0 && (
            <span style={{ alignSelf: 'center', color: 'var(--text-muted)', fontSize: '0.875rem' }}>
              {reportData.length} row{reportData.length !== 1 ? 's' : ''} found
            </span>
          )}
        </div>
      </div>

      {/* Summary KPI Cards for Paper Trading / Testing */}
      {totalBets > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 'var(--space-4)', marginBottom: 'var(--space-5)' }}>
          <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Total Tracked Bets</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: 'var(--brand-accent)' }}>{totalBets}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>{pendingBets} pending</div>
          </div>
          <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Won / Lost</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700 }}>
              <span style={{ color: 'var(--accent-emerald, #10b981)' }}>{wonBets}</span> / <span style={{ color: 'var(--accent-rose, #ef4444)' }}>{lostBets}</span>
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>Win Rate: {winRate}%</div>
          </div>
          <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Simulated Net Profit</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: totalProfit >= 0 ? '#10b981' : '#ef4444' }}>
              {totalProfit >= 0 ? `+${totalProfit.toFixed(2)}` : totalProfit.toFixed(2)}
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>Staked: {totalStaked.toFixed(2)}</div>
          </div>
          <div className="glass-panel" style={{ padding: 'var(--space-4)', textAlign: 'center' }}>
            <div style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '4px' }}>Simulated ROI</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: Number(roi) >= 0 ? '#10b981' : '#ef4444' }}>
              {Number(roi) >= 0 ? `+${roi}%` : `${roi}%`}
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '4px' }}>Paper Trading Mode (No Risk)</div>
          </div>
        </div>
      )}

      {/* Column Labels Legend */}
      {reportData.length > 0 && (
        <div className="glass-panel" style={{ padding: 'var(--space-4)', marginBottom: 'var(--space-4)', fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          <strong style={{ color: 'var(--text-secondary)', display: 'block', marginBottom: '8px' }}>
            📋 Column Names (as they appear in exports):
          </strong>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
            {columns.map((col) => (
              <span key={col}>
                <code style={{ color: 'var(--brand-accent)', fontSize: '0.75rem' }}>{col}</code>
                {' → '}
                <strong style={{ color: 'var(--text-secondary)' }}>{COLUMN_LABELS[col] ?? col}</strong>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Preview Table */}
      {reportData.length === 0 && !loading ? (
        <div className="glass-panel empty-state">
          Set filters and click <strong>Generate Report</strong> to preview results
        </div>
      ) : (
        <div className="glass-panel" style={{ padding: 'var(--space-5)' }}>
          <div className="section-title">Preview ({reportData.length} rows)</div>
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

                      if (col === 'edge' || col === 'actual_edge') {
                        const num = parseFloat(display);
                        if (!isNaN(num)) cls = num > 5 ? 'edge-high' : num > 2 ? 'edge-medium' : 'edge-low';
                      }
                      if (col === 'profit' && val != null) {
                        const num = parseFloat(display);
                        cls = num >= 0 ? 'positive' : 'negative';
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
