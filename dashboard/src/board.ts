import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, type Filters } from "./api";
import { usePoll } from "./hooks";
import { applyMove, canMove, COLUMN_ORDER, mergeColumns, STATUS_LABELS } from "./tickets";
import type { TicketBoard, TicketColumn, TicketStatus, TicketSummary, Transitions } from "./types";

interface Move {
  to: TicketStatus;
  /** The server accepted it; dropped once a board loaded after that arrives. */
  done: boolean;
}

/** A "Tải thêm" page, shown on top of the board it was loaded after until the next poll. */
interface Page {
  base: TicketBoard;
  status: TicketStatus;
  items: TicketSummary[];
  next_before: string | null;
}

export interface BoardState {
  columns: TicketColumn[] | null;
  transitions: Transitions | undefined;
  error: Error | null;
  loading: boolean;
  /** When the board last loaded, in ms since the epoch. */
  updatedAt: number | null;
  reload: () => void;
  /** Whether a "Tải thêm" request is running, per column. */
  loadingMore: Partial<Record<TicketStatus, boolean>>;
  loadMore: (status: TicketStatus) => void;
  move: (ticket: TicketSummary, to: TicketStatus) => Promise<void>;
  /** Tickets whose move the server has not answered yet. */
  pending: ReadonlySet<string>;
  /** Status to show for a ticket, counting moves the server has not confirmed yet. */
  statusOf: (fp: string, fallback: TicketStatus) => TicketStatus;
  toast: string;
  dismissToast: () => void;
}

/**
 * The ticket board for `filters`: polled columns, per-column "Tải thêm" pages and
 * optimistic moves. A moved card stays where the user put it until the server answers;
 * polling results never move it back while the request is pending.
 */
export function useTicketBoard(filters: Filters, filterKey: string): BoardState {
  // How many tickets each expanded column shows. Polling reloads those columns at that
  // size, so the whole board stays current and a page never goes stale between polls.
  const shown = useRef<Partial<Record<TicketStatus, number>>>({});
  const [page, setPage] = useState<Page | null>(null);
  const [loadingMore, setLoadingMore] = useState<Partial<Record<TicketStatus, boolean>>>({});
  const loadingRef = useRef<Partial<Record<TicketStatus, boolean>>>({});
  const [moves, setMoves] = useState<Map<string, Move>>(new Map());
  const [toast, setToast] = useState("");
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const filterKeyRef = useRef(filterKey);
  filterKeyRef.current = filterKey;

  // Declared before the poll so a filter change clears the expansion before it reloads.
  useEffect(() => {
    shown.current = {};
    loadingRef.current = {};
    setPage(null);
    setLoadingMore({});
  }, [filterKey]);

  const load = useCallback(async (signal: AbortSignal): Promise<TicketBoard> => {
    const f = filtersRef.current;
    const board = await api.tickets(f, signal);
    const expanded = COLUMN_ORDER.filter((s) => (shown.current[s] ?? 0) > (board.columns.find((c) => c.status === s)?.items.length ?? 0));
    if (!expanded.length) return board;
    const cols = await Promise.all(expanded.map((s) => api.ticketColumn(f, s, { limit: shown.current[s] }, signal)));
    const fresh = new Map(cols.map((b) => [b.columns[0].status, b.columns[0]]));
    return { ...board, columns: board.columns.map((c) => fresh.get(c.status) ?? c) };
  }, []);
  const board = usePoll(load, filterKey);

  // A board loaded after the server accepted a move already shows it.
  useEffect(() => {
    setMoves((m) => {
      if (![...m.values()].some((x) => x.done)) return m;
      return new Map([...m].filter(([, x]) => !x.done));
    });
  }, [board.data]);

  const transitions = board.data?.transitions;
  const columns = useMemo(() => {
    if (!board.data) return null;
    let cols = board.data.columns;
    if (page?.base === board.data) {
      cols = mergeColumns(cols, page.items).map((c) => (c.status === page.status ? { ...c, next_before: page.next_before } : c));
    }
    for (const [fp, m] of moves) cols = applyMove(cols, transitions, fp, m.to);
    return cols;
  }, [board.data, page, moves, transitions]);

  const loadMore = useCallback(
    async (status: TicketStatus) => {
      const col = columns?.find((c) => c.status === status);
      if (!col?.next_before || !board.data || loadingRef.current[status]) return;
      const base = board.data;
      const requestedFor = filterKey;
      loadingRef.current[status] = true;
      setLoadingMore((l) => ({ ...l, [status]: true }));
      try {
        const next = (await api.ticketColumn(filters, status, { before: col.next_before })).columns[0];
        if (filterKeyRef.current !== requestedFor) return; // filters changed meanwhile
        shown.current[status] = col.items.length + next.items.length;
        const earlier = page?.base === base && page.status === status ? page.items : [];
        setPage({ base, status, items: [...earlier, ...next.items], next_before: next.next_before });
        board.reload(); // a poll already running would come back without this page
      } catch (e) {
        if (filterKeyRef.current === requestedFor) setToast(`Không tải thêm được: ${(e as Error).message}`);
      } finally {
        loadingRef.current[status] = false;
        setLoadingMore((l) => ({ ...l, [status]: false }));
      }
    },
    [columns, board.data, board.reload, filterKey, filters, page],
  );

  const reload = board.reload;
  const move = useCallback(
    async (ticket: TicketSummary, to: TicketStatus) => {
      const fp = ticket.fingerprint;
      if (!canMove(transitions, ticket.status, to) || moves.get(fp)?.done === false) return;
      setMoves((m) => new Map(m).set(fp, { to, done: false }));
      try {
        await api.setTicketStatus(fp, ticket.status, to);
        setMoves((m) => new Map(m).set(fp, { to, done: true }));
      } catch (e) {
        setMoves((m) => {
          const next = new Map(m);
          next.delete(fp);
          return next;
        });
        setToast(`Không chuyển được sang "${STATUS_LABELS[to]}": ${(e as Error).message}`);
      }
      reload();
    },
    [transitions, moves, reload],
  );

  const pending = useMemo(() => new Set([...moves].filter(([, m]) => !m.done).map(([fp]) => fp)), [moves]);
  const statusOf = useCallback(
    (fp: string, fallback: TicketStatus) =>
      moves.get(fp)?.to ?? columns?.find((c) => c.items.some((t) => t.fingerprint === fp))?.status ?? fallback,
    [moves, columns],
  );
  const dismissToast = useCallback(() => setToast(""), []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 8000);
    return () => clearTimeout(t);
  }, [toast]);

  return {
    columns,
    transitions,
    error: board.error,
    loading: board.loading,
    updatedAt: board.updatedAt,
    reload,
    loadingMore,
    loadMore,
    move,
    pending,
    statusOf,
    toast,
    dismissToast,
  };
}
