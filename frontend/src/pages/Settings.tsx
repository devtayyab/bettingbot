import React, { useState, useEffect } from 'react';

const API = '/api';

interface ConfigData {
  edge_threshold: number;
  live_edge_threshold: number;
  re_read_min_edge_delta: number;
  confirmation_tolerance: number;
  max_live_latency_seconds: number;
  max_prematch_latency_seconds: number;
  min_total_matched: number;
  min_liquidity: number;
  max_spread: number;
  strong_total_matched: number;
  strong_liquidity: number;
  good_spread: number;
  depth_band: number;
  betfair_ladder_levels: number;
  min_market_quality: number;
  enable_edge_buffer: boolean;
  slippage_buffer: number;
  live_slippage_buffer: number;
  quality_penalty_max: number;
  unknown_spread_prob: number;
  require_confirmation: boolean;
  enable_pinnacle_confirmation: boolean;
  min_odds: number;
  max_odds: number;
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
  odds_api_markets?: string;
  odds_api_fetch_lay?: boolean;
}

const MARKET_OPTIONS = [
  { key: 'h2h', label: 'Match Winner / 1X2 (h2h)', desc: 'Home Win, Draw, Away Win (Football, Basketball, Tennis)' },
  { key: 'totals', label: 'Over / Under Goals & Points (totals)', desc: 'Total goals/points lines (e.g. Over 2.5, Under 2.5)' },
  { key: 'spreads', label: 'Handicap / Point Spread (spreads)', desc: 'Asian & European handicap lines (e.g. -1.5, +1.5)' },
];

const FIELD_LABELS: Record<
  string,
  {
    label: string;
    description: string;
    recommended?: string;
    type: 'percent' | 'number' | 'boolean' | 'markets';
    min?: number;
    max?: number;
    step?: number;
  }
