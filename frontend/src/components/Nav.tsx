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
    <nav className="ge-nav" aria-label="Main navigation">
      <ul className="ge-nav-list">
        {NAV_ITEMS.map((item) => (
          <li key={item.id}>
            <button
              className={`ge-nav-item ${active === item.id ? 'active' : ''}`}
              onClick={() => onChange(item.id)}
            >
              <span className="ge-nav-icon">{item.icon}</span>
              <span>{item.label}</span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
};
