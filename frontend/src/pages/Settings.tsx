import React, { useState, useEffect } from 'react';

const API = '/api';

interface ConfigData {
  edge_threshold: number;
  live_edge_threshold: number;
  confirmation_tolerance: number;
  max_live_latency_seconds: number;
  max_prematch_latency_seconds: number;
  min_total_matched: number;
  min_liquidity: number;
  max_spread: number;
  require_confirmation: boolean;
  favorite_min_prob: number;
  kelly_fraction: number;
  max_stake: number;
  max_event_exposure: number;
  bankroll: number;
  poll_interval_live: number;
  poll_interval_prematch: number;
  placement_dry_run: boolean;
  placement_require_approval: boolean;
  allow_demo_fallback: boolean;
  placement_bookmaker: string;
}

const FIELD_LABELS: Record<string, { label: string; description: string; type: string; min?: number; max?: number; step?: number }> = {
  edge_threshold: { label: 'Edge Threshold', description: 'Minimum edge required to generate a value signal (pre-match)', type: 'percent', min: 0, max: 1, step: 0.005 },
  live_edge_threshold: { label: 'Live Edge Threshold', description: 'Minimum edge required for live (in-play) signals — should be higher than pre-match', type: 'percent', min: 0, max: 1, step: 0.005 },
  kelly_fraction: { label: 'Kelly Fraction', description: 'Fraction of full Kelly to stake (0.25 = quarter Kelly)', type: 'percent', min: 0, max: 1, step: 0.05 },
  max_stake: { label: 'Max Stake per Bet (€)', description: 'Hard cap on any single bet stake regardless of Kelly sizing', type: 'number', min: 0, step: 1 },
  bankroll: { label: 'Bankroll (€)', description: 'Total available capital — used for Kelly stake calculations', type: 'number', min: 0, step: 50 },
  max_event_exposure: { label: 'Max Event Exposure (€)', description: 'Maximum total staked on a single event across all markets', type: 'number', min: 0, step: 5 },
  min_total_matched: { label: 'Min Total Matched (€)', description: 'Minimum Betfair market liquidity required to consider a signal', type: 'number', min: 0, step: 100 },
  min_liquidity: { label: 'Min Liquidity (€)', description: 'Minimum available liquidity per selection', type: 'number', min: 0, step: 5 },
  max_spread: { label: 'Max Back/Lay Spread', description: 'Maximum acceptable spread between back and lay odds', type: 'percent', min: 0, max: 0.5, step: 0.01 },
  confirmation_tolerance: { label: 'Confirmation Tolerance', description: 'How closely Pinnacle probability must match Betfair to confirm a signal', type: 'percent', min: 0, max: 0.5, step: 0.01 },
  poll_interval_live: { label: 'Live Poll Interval (sec)', description: 'How often to scan live markets (seconds)', type: 'number', min: 5, step: 5 },
  poll_interval_prematch: { label: 'Pre-Match Poll Interval (sec)', description: 'How often to scan pre-match markets (seconds)', type: 'number', min: 30, step: 30 },
  placement_dry_run: { label: 'Paper Trading Mode', description: 'When enabled, bets are simulated — no real money is staked', type: 'boolean' },
  placement_require_approval: { label: 'Require Manual Approval', description: 'When enabled, each signal must be manually approved before placement', type: 'boolean' },
  require_confirmation: { label: 'Require Pinnacle Confirmation', description: 'When enabled, a signal requires a matching Pinnacle price', type: 'boolean' },
  allow_demo_fallback: { label: 'Allow Demo Fallback', description: 'When enabled, uses fake demo data if no real signals found. Keep OFF in production!', type: 'boolean' },
};

const GROUPS = [
  {
    title: '🎯 Value Detection',
    fields: ['edge_threshold', 'live_edge_threshold', 'confirmation_tolerance', 'require_confirmation'],
  },
  {
    title: '💰 Stake Sizing',
    fields: ['kelly_fraction', 'max_stake', 'bankroll', 'max_event_exposure'],
  },
  {
    title: '📊 Market Health',
    fields: ['min_total_matched', 'min_liquidity', 'max_spread'],
  },
  {
    title: '⏱ Scanning Schedule',
    fields: ['poll_interval_live', 'poll_interval_prematch'],
  },
  {
    title: '⚙️ Placement Controls',
    fields: ['placement_dry_run', 'placement_require_approval', 'allow_demo_fallback'],
  },
];