> = {
  odds_api_markets: {
    label: 'Bet Markets to Scan',
    description:
      'Choose which betting markets the scanner reads from bookmakers. Enabling Over/Under and Handicap finds value bets beyond Match Winner (each extra market uses 1 API credit per active league scan).',
    recommended: 'Match Winner + Over/Under + Handicap',
    type: 'markets',
  },
  edge_threshold: {
    label: 'Pre-Match Edge Threshold (Min Value %)',
    description:
      'Minimum mathematical value advantage (Fair Probability × Bookmaker Odds − 1) required to flag a pre-match bet. Example: 2.0% means if true fair odds are 2.00 (50%), target odds must be at least 2.04+ (plus buffer if Edge Buffer is ON).',
    recommended: '1.5% – 2.5%',
    type: 'percent',
    min: 0,
    max: 0.25,
    step: 0.0025,
  },
  live_edge_threshold: {
    label: 'Live (In-Play) Edge Threshold',
    description:
      'Minimum value edge required for live in-play matches. Kept slightly higher than pre-match because live odds move quickly during play.',
    recommended: '3.0% – 4.0%',
    type: 'percent',
    min: 0,
    max: 0.25,
    step: 0.0025,
  },
  re_read_min_edge_delta: {
    label: 'Re-Read Minimum Value Change (%)',
    description:
      'Prevents duplicate signals for the exact same bet (same Match + Market + Selection). Once a bet is read, the bot will ONLY re-read or update it if its value (edge) changes by at least this percentage.',
    recommended: '1.5%',
    type: 'percent',
    min: 0,
    max: 0.15,
    step: 0.0025,
  },
  favorite_min_prob: {
    label: 'Minimum Win Probability Filter',
    description:
      'Skips unlikely selections whose fair win probability is below this percentage. Warning: setting this high (e.g. 40%) blocks almost all Draws, Underdogs, and higher-odds bets! Set to 15% (or 0%) to scan full matches.',
    recommended: '10% – 15%',
    type: 'percent',
    min: 0,
    max: 0.8,
    step: 0.01,
  },
  min_odds: {
    label: 'Minimum Bookmaker Odds',
    description: 'Lowest decimal odds the bot will bet on (filters out heavy odds-on selections paying very little return).',
    recommended: '1.25 – 1.40',
    type: 'number',
    min: 1.01,
    step: 0.05,
  },
  max_odds: {
    label: 'Maximum Bookmaker Odds',
    description: 'Highest decimal odds allowed. Keeps variance manageable by ignoring extreme longshots.',
    recommended: '5.00 – 7.00',
    type: 'number',
    min: 1.1,
    step: 0.1,
  },
  enable_pinnacle_confirmation: {
    label: 'Use Pinnacle Sharp Reference',
    description:
      'Uses Pinnacle (sharpest low-margin bookmaker) both to cross-check Betfair Exchange prices AND as a sharp reference fallback on Over/Under & Handicap markets where Betfair Exchange data is unavailable.',
    recommended: 'Enabled',
    type: 'boolean',
  },
  require_confirmation: {
    label: 'Strict Dual-Source Confirmation (Betfair + Pinnacle)',
    description:
      'If enabled, a bet is ONLY accepted when BOTH Betfair Exchange AND Pinnacle list the exact market and agree within tolerance. Keep Disabled if you want Over/Under & Handicap bets when only one sharp source lists them.',
    recommended: 'Disabled',
    type: 'boolean',
  },
  confirmation_tolerance: {
    label: 'Betfair vs Pinnacle Agreement Tolerance',
    description:
      'Maximum allowed probability difference between Betfair and Pinnacle when cross-checking. If they disagree by more than this %, the bet is skipped.',
    recommended: '3.0% – 5.0%',
    type: 'percent',
    min: 0,
    max: 0.25,
    step: 0.005,
  },
  enable_edge_buffer: {
    label: 'Enable Safety Edge Buffer',
    description:
      'Adds an extra safety cushion on top of Edge Threshold to account for market spread, liquidity quality, and price slippage. Note: When ON, total required edge = Edge Threshold + Buffers (often +1.5% to +2.5% extra). Turn OFF if you want exact Edge Threshold matching.',
    recommended: 'Enabled (with 2% Edge Threshold) or Disabled',
    type: 'boolean',
  },
  slippage_buffer: {
    label: 'Pre-Match Slippage Buffer',
    description: 'Extra value % added when Safety Edge Buffer is ON to cover slight odds drops before placement.',
    recommended: '0.5%',
    type: 'percent',
    min: 0,
    max: 0.1,
    step: 0.0025,
  },
  live_slippage_buffer: {
    label: 'Live Slippage Buffer',
    description: 'Extra value % added during live matches when Safety Edge Buffer is ON.',
    recommended: '1.5%',
    type: 'percent',
    min: 0,
    max: 0.1,
    step: 0.0025,
  },
  quality_penalty_max: {
    label: 'Low-Liquidity Quality Penalty (Max)',
    description:
      'Extra edge required when Betfair volume/depth metadata is thin or unavailable (such as standard Odds API feeds without full exchange depth ladders).',
    recommended: '1.0% – 2.0%',
    type: 'percent',
    min: 0,
    max: 0.1,
    step: 0.0025,
  },
  unknown_spread_prob: {
    label: 'Missing Lay-Price Spread Cushion',
    description: 'Small probability cushion added when only Back odds (not Lay odds) are available from the exchange.',
    recommended: '0.5% – 0.75%',
    type: 'percent',
    min: 0,
    max: 0.05,
    step: 0.0005,
  },
  kelly_fraction: {
    label: 'Kelly Stake Multiplier',
    description:
      'Controls how aggressively stakes scale with value edge (0.25 = Quarter-Kelly, safe industry standard for bankroll growth).',
    recommended: '25.0% (0.25)',
    type: 'percent',
    min: 0,
    max: 1,
    step: 0.05,
  },
  bankroll: {
    label: 'Total Bankroll (€)',
    description: 'Your total betting capital used to calculate proportional Kelly stake sizes.',
    recommended: '€1,000+',
    type: 'number',
    min: 0,
    step: 50,
  },
  max_stake: {
    label: 'Max Stake per Bet (€)',
    description: 'Hard safety ceiling on any single bet, regardless of how high the value edge is.',
    recommended: '1% – 2.5% of Bankroll',
    type: 'number',
    min: 0,
    step: 1,
  },
  max_event_exposure: {
    label: 'Max Total Exposure per Match (€)',
    description: 'Maximum combined € staked across all markets (1X2 + Over/Under + Handicap) on the same match.',
    recommended: '2% – 5% of Bankroll',
    type: 'number',
    min: 0,
    step: 5,
  },
  poll_interval_prematch: {
    label: 'Pre-Match Scan Interval (seconds)',
    description:
      'How often the bot scans upcoming pre-match games. 900s (15 min) balances timely odds discovery with conserving your monthly Odds API quota.',
    recommended: '900 sec (15 min)',
    type: 'number',
    min: 60,
    step: 60,
  },
  poll_interval_live: {
    label: 'Live Match Scan Interval (seconds)',
    description: 'How often live in-play markets are scanned. Increase to 300s+ if conserving API credits.',
    recommended: '300 sec (5 min)',
    type: 'number',
    min: 15,
    step: 15,
  },
  odds_api_fetch_lay: {
    label: 'Fetch Betfair Lay Odds (The Odds API)',
    description:
      'Requests Betfair lay prices alongside back prices for tighter fair-odds midpoint calculation. Costs 1 extra API credit per sport scan.',
    recommended: 'Disabled (saves quota) / Enabled (higher precision)',
    type: 'boolean',
  },
  min_total_matched: {
    label: 'Min Exchange Volume (€) [Direct Betfair Stream Only]',
    description: 'Minimum € matched on Betfair Exchange when connected directly via Betfair API certificates (ignored when volume is unknown on Odds API).',
    recommended: '€10,000',
    type: 'number',
    min: 0,
    step: 500,
  },
  min_liquidity: {
    label: 'Min Exchange Depth (€) [Direct Betfair Stream Only]',
    description: 'Minimum € available at best back/lay prices on direct Betfair stream.',
    recommended: '€100',
    type: 'number',
    min: 0,
    step: 10,
  },
  max_spread: {
    label: 'Max Back/Lay Spread (%)',
    description: 'Rejects illiquid exchange markets where the gap between Back and Lay odds is wider than this %.',
    recommended: '2.0% – 3.0%',
    type: 'percent',
    min: 0,
    max: 0.25,
    step: 0.005,
  },
  strong_total_matched: {
    label: 'Strong Volume Benchmark (€) [Direct Betfair Only]',
    description: 'Traded volume that earns a 100% volume quality score on direct Betfair feeds.',
    type: 'number',
    min: 0,
    step: 1000,
  },
  strong_liquidity: {
    label: 'Strong Depth Benchmark (€) [Direct Betfair Only]',
    description: 'Order-book depth that earns a 100% depth quality score on direct Betfair feeds.',
    type: 'number',
    min: 0,
    step: 50,
  },
  good_spread: {
    label: 'Ideal Back/Lay Spread (%)',
    description: 'Back/Lay spread at or below this % earns full market quality marks.',
    type: 'percent',
    min: 0,
    max: 0.1,
    step: 0.0025,
  },
  depth_band: {
    label: 'Order-Book Depth Band (%) [Direct Betfair Only]',
    description: 'Price distance around best odds counted toward order-book depth.',
    type: 'percent',
    min: 0,
    max: 0.2,
    step: 0.005,
  },
  betfair_ladder_levels: {
    label: 'Betfair Price Ladder Levels [Direct Betfair Only]',
    description: 'Number of order-book levels read from direct Betfair stream (1–10).',
    type: 'number',
    min: 1,
    max: 10,
    step: 1,
  },
  min_market_quality: {
    label: 'Minimum Market Quality Score',
    description: 'Hard floor (0%–100%) to reject low-quality markets. Keep at 0% when using The Odds API.',
    recommended: '0%',
    type: 'percent',
    min: 0,
    max: 1,
    step: 0.05,
  },
  placement_dry_run: {
    label: 'Paper Trading (Simulation) Mode',
    description:
      'Simulates bet placement and tracks official match scores & automatic Won/Lost settlement without risking real money.',
    recommended: 'Enabled during testing',
    type: 'boolean',
  },
  placement_require_approval: {
    label: 'Require Manual Approval Before Placing',
    description:
      'Holds detected signals in "Detected" state until you click Approve on Live Feed. When Disabled, qualified value bets are recorded automatically.',
    recommended: 'Disabled for automatic tracking',
    type: 'boolean',
  },
  allow_demo_fallback: {
    label: 'Allow Fake Demo Matches When Feed Is Empty',
    description:
      'WARNING: Injects synthetic test matches ("Team A vs Team B", fake demo fixtures) when API quota is 0 or no bets qualify. Keep DISABLED on live servers!',
    recommended: 'Disabled (OFF)',
    type: 'boolean',
  },
};

