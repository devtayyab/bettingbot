import { useState, useEffect, useRef, useCallback } from 'react';

export interface SignalData {
  id: number;
  event_id: number;
  selection: string;
  sport: string;
  market_type: string;
  fair_prob: number;
  confirm_prob: number | null;
  target_odds: number;
  edge: number;
  recommended_stake: number;
  is_live: boolean;
  max_bet: number | null;
  variables_complete: boolean;
  status: string;
  detected_at: string | null;
  event_start_time: string | null;
}

interface UseFeedReturn {
  signals: SignalData[];
  connected: boolean;
  error: string | null;
  retry: () => void;
}

export function useFeed(): UseFeedReturn {
  const [signals, setSignals] = useState<SignalData[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const retryRef = useRef<number>(0);

  const connect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
    }

    const es = new EventSource('/api/signals/feed');
    esRef.current = es;

    es.onopen = () => {
      setConnected(true);
      setError(null);
      retryRef.current = 0;
    };

    es.onmessage = (event) => {
      try {
        const data: SignalData[] = JSON.parse(event.data);
        // Filter out failed/cancelled bets from active feed
        const active = data.filter(
          (s) => s.status !== 'failed' && s.status !== 'cancelled' && s.status !== 'rejected'
        );
        setSignals(active);
      } catch (e) {
        console.warn('Failed to parse SSE message', e);
      }
    };

    es.addEventListener('error', () => {
      if (es.readyState === EventSource.CLOSED) {
        setConnected(false);
        setError('Connection lost — retrying…');
        // Exponential back-off: 1s, 2s, 4s, max 30s
        const delay = Math.min(1000 * Math.pow(2, retryRef.current), 30_000);
        retryRef.current += 1;
        setTimeout(() => connect(), delay);
      }
    });
  }, []);

  useEffect(() => {
    connect();
    return () => {
      esRef.current?.close();
    };
  }, [connect]);

  return { signals, connected, error, retry: connect };
}
