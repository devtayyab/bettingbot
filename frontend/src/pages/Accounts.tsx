import React, { useState, useEffect, useCallback, useMemo } from 'react';
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
  won_bets?: number;
  lost_bets?: number;
  pending_bets?: number;
  net_profit: number;
  roi: number;
}

interface ActivityBet {
  signal_id: number;
  bet_id: number | null;
  event_name?: string | null;
  selection: string;
  sport: string;
  market_type: string;
  is_live: boolean;
  edge: number;
  allocated_stake: number;
  placed_odds: number | null;
  outcome: string;
  final_score?: string | null;
  profit_share: number;
  event_start_time: string | null;
  identified_at: string | null;
  placed_at: string | null;
}

interface AccountActivity {
  account_id: number;
  name: string;
  initial_deposit: number;
  percentage_share: number;
  is_paused: boolean;
  total_bets: number;
  won_bets?: number;
  lost_bets?: number;
  pending_bets?: number;
  net_profit: number;
  roi: number;
  bets: ActivityBet[];
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
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  // Filtering & Pagination for Bet Activity
  const [outcomeFilter, setOutcomeFilter] = useState<'all' | 'won' | 'lost' | 'pending'>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState<number | 'all'>(25);

  const fetchAccounts = useCallback(async () => {
    try {
      const res = await fetch(`${API}/accounts`);
      if (res.ok) {
        const data = await res.json();
        setAccounts(data);
      }
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchActivity = useCallback(async (id: number) => {
    try {
      const [aRes, nRes] = await Promise.all([
        fetch(`${API}/accounts/${id}/activity`),
        fetch(`${API}/accounts/${id}/notes`),
      ]);
      if (aRes.ok) setActivity(await aRes.json());
      if (nRes.ok) setNotes(await nRes.json());
    } catch {
      // ignore
    }
  }, []);

  // Initial fetch and automatic 15s real-time polling
  useEffect(() => {
    fetchAccounts();
    const interval = setInterval(() => {
      fetchAccounts();
      if (selectedId != null) {
        fetchActivity(selectedId);
      }
    }, 15_000);
    return () => clearInterval(interval);
  }, [fetchAccounts, selectedId, fetchActivity]);

  // Auto-select first account if none selected
  useEffect(() => {
    if (accounts.length > 0 && selectedId == null) {
      setSelectedId(accounts[0].id);
    }
  }, [accounts, selectedId]);

  useEffect(() => {
    if (selectedId != null) {
      fetchActivity(selectedId);
      setCurrentPage(1);
    }
  }, [selectedId, fetchActivity]);

  const handleAutoSettle = async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const res = await fetch(`${API}/bets/settle-all`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setSyncMsg(data.message || `Settled ${data.settled ?? 0} matches.`);
        await fetchAccounts();
        if (selectedId != null) await fetchActivity(selectedId);
      } else {
        setSyncMsg('Failed to settle bets.');
      }
    } catch {
      setSyncMsg('Network error while settling.');
    } finally {
      setSyncing(false);
    }
  };

  const togglePause = async (acc: Account) => {
    const res = await fetch(`${API}/accounts/${acc.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_paused: !acc.is_paused }),
    });
    if (res.ok) {
      fetchAccounts();
      if (selectedId === acc.id) fetchActivity(acc.id);
    }
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

  // Counts breakdown for Bet Activity
  const allBets = activity?.bets || [];
  const wonCount = useMemo(() => allBets.filter((b) => b.outcome === 'won').length, [allBets]);
  const lostCount = useMemo(() => allBets.filter((b) => b.outcome === 'lost').length, [allBets]);
  const pendingCount = useMemo(() => allBets.filter((b) => b.outcome === 'pending').length, [allBets]);

  // Filtered bets
  const filteredBets = useMemo(() => {
    return allBets.filter((b) => {
      if (outcomeFilter === 'won') return b.outcome === 'won';
      if (outcomeFilter === 'lost') return b.outcome === 'lost';
      if (outcomeFilter === 'pending') return b.outcome === 'pending';
      return true;
    });
  }, [allBets, outcomeFilter]);

  // Pagination calculations
  const totalItems = filteredBets.length;
  const totalPages = pageSize === 'all' ? 1 : Math.max(1, Math.ceil(totalItems / Number(pageSize)));
  const paginatedBets = useMemo(() => {
    if (pageSize === 'all') return filteredBets;
    const start = (currentPage - 1) * Number(pageSize);
    return filteredBets.slice(start, start + Number(pageSize));
  }, [filteredBets, currentPage, pageSize]);

  return (
    <div className="accounts-page">
      <div className="page-header">
        <div>
          <h1 className="page-title text-gradient">Accounts &amp; Player Allocations</h1>
          <p className="page-subtitle">Real-time portfolio tracking, allocated stakes, won/lost settlement &amp; net P&amp;L shares</p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button className="btn btn-primary btn-sm" onClick={handleAutoSettle} disabled={syncing}>
            {syncing ? '⏳ Settling Bets…' : '⚡ Settle Finished Bets'}
          </button>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => {
              fetchAccounts();
              if (selectedId != null) fetchActivity(selectedId);
            }}
          >
            🔄 Refresh
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => setModal('create')}>
            + Add Account
          </button>
        </div>
      </div>

      {syncMsg && (
        <div
          className="glass-panel"
          style={{
            padding: '10px 14px',
            marginBottom: 'var(--space-4)',
            background: 'rgba(56, 189, 248, 0.12)',
            border: '1px solid rgba(56, 189, 248, 0.3)',
            color: '#7dd3fc',
            fontSize: '0.85rem',
          }}
        >
          ℹ️ {syncMsg}
        </div>
      )}

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
                {
                  label: 'Net Realized Profit',
                  value: activity ? `${activity.net_profit >= 0 ? '+' : ''}€${activity.net_profit.toFixed(2)}` : '—',
                  cls: activity ? (activity.net_profit >= 0 ? 'positive' : 'negative') : '',
                  subtext: `${wonCount} Won / ${lostCount} Lost (${pendingCount} pending)`,
                  icon: '📈',
                },
                { label: 'Portfolio ROI', value: activity ? `${(activity.roi * 100).toFixed(2)}%` : '—', icon: '🎯' },
                { label: 'Allocated Bets', value: activity?.total_bets ?? '—', icon: '📑' },
                { label: 'Portfolio Share', value: selected ? `${(selected.percentage_share * 100).toFixed(0)}%` : '—', icon: '📊' },
              ].map((stat, i) => (
                <div key={i} className="account-stat glass-panel-solid">
                  <span className="metric-label">{stat.icon} {stat.label}</span>
                  <span className={`metric-value ${stat.cls ?? ''}`} style={{ fontSize: '1.4rem' }}>
                    {stat.value}
                  </span>
                  {stat.subtext && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                      {stat.subtext}
                    </span>
                  )}
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

            {/* Activity / Bets Table with Filtering & Pagination */}
            <div className="glass-panel" style={{ padding: 'var(--space-5)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: 'var(--space-4)' }}>
                <div>
                  <div className="section-title" style={{ marginBottom: '2px' }}>
                    📋 Bet Activity ({totalItems})
                  </div>
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                    Real-time updates every 15s • Sorted newest first
                  </div>
                </div>

                {/* Outcome Filter Buttons */}
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
                  <button
                    className={`btn btn-sm ${outcomeFilter === 'all' ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => { setOutcomeFilter('all'); setCurrentPage(1); }}
                  >
                    All ({allBets.length})
                  </button>
                  <button
                    className={`btn btn-sm ${outcomeFilter === 'won' ? 'btn-success' : 'btn-ghost'}`}
                    style={{ color: outcomeFilter === 'won' ? '#fff' : '#10b981' }}
                    onClick={() => { setOutcomeFilter('won'); setCurrentPage(1); }}
                  >
                    🏆 Won ({wonCount})
                  </button>
                  <button
                    className={`btn btn-sm ${outcomeFilter === 'lost' ? 'btn-danger' : 'btn-ghost'}`}
                    style={{ color: outcomeFilter === 'lost' ? '#fff' : '#ef4444' }}
                    onClick={() => { setOutcomeFilter('lost'); setCurrentPage(1); }}
                  >
                    ❌ Lost ({lostCount})
                  </button>
                  <button
                    className={`btn btn-sm ${outcomeFilter === 'pending' ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => { setOutcomeFilter('pending'); setCurrentPage(1); }}
                  >
                    ⏳ Pending ({pendingCount})
                  </button>
                </div>
              </div>

              {!filteredBets.length ? (
                <div className="empty-state" style={{ padding: 'var(--space-6)' }}>
                  No bets found for this filter.
                </div>
              ) : (
                <>
                  <div style={{ overflowX: 'auto' }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Selection / Fixture</th>
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
                        {paginatedBets.map((b, i) => {
                          const isWon = b.outcome === 'won';
                          const isLost = b.outcome === 'lost';
                          const isPending = b.outcome === 'pending';
                          return (
                            <tr key={b.signal_id || i}>
                              <td style={{ maxWidth: '240px' }}>
                                <div style={{ fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                  {b.selection}
                                </div>
                                {b.event_name && (
                                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    🏟 {b.event_name}
                                  </div>
                                )}
                              </td>
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
                              <td style={{ fontWeight: 600 }}>€{b.allocated_stake.toFixed(2)}</td>
                              <td style={{ fontWeight: 600 }}>{b.placed_odds?.toFixed(2) ?? '—'}</td>
                              <td>
                                <span
                                  className="pill"
                                  style={{
                                    background: isWon
                                      ? 'rgba(16, 185, 129, 0.2)'
                                      : isLost
                                        ? 'rgba(239, 68, 68, 0.2)'
                                        : 'rgba(245, 158, 11, 0.16)',
                                    color: isWon ? '#34d399' : isLost ? '#f87171' : '#fbbf24',
                                    fontWeight: 700,
                                    padding: '3px 8px',
                                    borderRadius: '6px',
                                    fontSize: '0.72rem',
                                    display: 'inline-block',
                                  }}
                                >
                                  {isWon ? '✓ WON' : isLost ? '✗ LOST' : isPending ? '⏳ PENDING' : b.outcome.toUpperCase()}
                                </span>
                              </td>
                              <td style={{ fontWeight: 700 }} className={b.profit_share > 0 ? 'positive' : b.profit_share < 0 ? 'negative' : ''}>
                                {b.profit_share > 0 ? `+€${b.profit_share.toFixed(2)}` : b.profit_share < 0 ? `-€${Math.abs(b.profit_share).toFixed(2)}` : '€0.00'}
                              </td>
                              <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                                {b.identified_at ? new Date(b.identified_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}
                              </td>
                              <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                                {b.placed_at ? new Date(b.placed_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* Pagination Toolbar */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 'var(--space-4)', flexWrap: 'wrap', gap: '10px' }}>
                    <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                      Showing {pageSize === 'all' ? 1 : (currentPage - 1) * Number(pageSize) + 1}–{pageSize === 'all' ? totalItems : Math.min(currentPage * Number(pageSize), totalItems)} of {totalItems} bets
                    </div>

                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>Per page:</span>
                      <select
                        value={pageSize}
                        onChange={(e) => {
                          const val = e.target.value === 'all' ? 'all' : Number(e.target.value);
                          setPageSize(val);
                          setCurrentPage(1);
                        }}
                        style={{ padding: '4px 8px', borderRadius: 'var(--radius-sm)', background: 'var(--bg-elevated)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', fontSize: '0.82rem' }}
                      >
                        <option value={25}>25</option>
                        <option value={50}>50</option>
                        <option value={100}>100</option>
                        <option value="all">All</option>
                      </select>

                      {pageSize !== 'all' && totalPages > 1 && (
                        <div style={{ display: 'flex', gap: '4px', marginLeft: '8px' }}>
                          <button
                            className="btn btn-ghost btn-sm"
                            disabled={currentPage === 1}
                            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                            style={{ padding: '4px 8px', fontSize: '0.78rem' }}
                          >
                            ◀ Prev
                          </button>
                          <span style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', padding: '0 6px', display: 'flex', alignItems: 'center' }}>
                            Page {currentPage} of {totalPages}
                          </span>
                          <button
                            className="btn btn-ghost btn-sm"
                            disabled={currentPage === totalPages}
                            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                            style={{ padding: '4px 8px', fontSize: '0.78rem' }}
                          >
                            Next ▶
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </>
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