const GROUPS = [
  {
    title: '🎯 Value Detection & Bet Markets',
    subtitle: 'Controls which sports markets are scanned, minimum value edge, odds limits, and the 1.5% re-read rule.',
    fields: [
      'odds_api_markets',
      'edge_threshold',
      'live_edge_threshold',
      're_read_min_edge_delta',
      'favorite_min_prob',
      'min_odds',
      'max_odds',
      'enable_pinnacle_confirmation',
      'require_confirmation',
      'confirmation_tolerance',
    ],
  },
  {
    title: '🛡 Safety Edge Buffer',
    subtitle: 'Optional extra cushion added on top of Edge Threshold to protect against slippage and thin spreads.',
    fields: [
      'enable_edge_buffer',
      'slippage_buffer',
      'live_slippage_buffer',
      'quality_penalty_max',
      'unknown_spread_prob',
    ],
  },
  {
    title: '💰 Bankroll & Stake Sizing',
    subtitle: 'Controls how much is staked on each value bet using fractional Kelly sizing and hard exposure caps.',
    fields: ['bankroll', 'kelly_fraction', 'max_stake', 'max_event_exposure'],
  },
  {
    title: '⏱ Scanning Schedule & API Usage',
    subtitle: 'How frequently the scanner checks pre-match and live odds (affects monthly Odds API credit usage).',
    fields: ['poll_interval_prematch', 'poll_interval_live', 'odds_api_fetch_lay'],
  },
  {
    title: '⚙️ Execution & Demo Controls',
    subtitle: 'Paper-trading simulation, manual approval workflow, and demo data toggle.',
    fields: ['placement_dry_run', 'placement_require_approval', 'allow_demo_fallback'],
  },
  {
    title: '📊 Advanced Exchange Filters (Direct Betfair API)',
    subtitle: 'Order-book liquidity & spread filters primarily used when streaming directly from Betfair Exchange.',
    fields: [
      'max_spread',
      'good_spread',
      'min_market_quality',
      'min_total_matched',
      'min_liquidity',
      'strong_total_matched',
      'strong_liquidity',
      'depth_band',
      'betfair_ladder_levels',
    ],
  },
];