export const Settings: React.FC = () => {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  
  const [cookieJson, setCookieJson] = useState('');
  const [cookieUploading, setCookieUploading] = useState(false);
  const [cookieStatus, setCookieStatus] = useState<{msg: string, type: string} | null>(null);

  // Odds API Key Management
  const [oddsKeyInput, setOddsKeyInput] = useState('');
  const [oddsApiStatus, setOddsApiStatus] = useState<{
    configured: boolean;
    valid: boolean;
    masked_key: string;
    message: string;
    requests_remaining: number | string | null;
    requests_used: number | string | null;
  } | null>(null);
  const [checkingOddsApi, setCheckingOddsApi] = useState(false);
  const [savingOddsApi, setSavingOddsApi] = useState(false);
  const [oddsApiFeedback, setOddsApiFeedback] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  const fetchOddsApiStatus = async () => {
    setCheckingOddsApi(true);
    try {
      const res = await fetch(`${API}/odds-api/status`);
      if (res.ok) {
        setOddsApiStatus(await res.json());
      }
    } finally {
      setCheckingOddsApi(false);
    }
  };

  const handleUpdateOddsKey = async () => {
    if (!oddsKeyInput.trim()) return;
    setSavingOddsApi(true);
    setOddsApiFeedback(null);
    try {
      const res = await fetch(`${API}/odds-api/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key: oddsKeyInput.trim() }),
      });
      const data = await res.json();
      if (res.ok) {
        setOddsApiFeedback({ msg: data.message || "API key saved and now LIVE!", type: 'success' });
        setOddsKeyInput('');
        fetchOddsApiStatus();
      } else {
        setOddsApiFeedback({ msg: data.detail || "Failed to update API key", type: 'error' });
      }
    } catch (e: any) {
      setOddsApiFeedback({ msg: e.message || "Network error", type: 'error' });
    } finally {
      setSavingOddsApi(false);
    }
  };

  useEffect(() => {
    fetch(`${API}/config`)
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => setError('Failed to load config'));
    fetchOddsApiStatus();
  }, []);

  const getValue = (key: string) => {
    if (key in edits) return edits[key];
    return (config as unknown as Record<string, unknown>)?.[key];
  };

  const handleChange = (key: string, value: unknown) => {
    setEdits((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setError('');
    try {
      const res = await fetch(`${API}/config`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(edits),
      });
      if (res.ok) {
        setSaved(true);
        setEdits({});
        const refreshed = await fetch(`${API}/config`);
        if (refreshed.ok) setConfig(await refreshed.json());
      } else {
        const err = await res.json();
        setError(err.detail || 'Failed to save');
      }
    } finally {
      setSaving(false);
    }
  };

  const handleUploadCookies = async () => {
    if (!cookieJson.trim()) return;
    setCookieUploading(true);
    setCookieStatus(null);
    try {
      const parsed = JSON.parse(cookieJson);
      if (!Array.isArray(parsed)) throw new Error("Cookies must be a JSON array");
      
      const res = await fetch(`${API}/cookies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsed),
      });
      if (res.ok) {
        const data = await res.json();
        setCookieStatus({ msg: data.message || "Cookies saved successfully", type: "success" });
        setCookieJson('');
      } else {
        const err = await res.json();
        setCookieStatus({ msg: err.detail || "Upload failed", type: "error" });
      }
    } catch (e: any) {
      setCookieStatus({ msg: e.message || "Invalid JSON", type: "error" });
    } finally {
      setCookieUploading(false);
    }
  };

  const renderField = (key: string) => {
    const meta = FIELD_LABELS[key];
    if (!meta) return null;
    const val = getValue(key);
    const isDirty = key in edits;

    return (
      <div key={key} className={`config-field ${isDirty ? 'config-field--dirty' : ''}`}>
        <div className="config-field__header">
          <label className="config-field__label">
            {meta.label}
            {isDirty && <span className="config-field__dirty-badge">Modified</span>}
          </label>
          <span className="config-field__desc">{meta.description}</span>
        </div>

        {meta.type === 'boolean' ? (
          <div className="config-toggle">
            <button
              className={`toggle-btn ${val ? 'toggle-btn--on' : 'toggle-btn--off'}`}
              onClick={() => handleChange(key, !val)}
            >
              <span className="toggle-knob" />
            </button>
            <span style={{ color: val ? 'var(--status-success)' : 'var(--text-muted)', fontSize: '0.875rem', fontWeight: 500 }}>
              {val ? 'Enabled' : 'Disabled'}
            </span>
          </div>
        ) : meta.type === 'percent' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <input
              type="range"
              min={meta.min ?? 0}
              max={meta.max ?? 1}
              step={meta.step ?? 0.01}
              value={Number(val) || 0}
              onChange={(e) => handleChange(key, parseFloat(e.target.value))}
              style={{ flex: 1, accentColor: 'var(--brand-primary)' }}
            />
            <input
              type="number"
              min={meta.min ?? 0}
              max={meta.max ?? 1}
              step={meta.step ?? 0.01}
              value={Number(val) || 0}
              onChange={(e) => handleChange(key, parseFloat(e.target.value))}
              style={{ width: '90px', textAlign: 'right' }}
            />
            <span style={{ color: 'var(--text-muted)', width: '42px' }}>
              {((Number(val) || 0) * 100).toFixed(1)}%
            </span>
          </div>
        ) : (
          <input
            type="number"
            min={meta.min}
            step={meta.step ?? 1}
            value={Number(val) || 0}
            onChange={(e) => handleChange(key, parseFloat(e.target.value))}
            style={{ maxWidth: '200px' }}
          />
        )}
      </div>
    );
  };

  if (!config) {
    return (
      <div className="settings-page">
        <div className="page-header">
          <h1 className="page-title text-gradient">Settings</h1>
        </div>
        <div className="glass-panel empty-state">
          {error || <><span className="spinner" /> Loading configuration…</>}
        </div>
      </div>
    );
  }

  return (
    <div className="settings-page">
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Settings</h1>
          <p className="page-subtitle">Edit live configuration variables — changes are written to .env</p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center' }}>
          {Object.keys(edits).length > 0 && (
            <span style={{ fontSize: '0.875rem', color: 'var(--status-warning)' }}>
              {Object.keys(edits).length} unsaved change{Object.keys(edits).length !== 1 ? 's' : ''}
            </span>
          )}
          {saved && (
            <span style={{ fontSize: '0.875rem', color: 'var(--status-success)' }}>
              ✓ Saved — restart app to apply all changes
            </span>
          )}
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || Object.keys(edits).length === 0}
          >
            {saving ? <><span className="spinner" style={{ width: '14px', height: '14px' }} /> Saving…</> : '💾 Save Changes'}
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: 'var(--space-3)', background: 'var(--status-danger-bg)', borderRadius: 'var(--radius-lg)', color: 'var(--status-danger)', marginBottom: 'var(--space-5)' }}>
          {error}
        </div>
      )}

      <div className="settings-groups">
        
        {/* The Odds API Key Management Section */}
        <div className="glass-panel settings-group" style={{ borderColor: 'rgba(99, 102, 241, 0.3)', marginBottom: 'var(--space-4)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 'var(--space-3)' }}>
            <div className="section-title" style={{ margin: 0 }}>
              🔑 The Odds API Key & Live Quota
            </div>
            {oddsApiStatus && (
              <span
                className="pill"
                style={{
                  background: oddsApiStatus.valid ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                  color: oddsApiStatus.valid ? '#34d399' : '#f87171',
                  fontWeight: 600,
                  fontSize: '0.78rem',
                  padding: '4px 10px',
                }}
              >
                {oddsApiStatus.valid ? '● Active & Live' : '● Invalid / Expired'}
              </span>
            )}
          </div>

          <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: 'var(--space-4)' }}>
            Check if your Odds API key is active or expired, view remaining quota, and update it live without server restarts.
          </div>

          {/* Current Quota Status Cards */}
          {oddsApiStatus && (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 'var(--space-3)',
              background: 'rgba(0, 0, 0, 0.25)',
              padding: 'var(--space-3)',
              borderRadius: 'var(--radius-md)',
              marginBottom: 'var(--space-4)',
            }}>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Current Key</div>
                <div style={{ fontSize: '1rem', fontWeight: 600, fontFamily: 'monospace', color: 'var(--brand-accent)' }}>
                  {oddsApiStatus.masked_key || 'Not configured'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Requests Remaining</div>
                <div style={{ fontSize: '1.2rem', fontWeight: 700, color: '#34d399' }}>
                  {oddsApiStatus.requests_remaining !== null ? `${oddsApiStatus.requests_remaining}` : '—'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Requests Used</div>
                <div style={{ fontSize: '1.2rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                  {oddsApiStatus.requests_used !== null ? `${oddsApiStatus.requests_used}` : '—'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Status</div>
                <div style={{ fontSize: '0.85rem', fontWeight: 600, color: oddsApiStatus.valid ? '#34d399' : '#f87171' }}>
                  {oddsApiStatus.message}
                </div>
              </div>
            </div>
          )}

          {/* Feedback banner */}
          {oddsApiFeedback && (
            <div style={{
              padding: '10px 14px',
              borderRadius: 'var(--radius-md)',
              marginBottom: 'var(--space-4)',
              background: oddsApiFeedback.type === 'success' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
              color: oddsApiFeedback.type === 'success' ? '#34d399' : '#f87171',
              border: oddsApiFeedback.type === 'success' ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid rgba(239, 68, 68, 0.3)',
              fontSize: '0.875rem',
            }}>
              {oddsApiFeedback.msg}
            </div>
          )}

          {/* Form to update key */}
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <input
              type="text"
              placeholder="Paste new Odds API key (e.g. d78aa876cd16f312...)"
              value={oddsKeyInput}
              onChange={(e) => setOddsKeyInput(e.target.value)}
              style={{
                flex: 1,
                minWidth: '280px',
                padding: '10px 14px',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-elevated)',
                border: 'var(--glass-border)',
                color: '#fff',
                fontFamily: 'monospace',
                fontSize: '0.875rem',
              }}
            />
            <button
              className="btn btn-secondary"
              onClick={fetchOddsApiStatus}
              disabled={checkingOddsApi}
              title="Test current key status"
            >
              {checkingOddsApi ? 'Checking…' : '🔍 Check Status'}
            </button>
            <button
              className="btn btn-primary"
              onClick={handleUpdateOddsKey}
              disabled={savingOddsApi || !oddsKeyInput.trim()}
            >
              {savingOddsApi ? 'Validating & Saving…' : '⚡ Verify & Set Live'}
            </button>
          </div>
        </div>

        {/* Cookies Section */}
        <div className="glass-panel settings-group">
          <div className="section-title">🍪 Browser Cookies (Stoiximan)</div>
          <div className="config-fields">
            <div className="config-field">
              <div className="config-field__header">
                <label className="config-field__label">Upload Cookies JSON</label>
                <span className="config-field__desc">
                  Export cookies from your browser (using Cookie-Editor extension) on Stoiximan and paste the raw JSON array here.
                </span>
              </div>
              <textarea 
                value={cookieJson}
                onChange={e => setCookieJson(e.target.value)}
                placeholder='[{"domain": ".stoiximan.gr", "name": "...", "value": "..."}]'
                style={{ width: '100%', minHeight: '100px', fontFamily: 'monospace', fontSize: '0.8rem', background: 'var(--bg-elevated)', border: 'var(--glass-border)', color: 'var(--text-primary)', padding: '8px', borderRadius: '4px' }}
              />
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginTop: '10px' }}>
                <button 
                  className="btn btn-primary btn-sm" 
                  onClick={handleUploadCookies}
                  disabled={cookieUploading || !cookieJson.trim()}
                >
                  {cookieUploading ? 'Uploading...' : 'Save Cookies'}
                </button>
                {cookieStatus && (
                  <span style={{ fontSize: '0.85rem', color: cookieStatus.type === 'error' ? 'var(--status-danger)' : 'var(--status-success)' }}>
                    {cookieStatus.msg}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {GROUPS.map((group) => (
          <div key={group.title} className="glass-panel settings-group">
            <div className="section-title">{group.title}</div>
            <div className="config-fields">
              {group.fields.map(renderField)}
            </div>
          </div>
        ))}
      </div>

      {/* Dry-run warning */}
      {config.placement_dry_run && (
        <div className="glass-panel" style={{ padding: 'var(--space-4)', marginTop: 'var(--space-5)', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
          <strong style={{ color: 'var(--status-warning)' }}>⚠️ Paper Trading Mode is ON</strong>
          <p style={{ color: 'var(--text-secondary)', marginTop: '4px', fontSize: '0.875rem' }}>
            No real bets are being placed. Toggle off "Paper Trading Mode" above when ready to go live.
          </p>
        </div>
      )}
    </div>
  );
};
