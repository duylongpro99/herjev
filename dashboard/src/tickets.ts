import type { TicketColumn, TicketStatus, TicketSummary, Transitions } from "./types";

/** Board column order, left to right: the working columns, then the two terminal ones. */
export const COLUMN_ORDER: TicketStatus[] = ["new", "inprogress", "review", "done", "cancel"];

export const STATUS_LABELS: Record<TicketStatus, string> = {
  new: "Mới",
  inprogress: "Đang xử lý",
  review: "Chờ review",
  done: "Hoàn thành",
  cancel: "Đã huỷ",
};

/** What the move control calls each target, from the card's point of view. */
export function moveVerb(from: TicketStatus, to: TicketStatus): string {
  if (to === "inprogress") return from === "review" ? "Trả lại xử lý" : "Nhận xử lý";
  return { new: "Trả về Mới", review: "Gửi review", done: "Hoàn thành", cancel: "Huỷ ticket" }[to];
}

/** Move targets in board order, which puts cancelling last. */
export function moveTargets(transitions: Transitions | undefined, from: TicketStatus): TicketStatus[] {
  return COLUMN_ORDER.filter((s) => canMove(transitions, from, s));
}

/** Nothing moves out of these by hand; a new occurrence reopens a Done ticket. */
export const TERMINAL: ReadonlySet<TicketStatus> = new Set(["cancel", "done"]);

export function canMove(transitions: Transitions | undefined, from: TicketStatus, to: TicketStatus): boolean {
  return !!transitions?.[from]?.includes(to);
}

/** The server's order inside a column: newest activity first, ties by fingerprint. */
export function byActivity(a: TicketSummary, b: TicketSummary): number {
  return b.last_seen - a.last_seen || (a.fingerprint < b.fingerprint ? 1 : a.fingerprint > b.fingerprint ? -1 : 0);
}

/**
 * The board after moving ticket `fp` to `to`, with both column totals updated.
 * Returns `columns` itself when the ticket is not on the board or the move is not allowed.
 */
export function applyMove(
  columns: TicketColumn[],
  transitions: Transitions | undefined,
  fp: string,
  to: TicketStatus,
): TicketColumn[] {
  const source = columns.find((c) => c.items.some((t) => t.fingerprint === fp));
  const target = columns.find((c) => c.status === to);
  const card = source?.items.find((t) => t.fingerprint === fp);
  if (!source || !target || !card || !canMove(transitions, card.status, to)) return columns;
  const moved = { ...card, status: to };
  return columns.map((c) => {
    if (c === source) return { ...c, total: Math.max(0, c.total - 1), items: c.items.filter((t) => t !== card) };
    if (c === target) return { ...c, total: c.total + 1, items: [...c.items, moved].sort(byActivity) };
    return c;
  });
}

/**
 * Server columns in board order, extended with the tickets loaded through "Tải thêm".
 * `extra` items sit in the column of their own status; a ticket the first pages already
 * hold is taken from there, since the first pages are the freshest.
 */
export function mergeColumns(first: TicketColumn[], extra: TicketSummary[]): TicketColumn[] {
  const onFirst = new Set(first.flatMap((c) => c.items.map((t) => t.fingerprint)));
  return COLUMN_ORDER.map((status) => {
    const col = first.find((c) => c.status === status) ?? { status, total: 0, items: [], next_before: null };
    const more = extra.filter((t) => t.status === status && !onFirst.has(t.fingerprint));
    return more.length ? { ...col, items: [...col.items, ...more].sort(byActivity) } : col;
  });
}