export const Settings: React.FC = () => {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [showGuide, setShowGuide] = useState(true);

  const [cookieJson, setCookieJson] = useState('');
  const [cookieUploading, setCookieUploading] = useState(false);
  const [cookieStatus, setCookieStatus] = useState<{ msg: string; type: string } | null>(null);

  // Demo Data Cleanup
  const [purgingDemo, setPurgingDemo] = useState(false);
  const [purgeFeedback, setPurgeFeedback] = useState<string | null>(null);

  // Odds API Key Management
  const [oddsKeyInput, setOddsKeyInput] = useState('');
  const [oddsApiStatus, setOddsApiStatus] = useState<{
    configured: boolean;
    valid: boolean;
    quota_exhausted?: boolean;
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
        setOddsApiFeedback({ msg: data.message || 'API key saved and now LIVE!', type: 'success' });
        setOddsKeyInput('');
        setOddsApiStatus({
          configured: true,
          valid: Boolean(data.valid),
          quota_exhausted: Boolean(data.quota_exhausted),
          masked_key: data.masked_key || '',
          message: data.message || 'API Key is Active & LIVE',
          requests_remaining: data.requests_remaining ?? null,
          requests_used: data.requests_used ?? null,
        });
      } else {
        setOddsApiFeedback({ msg: data.detail || 'Failed to update API key', type: 'error' });
      }
    } catch (e: any) {
      setOddsApiFeedback({ msg: e.message || 'Network error', type: 'error' });
    } finally {
      setSavingOddsApi(false);
    }
  };

  const handlePurgeDemoData = async () => {
    setPurgingDemo(true);
    setPurgeFeedback(null);
    try {
      const res = await fetch(`${API}/demo-data`, { method: 'DELETE' });
      const data = await res.json();
      if (res.ok) {
        setPurgeFeedback(
          `✓ Cleaned ${data.signals_deleted ?? 0} fake demo signals and ${data.bets_deleted ?? 0} fake demo bets.`
        );
      } else {
        setPurgeFeedback(`⚠️ ${data.detail || 'Failed to clean demo data'}`);
      }
    } catch (e: any) {
      setPurgeFeedback(`⚠️ ${e.message || 'Network error'}`);
    } finally {
      setPurgingDemo(false);
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

  const toggleMarketOption = (marketKey: string) => {
    const raw = String(getValue('odds_api_markets') || 'h2h');
    const current = raw
      .split(',')
      .map((m) => m.trim())
      .filter(Boolean);
    const exists = current.includes(marketKey);
    let next = exists ? current.filter((m) => m !== marketKey) : [...current, marketKey];
    if (next.length === 0) next = ['h2h'];
    handleChange('odds_api_markets', next.join(','));
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
      if (!Array.isArray(parsed)) throw new Error('Cookies must be a JSON array');

      const res = await fetch(`${API}/cookies`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(parsed),
      });
      if (res.ok) {
        const data = await res.json();
        setCookieStatus({ msg: data.message || 'Cookies saved successfully', type: 'success' });
        setCookieJson('');
      } else {
        const err = await res.json();
        setCookieStatus({ msg: err.detail || 'Upload failed', type: 'error' });
      }
    } catch (e: any) {
      setCookieStatus({ msg: e.message || 'Invalid JSON', type: 'error' });
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
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <label className="config-field__label" style={{ margin: 0 }}>
              {meta.label}
              {isDirty && <span className="config-field__dirty-badge">Modified</span>}
            </label>
            {meta.recommended && (
              <span
                style={{
                  fontSize: '0.7rem',
                  padding: '2px 8px',
                  borderRadius: '999px',
                  background: 'rgba(99, 102, 241, 0.14)',
                  color: '#a5b4fc',
                  border: '1px solid rgba(99, 102, 241, 0.25)',
                }}
              >
                Recommended: {meta.recommended}
              </span>
            )}
          </div>
          <span className="config-field__desc">{meta.description}</span>
        </div>

        {meta.type === 'markets' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '4px' }}>
            {MARKET_OPTIONS.map((opt) => {
              const selected = String(val || 'h2h')
                .split(',')
                .map((s) => s.trim())
                .includes(opt.key);
              return (
                <label
                  key={opt.key}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: '10px',
                    padding: '8px 12px',
                    borderRadius: 'var(--radius-md)',
                    background: selected ? 'rgba(16, 185, 129, 0.1)' : 'rgba(255,255,255,0.03)',
                    border: selected ? '1px solid rgba(16, 185, 129, 0.35)' : '1px solid rgba(255,255,255,0.08)',
                    cursor: 'pointer',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    onChange={() => toggleMarketOption(opt.key)}
                    style={{ marginTop: '3px', accentColor: 'var(--status-success)' }}
                  />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.86rem', color: 'var(--text-primary)' }}>{opt.label}</div>
                    <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>{opt.desc}</div>
                  </div>
                </label>
              );
            })}
          </div>
        ) : meta.type === 'boolean' ? (
          <div className="config-toggle">
            <button
              className={`toggle-btn ${val ? 'toggle-btn--on' : 'toggle-btn--off'}`}
              onClick={() => handleChange(key, !val)}
            >
              <span className="toggle-knob" />
            </button>
            <span
              style={{
                color: val ? 'var(--status-success)' : 'var(--text-muted)',
                fontSize: '0.875rem',
                fontWeight: 500,
              }}
            >
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
            <span style={{ color: 'var(--text-muted)', width: '52px', fontFamily: 'var(--font-mono)' }}>
              {((Number(val) || 0) * 100).toFixed(2)}%
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
          {error || (
            <>
              <span className="spinner" /> Loading configuration…
            </>
          )}
        </div>
      </div>
    );
  }

  const isQuotaExhausted =
    Boolean(oddsApiStatus?.quota_exhausted) ||
    (oddsApiStatus?.requests_remaining !== null &&
      oddsApiStatus?.requests_remaining !== undefined &&
      Number(oddsApiStatus.requests_remaining) <= 0);
  const isOddsKeyHealthy = Boolean(oddsApiStatus?.valid) && !isQuotaExhausted;

  return (
    <div className="settings-page">
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Settings & Strategy Guide</h1>
          <p className="page-subtitle">
            Live scanner rules, value thresholds, market coverage, and API key management — changes apply automatically
          </p>
        </div>
        <div style={{ display: 'flex', gap: 'var(--space-3)', alignItems: 'center', flexWrap: 'wrap' }}>
          {Object.keys(edits).length > 0 && (
            <span style={{ fontSize: '0.875rem', color: 'var(--status-warning)' }}>
              {Object.keys(edits).length} unsaved change{Object.keys(edits).length !== 1 ? 's' : ''}
            </span>
          )}
          {saved && (
            <span style={{ fontSize: '0.875rem', color: 'var(--status-success)' }}>
              ✓ Saved & applied to live scanner
            </span>
          )}
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving || Object.keys(edits).length === 0}
          >
            {saving ? (
              <>
                <span className="spinner" style={{ width: '14px', height: '14px' }} /> Saving…
              </>
            ) : (
              '💾 Save Changes'
            )}
          </button>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: 'var(--space-3)',
            background: 'var(--status-danger-bg)',
            borderRadius: 'var(--radius-lg)',
            color: 'var(--status-danger)',
            marginBottom: 'var(--space-5)',
          }}
        >
          {error}
        </div>
      )}

      {/* Quick Plain-English Settings Guide */}
      <div
        className="glass-panel"
        style={{
          padding: 'var(--space-4)',
          marginBottom: 'var(--space-4)',
          border: '1px solid rgba(99, 102, 241, 0.28)',
          background: 'rgba(99, 102, 241, 0.06)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' }} onClick={() => setShowGuide((v) => !v)}>
          <strong style={{ fontSize: '0.95rem', color: 'var(--text-primary)' }}>
            📖 Quick Guide: How Value Detection & Settings Work
          </strong>
          <span style={{ fontSize: '0.8rem', color: 'var(--brand-accent)' }}>
            {showGuide ? 'Hide ▲' : 'Show Explanation ▼'}
          </span>
        </div>
        {showGuide && (
          <div style={{ marginTop: '10px', fontSize: '0.84rem', color: 'var(--text-secondary)', lineHeight: 1.55, display: 'grid', gap: '8px' }}>
            <div>
              • <strong>How Value (Edge %) is calculated:</strong> The bot removes bookmaker margin from sharp exchanges (Betfair Exchange / Pinnacle) to find the true <em>Fair Win Probability</em>, then checks if Stoiximan or soft bookmakers pay higher odds than fair value: <code>Edge = (Fair Prob × Target Odds) − 1</code>.
            </div>
            <div>
              • <strong>Why high Edge Threshold + Edge Buffer blocks most games:</strong> When <em>Enable Safety Edge Buffer</em> is ON, the scanner adds ~1.5%–2.2% safety margin for slippage and unknown exchange depth. So a <strong>5% Edge Threshold</strong> actually demanded <strong>~7.2% edge</strong> (very rare in pre-match football!). Setting <strong>Pre-Match Edge Threshold = 2.0%</strong> and <strong>Min Win Probability = 15%</strong> allows genuine value bets across leagues.
            </div>
            <div>
              • <strong>1.5% Re-Read Rule:</strong> Once a bet (same Match + Market + Selection) is recorded, it will <strong>not</strong> be re-read or duplicated on future scans unless its value changes by at least <strong>1.5%</strong> (configurable via <em>Re-Read Minimum Value Change</em>).
            </div>
            <div>
              • <strong>Automatic Score & Won/Lost Settlement:</strong> Official final scores are fetched automatically from The Odds API Scores feed and settle 1X2, Over/Under, and Handicap bets into Won / Lost / Void without manual buttons.
            </div>
          </div>
        )}
      </div>

      <div className="settings-groups">
        {/* The Odds API Key Management Section */}
        <div
          className="glass-panel settings-group"
          style={{
            borderColor: isQuotaExhausted ? 'rgba(239, 68, 68, 0.45)' : 'rgba(99, 102, 241, 0.3)',
            marginBottom: 'var(--space-4)',
          }}
        >
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 'var(--space-3)',
              flexWrap: 'wrap',
              gap: '8px',
            }}
          >
            <div className="section-title" style={{ margin: 0 }}>
              🔑 The Odds API Key & Live Quota
            </div>
            {oddsApiStatus && (
              <span
                className="pill"
                style={{
                  background: isOddsKeyHealthy ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)',
                  color: isOddsKeyHealthy ? '#34d399' : '#f87171',
                  fontWeight: 600,
                  fontSize: '0.78rem',
                  padding: '4px 10px',
                }}
              >
                {isQuotaExhausted
                  ? '● Quota Exhausted (0 Credits Left)'
                  : isOddsKeyHealthy
                    ? '● Active & Live'
                    : '● Invalid / Expired'}
              </span>
            )}
          </div>

          <div style={{ fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: 'var(--space-4)' }}>
            Powers real-time fixtures, bookmaker odds (1X2, Over/Under, Handicap), and official match scores for automatic Won/Lost settlement.
          </div>

          {oddsApiStatus && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 'var(--space-3)',
                background: 'rgba(0, 0, 0, 0.25)',
                padding: 'var(--space-3)',
                borderRadius: 'var(--radius-md)',
                marginBottom: 'var(--space-4)',
              }}
            >
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                  Current Key
                </div>
                <div style={{ fontSize: '1rem', fontWeight: 600, fontFamily: 'monospace', color: 'var(--brand-accent)' }}>
                  {oddsApiStatus.masked_key || 'Not configured'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                  Requests Remaining
                </div>
                <div
                  style={{
                    fontSize: '1.2rem',
                    fontWeight: 700,
                    color: isQuotaExhausted ? '#f87171' : '#34d399',
                  }}
                >
                  {oddsApiStatus.requests_remaining !== null ? `${oddsApiStatus.requests_remaining}` : '—'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>
                  Requests Used
                </div>
                <div style={{ fontSize: '1.2rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                  {oddsApiStatus.requests_used !== null ? `${oddsApiStatus.requests_used}` : '—'}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase' }}>Status</div>
                <div
                  style={{
                    fontSize: '0.85rem',
                    fontWeight: 600,
                    color: isOddsKeyHealthy ? '#34d399' : '#f87171',
                  }}
                >
                  {oddsApiStatus.message}
                </div>
              </div>
            </div>
          )}

          {oddsApiFeedback && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 'var(--radius-md)',
                marginBottom: 'var(--space-4)',
                background: oddsApiFeedback.type === 'success' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                color: oddsApiFeedback.type === 'success' ? '#34d399' : '#f87171',
                border:
                  oddsApiFeedback.type === 'success'
                    ? '1px solid rgba(16, 185, 129, 0.3)'
                    : '1px solid rgba(239, 68, 68, 0.3)',
                fontSize: '0.875rem',
              }}
            >
              {oddsApiFeedback.msg}
            </div>
          )}

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

        {/* Configuration Groups */}
        {GROUPS.map((group) => (
          <div key={group.title} className="glass-panel settings-group">
            <div className="section-title" style={{ marginBottom: '4px' }}>
              {group.title}
            </div>
            {group.subtitle && (
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: 'var(--space-3)' }}>
                {group.subtitle}
              </div>
            )}
            <div className="config-fields">{group.fields.map(renderField)}</div>
          </div>
        ))}

        {/* Database Maintenance: Clean Fake Demo Data */}
        <div className="glass-panel settings-group">
          <div className="section-title">🧹 Database Cleanup (Fake Demo Matches)</div>
          <div style={{ fontSize: '0.84rem', color: 'var(--text-muted)', marginBottom: '12px' }}>
            Removes synthetic demo rows (such as <em>"Team A vs Team B"</em> and mock test IDs 1001–1005) that were generated while Demo Fallback was enabled, without touching real bookmaker bets.
          </div>
          <div style={{ display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-secondary btn-sm" onClick={handlePurgeDemoData} disabled={purgingDemo}>
              {purgingDemo ? 'Cleaning…' : '🗑 Remove Fake Demo Bets'}
            </button>
            {purgeFeedback && (
              <span style={{ fontSize: '0.84rem', color: 'var(--text-secondary)' }}>{purgeFeedback}</span>
            )}
          </div>
        </div>

        {/* Cookies Section */}
        <div className="glass-panel settings-group">
          <div className="section-title">🍪 Browser Cookies (Stoiximan Session)</div>
          <div className="config-fields">
            <div className="config-field">
              <div className="config-field__header">
                <label className="config-field__label">Upload Cookies JSON</label>
                <span className="config-field__desc">
                  Optional: Only needed for live automated Stoiximan browser bet placement when Paper Trading Mode is turned OFF.
                </span>
              </div>
              <textarea
                value={cookieJson}
                onChange={(e) => setCookieJson(e.target.value)}
                placeholder='[{"domain": ".stoiximan.gr", "name": "...", "value": "..."}]'
                style={{
                  width: '100%',
                  minHeight: '90px',
                  fontFamily: 'monospace',
                  fontSize: '0.8rem',
                  background: 'var(--bg-elevated)',
                  border: 'var(--glass-border)',
                  color: 'var(--text-primary)',
                  padding: '8px',
                  borderRadius: '4px',
                }}
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
                  <span
                    style={{
                      fontSize: '0.85rem',
                      color: cookieStatus.type === 'error' ? 'var(--status-danger)' : 'var(--status-success)',
                    }}
                  >
                    {cookieStatus.msg}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Dry-run warning */}
      {config.placement_dry_run && (
        <div
          className="glass-panel"
          style={{
            padding: 'var(--space-4)',
            marginTop: 'var(--space-5)',
            background: 'rgba(245,158,11,0.08)',
            border: '1px solid rgba(245,158,11,0.2)',
          }}
        >
          <strong style={{ color: 'var(--status-warning)' }}>⚠️ Paper Trading Mode is ON</strong>
          <p style={{ color: 'var(--text-secondary)', marginTop: '4px', fontSize: '0.875rem' }}>
            Bets are recorded in simulation mode and automatically settled as Won/Lost using official final scores. Toggle off "Paper Trading Mode" above when ready for live stake placement.
          </p>
        </div>
      )}
    </div>
  );
};
