import React, { useState } from 'react';

interface AccountModalProps {
  mode: 'create' | 'edit';
  initial?: {
    id?: number;
    name?: string;
    bookmaker?: string;
    percentage_share?: number;
    initial_deposit?: number;
    is_paused?: boolean;
  };
  onClose: () => void;
  onSave: () => void;
}

export const AccountModal: React.FC<AccountModalProps> = ({ mode, initial, onClose, onSave }) => {
  const [name, setName] = useState(initial?.name ?? '');
  const [bookmaker, setBookmaker] = useState(initial?.bookmaker ?? 'stoiximan');
  const [pctShare, setPctShare] = useState(((initial?.percentage_share ?? 1) * 100).toString());
  const [deposit, setDeposit] = useState((initial?.initial_deposit ?? 0).toString());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSave = async () => {
    if (!name.trim()) { setError('Name is required'); return; }
    const pct = parseFloat(pctShare) / 100;
    if (isNaN(pct) || pct < 0 || pct > 1) { setError('Percentage must be 0–100'); return; }

    setSaving(true);
    setError('');
    try {
      const url = mode === 'create' ? '/api/accounts' : `/api/accounts/${initial?.id}`;
      const method = mode === 'create' ? 'POST' : 'PATCH';
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          bookmaker,
          percentage_share: pct,
          initial_deposit: parseFloat(deposit) || 0,
        }),
      });
      if (res.ok) {
        onSave();
        onClose();
      } else {
        const err = await res.json();
        setError(err.detail || 'Failed to save');
      }
    } catch {
      setError('Network error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-box">
        <div className="modal-header">
          <h2>{mode === 'create' ? 'Add New Account' : 'Edit Account'}</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        {error && (
          <div style={{ marginBottom: 'var(--space-4)', padding: 'var(--space-3)', background: 'var(--status-danger-bg)', borderRadius: 'var(--radius-md)', color: 'var(--status-danger)', fontSize: '0.875rem' }}>
            {error}
          </div>
        )}

        <div className="form-field">
          <label>Account Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. John Smith"
            autoFocus
          />
        </div>

        <div className="form-field">
          <label>Bookmaker</label>
          <select value={bookmaker} onChange={(e) => setBookmaker(e.target.value)}>
            <option value="stoiximan">Stoiximan</option>
            <option value="betano">Betano</option>
            <option value="bet365">Bet365</option>
            <option value="unibet">Unibet</option>
            <option value="other">Other</option>
          </select>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)' }}>
          <div className="form-field">
            <label>% Share of Each Bet</label>
            <input
              type="number"
              value={pctShare}
              onChange={(e) => setPctShare(e.target.value)}
              min="0"
              max="100"
              step="0.5"
              placeholder="100"
            />
            <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              Stake = Recommended Stake × {(parseFloat(pctShare || '0') / 100).toFixed(2)}
            </span>
          </div>

          <div className="form-field">
            <label>Initial Deposit (€)</label>
            <input
              type="number"
              value={deposit}
              onChange={(e) => setDeposit(e.target.value)}
              min="0"
              step="10"
              placeholder="0.00"
            />
          </div>
        </div>

        <div className="form-actions">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <><span className="spinner" style={{ width: '12px', height: '12px' }} /> Saving…</> : (mode === 'create' ? 'Create Account' : 'Save Changes')}
          </button>
        </div>
      </div>
    </div>
  );
};
