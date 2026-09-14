import React, { useState } from 'react';
import type { SignalData } from '../hooks/useFeed';

interface EditBetModalProps {
  signal: SignalData;
  onClose: () => void;
  onSave: (updates: Partial<SignalData>) => void;
}

export const EditBetModal: React.FC<EditBetModalProps> = ({ signal, onClose, onSave }) => {
  const [stake, setStake] = useState(signal.recommended_stake.toString());
  const [maxBet, setMaxBet] = useState(signal.max_bet?.toString() ?? '');
  const [isLive, setIsLive] = useState(signal.is_live);
  const [varsComplete, setVarsComplete] = useState(signal.variables_complete);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/signals/${signal.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          recommended_stake: parseFloat(stake) || signal.recommended_stake,
          max_bet: maxBet ? parseFloat(maxBet) : null,
          is_live: isLive,
          variables_complete: varsComplete,
          note: note || null,
        }),
      });
      if (res.ok) {
        onSave({ recommended_stake: parseFloat(stake), max_bet: maxBet ? parseFloat(maxBet) : null, is_live: isLive, variables_complete: varsComplete });
        onClose();
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-box">
        <div className="modal-header">
          <h2>Edit Bet Variables</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div style={{ marginBottom: 'var(--space-4)', padding: 'var(--space-3)', background: 'var(--bg-elevated)', borderRadius: 'var(--radius-lg)', fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--text-primary)' }}>{signal.selection}</strong>
          <span style={{ marginLeft: '8px' }}>{signal.sport} · {signal.market_type}</span>
        </div>

        <div className="form-field">
          <label>Recommended Stake (€)</label>
          <input
            type="number"
            step="0.01"
            value={stake}
            onChange={(e) => setStake(e.target.value)}
            min="0"
          />
        </div>

        <div className="form-field">
          <label>Max Bet Available (€)</label>
          <input
            type="number"
            step="0.01"
            value={maxBet}
            onChange={(e) => setMaxBet(e.target.value)}
            placeholder="e.g. 500.00"
            min="0"
          />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--space-4)', marginBottom: 'var(--space-4)' }}>
          <div className="form-field" style={{ marginBottom: 0 }}>
            <label>Bet Type</label>
            <select value={isLive ? 'live' : 'prematch'} onChange={(e) => setIsLive(e.target.value === 'live')}>
              <option value="prematch">Pre-Market</option>
              <option value="live">Live Bet</option>
            </select>
          </div>

          <div className="form-field" style={{ marginBottom: 0 }}>
            <label>Variables Complete</label>
            <select value={varsComplete ? 'yes' : 'no'} onChange={(e) => setVarsComplete(e.target.value === 'yes')}>
              <option value="yes">✅ All Available</option>
              <option value="no">⚠️ Missing Data</option>
            </select>
          </div>
        </div>

        <div className="form-field">
          <label>Internal Note (optional)</label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Add a note about this bet…"
          />
        </div>

        <div className="form-actions">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <><span className="spinner" style={{ width: '12px', height: '12px' }} /> Saving…</> : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  );
};
