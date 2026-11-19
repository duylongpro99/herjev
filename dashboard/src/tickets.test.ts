import { describe, expect, it } from "vitest";

import { applyMove, COLUMN_ORDER, mergeColumns, moveTargets, moveVerb, STATUS_LABELS, TERMINAL } from "./tickets";
import type { TicketColumn, TicketStatus, TicketSummary, Transitions } from "./types";

const TRANSITIONS: Transitions = {
  new: ["inprogress", "cancel", "review", "done"],
  inprogress: ["new", "cancel", "review", "done"],
  cancel: [],
  review: ["new", "inprogress", "cancel", "done"],
  done: [],
};

function ticket(fingerprint: string, status: TicketStatus, last_seen: number): TicketSummary {
  return {
    fingerprint,
    status,
    last_seen,
    updated_at: null,
    reopened_at: null,
    title: fingerprint,
    project: "p",
    service: "s",
    latest_id: `${fingerprint}-1`,
    incidents: 1,
    occurrences: 1,
    first_seen: last_seen,
    latest_outcome: "notified",
    max_level: "error",
  };
}

function board(cards: TicketSummary[]): TicketColumn[] {
  return COLUMN_ORDER.map((status) => {
    const items = cards.filter((t) => t.status === status);
    return { status, total: items.length, items, next_before: null };
  });
}

const col = (cols: TicketColumn[], status: TicketStatus) => cols.find((c) => c.status === status)!;

describe("ticket columns", () => {
  it("are ordered New, In progress, Review, Done, Cancel with Vietnamese labels", () => {
    expect(COLUMN_ORDER).toEqual(["new", "inprogress", "review", "done", "cancel"]);
    expect(COLUMN_ORDER.map((s) => STATUS_LABELS[s])).toEqual(["Mới", "Đang xử lý", "Chờ review", "Hoàn thành", "Đã huỷ"]);
    expect([...TERMINAL].sort()).toEqual(["cancel", "done"]);
  });
});

describe("applyMove", () => {
  const cols = board([ticket("a", "new", 30), ticket("b", "new", 10), ticket("c", "review", 20)]);

  it("moves the card into the target column in activity order and updates both totals", () => {
    const next = applyMove(cols, TRANSITIONS, "a", "review");
    expect(col(next, "new").items.map((t) => t.fingerprint)).toEqual(["b"]);
    expect(col(next, "new").total).toBe(1);
    expect(col(next, "review").items.map((t) => [t.fingerprint, t.status])).toEqual([
      ["a", "review"],
      ["c", "review"],
    ]);
    expect(col(next, "review").total).toBe(2);
    expect(col(next, "done")).toBe(col(cols, "done"));
  });

  it("is a no-op for a move the server does not allow", () => {
    const closed = board([ticket("d", "done", 5)]);
    expect(applyMove(closed, TRANSITIONS, "d", "new")).toBe(closed);
    expect(applyMove(cols, TRANSITIONS, "a", "new")).toBe(cols);
  });

  it("is a no-op for a ticket that is not on the board", () => {
    expect(applyMove(cols, TRANSITIONS, "zz", "done")).toBe(cols);
  });
});

describe("mergeColumns", () => {
  it("adds loaded pages to their status column and prefers the first page's copy", () => {
    const first = board([ticket("a", "new", 30), ticket("c", "review", 20)]);
    const merged = mergeColumns(first, [ticket("x", "new", 1), ticket("c", "new", 20)]);
    expect(col(merged, "new").items.map((t) => t.fingerprint)).toEqual(["a", "x"]);
    expect(col(merged, "review").items.map((t) => t.fingerprint)).toEqual(["c"]);
  });
});

describe("move controls", () => {
  it("lists targets in board order with cancelling last", () => {
    expect(moveTargets(TRANSITIONS, "inprogress")).toEqual(["new", "review", "done", "cancel"]);
    expect(moveTargets(TRANSITIONS, "done")).toEqual([]);
    expect(moveTargets(undefined, "new")).toEqual([]);
  });

  it("names the move from the card's point of view", () => {
    expect(moveVerb("new", "inprogress")).toBe("Nhận xử lý");
    expect(moveVerb("review", "inprogress")).toBe("Trả lại xử lý");
    expect(moveVerb("inprogress", "cancel")).toBe("Huỷ ticket");
  });
});
