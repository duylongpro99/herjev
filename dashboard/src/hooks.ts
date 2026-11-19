import { useCallback, useEffect, useRef, useState } from "react";

export const POLL_MS = 15_000;

interface Polled<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  /** When data last arrived, in ms since the epoch. */
  updatedAt: number | null;
  reload: () => void;
}

/**
 * Load now, then every `interval` ms while the tab is visible and `paused` is false;
 * reload when the tab becomes visible again. One request is in flight at a time,
 * so hiding and showing the tab never starts a second polling loop.
 */
export function usePoll<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  interval = POLL_MS,
  paused = false,
): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    setData(null);
    setUpdatedAt(null);
    setLoading(true);
  }, [key]);

  useEffect(() => {
    const ctrl = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight = false;
    const schedule = () => {
      clearTimeout(timer);
      if (interval > 0 && document.visibilityState === "visible") {
        timer = setTimeout(() => (pausedRef.current ? schedule() : run()), interval);
      }
    };
    const run = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const value = await loadRef.current(ctrl.signal);
        if (!ctrl.signal.aborted) {
          setData(value);
          setError(null);
          setUpdatedAt(Date.now());
        }
      } catch (e) {
        if (!ctrl.signal.aborted) setError(e as Error);
      } finally {
        inFlight = false;
        if (!ctrl.signal.aborted) {
          setLoading(false);
          schedule();
        }
      }
    };
    const onVisible = () => {
      clearTimeout(timer);
      if (document.visibilityState === "visible" && !pausedRef.current) run();
      else if (document.visibilityState === "visible") schedule();
    };
    run();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      ctrl.abort();
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [key, tick, interval]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, updatedAt, reload };
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
