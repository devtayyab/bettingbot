import React, { useState, useEffect, useCallback } from 'react';
import { AccountModal } from '../components/AccountModal';

const API = '/api';

interface Account {
  id: number;
  name: string;
  bookmaker: string;
  percentage_share: number;
  initial_deposit: number;
  is_paused: boolean;
  created_at: string;
  total_bets: number;
  net_profit: number;
  roi: number;
}

interface AccountActivity {
  account_id: number;
  name: string;
  initial_deposit: number;
  percentage_share: number;
  is_paused: boolean;
  total_bets: number;
  net_profit: number;
  roi: number;
  bets: {
    signal_id: number;
    bet_id: number | null;
    selection: string;
    sport: string;
    market_type: string;
    is_live: boolean;
    edge: number;
    allocated_stake: number;
    placed_odds: number | null;
    outcome: string;
    profit_share: number;
    identified_at: string | null;
    placed_at: string | null;
  }[];
}

interface Note {
  id: number;
  content: string;
  created_at: string;
}

export const Accounts: React.FC = () => {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activity, setActivity] = useState<AccountActivity | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [newNote, setNewNote] = useState('');
  const [modal, setModal] = useState<'create' | 'edit' | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAccounts = useCallback(async () => {
    const res = await fetch(`${API}/accounts`);
    if (res.ok) setAccounts(await res.json());
    setLoading(false);
  }, []);

  const fetchActivity = useCallback(async (id: number) => {
    const [aRes, nRes] = await Promise.all([
      fetch(`${API}/accounts/${id}/activity`),
      fetch(`${API}/accounts/${id}/notes`),
    ]);
    if (aRes.ok) setActivity(await aRes.json());
    if (nRes.ok) setNotes(await nRes.json());
  }, []);

  useEffect(() => { fetchAccounts(); }, [fetchAccounts]);

  useEffect(() => {
    if (selectedId != null) fetchActivity(selectedId);
  }, [selectedId, fetchActivity]);

  const togglePause = async (acc: Account) => {
    const res = await fetch(`${API}/accounts/${acc.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_paused: !acc.is_paused }),
    });
    if (res.ok) fetchAccounts();
  };

  const addNote = async () => {
    if (!newNote.trim() || selectedId == null) return;
    const res = await fetch(`${API}/accounts/${selectedId}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: newNote.trim() }),
    });
    if (res.ok) {
      setNewNote('');
      fetchActivity(selectedId);
    }
  };

  const deleteNote = async (noteId: number) => {
    if (!confirm('Delete this note?')) return;
    const res = await fetch(`${API}/accounts/${selectedId}/notes/${noteId}`, { method: 'DELETE' });
    if (res.ok && selectedId) fetchActivity(selectedId);
  };

  const selected = accounts.find((a) => a.id === selectedId);

  return (
    <div className="accounts-page">
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Accounts</h1>
          <p className="page-subtitle">Manage customer accounts, allocations, and activity</p>
        </div>
        <button className="btn btn-primary" onClick={() => setModal('create')}>
          + Add Account
        </button>
      </div>

      <div className="accounts-layout">
        {/* Left: Account List */}
        <div className="accounts-list glass-panel">
          <div className="section-title">Customers ({accounts.length})</div>

          {loading ? (
            <div className="empty-state"><span className="spinner" /> Loading…</div>
          ) : accounts.length === 0 ? (
            <div className="empty-state" style={{ padding: 'var(--space-6)' }}>
              No accounts yet.<br />
              <button className="btn btn-primary btn-sm" style={{ marginTop: '12px' }} onClick={() => setModal('create')}>
                + Add first account
              </button>
            </div>
          ) : accounts.map((acc) => (
            <div
              key={acc.id}
              className={`account-item ${selectedId === acc.id ? 'account-item--active' : ''} ${acc.is_paused ? 'account-item--paused' : ''}`}
              onClick={() => setSelectedId(acc.id)}
            >
              <div className="account-item__header">
                <div className="account-item__name">
                  {acc.name}
                  {acc.is_paused && <span className="pill" style={{ background: 'rgba(100,100,100,0.12)', color: '#888', fontSize: '0.65rem', marginLeft: '8px' }}>PAUSED</span>}
                </div>
                <button
                  className={`btn btn-sm ${acc.is_paused ? 'btn-success' : 'btn-warning'}`}
                  onClick={(e) => { e.stopPropagation(); togglePause(acc); }}
                  title={acc.is_paused ? 'Resume account' : 'Pause account'}
                >
                  {acc.is_paused ? '▶ Resume' : '⏸ Pause'}
                </button>
              </div>
              <div className="account-item__meta">
                <span>{acc.bookmaker}</span>
                <span>{(acc.percentage_share * 100).toFixed(0)}% share</span>
              </div>
              <div className="account-item__stats">
                <span>{acc.total_bets} bets</span>
                <span className={acc.net_profit >= 0 ? 'positive' : 'negative'}>
                  {acc.net_profit >= 0 ? '+' : ''}€{acc.net_profit.toFixed(2)}
                </span>
                <span className="neutral">ROI {(acc.roi * 100).toFixed(1)}%</span>
              </div>
            </div>
          ))}
        </div>

        {/* Right: Account Detail */}
        {selectedId == null ? (
          <div className="glass-panel empty-state" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            ← Select an account to view details
          </div>
        ) : (
          <div className="account-detail">
            {/* Stats Cards */}
            <div className="account-stat-cards">
              {[
                { label: 'Initial Deposit', value: `€${activity?.initial_deposit.toFixed(2) ?? '—'}`, icon: '💰' },
                { label: 'Net Profit / Loss', value: activity ? `${activity.net_profit >= 0 ? '+' : ''}€${activity.net_profit.toFixed(2)}` : '—', cls: activity ? (activity.net_profit >= 0 ? 'positive' : 'negative') : '' },
                { label: 'ROI', value: activity ? `${(activity.roi * 100).toFixed(2)}%` : '—' },
                { label: 'Bets Placed', value: activity?.total_bets ?? '—' },
                { label: '% Share', value: selected ? `${(selected.percentage_share * 100).toFixed(0)}%` : '—' },
              ].map((stat, i) => (
                <div key={i} className="account-stat glass-panel-solid">
                  <span className="metric-label">{stat.icon} {stat.label}</span>
                  <span className={`metric-value ${stat.cls ?? ''}`} style={{ fontSize: '1.4rem' }}>
                    {stat.value}
                  </span>
                </div>
              ))}
            </div>

            {/* Edit button */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 'var(--space-4)' }}>
              <button className="btn btn-ghost btn-sm" onClick={() => setModal('edit')}>
                ✏️ Edit Account
              </button>
            </div>

            {/* Notes Section */}
            <div className="glass-panel" style={{ padding: 'var(--space-5)', marginBottom: 'var(--space-4)' }}>
              <div className="section-title">📝 Notes</div>
              <div style={{ display: 'flex', gap: 'var(--space-3)', marginBottom: 'var(--space-4)' }}>
                <textarea
                  value={newNote}
                  onChange={(e) => setNewNote(e.target.value)}
                  placeholder="Add a note about this account…"
                  style={{ flex: 1, minHeight: '60px', fontSize: '0.875rem' }}
                />
                <button className="btn btn-primary btn-sm" onClick={addNote}>Add</button>
              </div>

              {notes.length === 0 ? (
                <div className="empty-state" style={{ padding: 'var(--space-4)' }}>No notes yet</div>
              ) : (
                <div className="notes-list">
                  {notes.map((n) => (
                    <div key={n.id} className="note-item">
                      <div className="note-content">{n.content}</div>
                      <div className="note-footer">
                        <span className="note-time">{new Date(n.created_at).toLocaleString()}</span>
                        <button className="btn btn-danger btn-sm" onClick={() => deleteNote(n.id)}>Delete</button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Activity / Bets Table */}
            <div className="glass-panel" style={{ padding: 'var(--space-5)' }}>
              <div className="section-title">📋 Bet Activity</div>
              {!activity?.bets?.length ? (
                <div className="empty-state">No bets recorded for this account</div>
              ) : (
                <div style={{ overflowX: 'auto' }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Selection</th>
                        <th>Sport</th>
                        <th>Type</th>
                        <th>Edge</th>
                        <th>Allocated Stake</th>
                        <th>Odds</th>
                        <th>Outcome</th>
                        <th>P/L Share</th>
                        <th>Identified</th>
                        <th>Placed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activity.bets.map((b, i) => (
                        <tr key={i}>
                          <td style={{ fontWeight: 500, maxWidth: '180px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.selection}</td>
                          <td style={{ color: 'var(--text-secondary)' }}>{b.sport}</td>
                          <td>
                            {b.is_live
                              ? <span className="pill pill-live" style={{ fontSize: '0.65rem' }}>LIVE</span>
                              : <span className="pill pill-prematch" style={{ fontSize: '0.65rem' }}>PRE</span>
                            }
                          </td>
                          <td>
                            <span className={b.edge > 0.08 ? 'edge-high' : b.edge > 0 ? 'edge-medium' : 'edge-low'}>
                              {b.edge >= 0 ? '+' : ''}{(b.edge * 100).toFixed(1)}%
                            </span>
                          </td>
                          <td>€{b.allocated_stake.toFixed(2)}</td>
                          <td>{b.placed_odds?.toFixed(2) ?? '—'}</td>
                          <td>
                            <span className={`pill pill-status-${b.outcome === 'won' ? 'placed' : b.outcome === 'lost' ? 'failed' : 'detected'}`}>
                              {b.outcome}
                            </span>
                          </td>
                          <td className={b.profit_share >= 0 ? 'positive' : 'negative'}>
                            {b.profit_share >= 0 ? '+' : ''}€{b.profit_share.toFixed(2)}
                          </td>
                          <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                            {b.identified_at ? new Date(b.identified_at).toLocaleString() : '—'}
                          </td>
                          <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                            {b.placed_at ? new Date(b.placed_at).toLocaleString() : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {modal === 'create' && (
        <AccountModal
          mode="create"
          onClose={() => setModal(null)}
          onSave={fetchAccounts}
        />
      )}

      {modal === 'edit' && selected && (
        <AccountModal
          mode="edit"
          initial={selected}
          onClose={() => setModal(null)}
          onSave={() => { fetchAccounts(); if (selectedId) fetchActivity(selectedId); }}
        />
      )}
    </div>
  );
};
