import React from 'react';

interface NavProps {
  active: string;
  onChange: (page: string) => void;
}

const NAV_ITEMS = [
  { id: 'feed', label: 'Live Feed', icon: '⚡' },
  { id: 'results', label: 'Bet Results', icon: '🏆' },
  { id: 'accounts', label: 'Accounts', icon: '👥' },
  { id: 'reports', label: 'Reports', icon: '📊' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
];

export const Nav: React.FC<NavProps> = ({ active, onChange }) => {
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-logo">⚡</span>
        <div>
          <div className="sidebar-title">ValueBet</div>
          <div className="sidebar-sub">Pilot v2</div>
        </div>
      </div>

      <ul className="sidebar-nav">
        {NAV_ITEMS.map((item) => (
          <li key={item.id}>
            <button
              className={`sidebar-item ${active === item.id ? 'active' : ''}`}
              onClick={() => onChange(item.id)}
            >
              <span className="sidebar-icon">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">
        <div className="sidebar-footer-text">ValueBet Pilot</div>
        <div className="sidebar-footer-sub">© 2026</div>
      </div>
    </nav>
  );
};
