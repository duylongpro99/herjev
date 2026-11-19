import { describe, expect, it } from "vitest";

import { groupIntoLanes } from "./lanes";
import { COLUMN_ORDER } from "./tickets";
import type { Level, TicketColumn, TicketStatus, TicketSummary } from "./types";

function ticket(fp: string, status: TicketStatus, project: string, service: string, max_level: Level): TicketSummary {
  return {
    fingerprint: fp, status, project, service, max_level,
    updated_at: null, reopened_at: null, title: fp, latest_id: `${fp}-1`,
    incidents: 1, occurrences: 1, first_seen: 0, last_seen: 0, latest_outcome: "notified",
  };
}

function board(cards: TicketSummary[]): TicketColumn[] {
  return COLUMN_ORDER.map((status) => {
    const items = cards.filter((t) => t.status === status);
    return { status, total: items.length, items, next_before: null };
  });
}

const CARDS = [
  ticket("a", "new", "shop", "api", "warn"),
  ticket("b", "inprogress", "shop", "api", "error"),
  ticket("c", "new", "pay", "gateway", "fatal"),
  ticket("d", "done", "pay", "gateway", "panic"),
  ticket("e", "new", "blog", "", "error"),
  ticket("f", "cancel", "zzz", "cron", "panic"),
];

describe("groupIntoLanes", () => {
  it("groups by project and counts open and closed tickets per lane", () => {
    const { lanes } = groupIntoLanes(board(CARDS), "project");
    const pay = lanes.find((l) => l.key === "pay")!;
    expect(pay.open).toBe(1);
    expect(pay.closed).toBe(1);
    expect(pay.worst).toBe("fatal");
    expect(pay.cells.new.map((t) => t.fingerprint)).toEqual(["c"]);
    expect(pay.cells.done.map((t) => t.fingerprint)).toEqual(["d"]);
  });

  it("orders lanes by worst open level, then open count, then name", () => {
    const { lanes } = groupIntoLanes(board(CARDS), "project");
    // A closed panic does not count: zzz has nothing open.
    expect(lanes.map((l) => l.key)).toEqual(["pay", "shop", "blog", "zzz"]);
  });

  it("keys service lanes by project and service, with a dash for no service", () => {
    const { lanes } = groupIntoLanes(board(CARDS), "service");
    expect(lanes.map((l) => l.key)).toEqual(["pay/gateway", "shop/api", "blog/—", "zzz/cron"]);
    expect(lanes.find((l) => l.key === "blog/—")!.name).toBe("—");
    expect(lanes.find((l) => l.key === "shop/api")!.sub).toBe("shop");
  });

  it("orders level lanes by severity", () => {
    const { lanes } = groupIntoLanes(board(CARDS), "level");
    expect(lanes.map((l) => l.key)).toEqual(["panic", "fatal", "error", "warn"]);
  });

  it("hides lanes with nothing open and counts them", () => {
    const { lanes, hidden } = groupIntoLanes(board(CARDS), "project", { hideEmpty: true });
    expect(lanes.map((l) => l.key)).toEqual(["pay", "shop", "blog"]);
    expect(hidden).toBe(1);
  });

  it("lists the worst ticket first inside a cell, then the most recent", () => {
    const cards = [
      { ...ticket("old-warn", "new", "shop", "api", "warn"), last_seen: 10 },
      { ...ticket("new-warn", "new", "shop", "api", "warn"), last_seen: 20 },
      { ...ticket("fatal", "new", "shop", "api", "fatal"), last_seen: 1 },
    ];
    const [lane] = groupIntoLanes(board(cards), "project").lanes;
    expect(lane.cells.new.map((t) => t.fingerprint)).toEqual(["fatal", "new-warn", "old-warn"]);
  });
});
