/** A move whose reply fails is not saved. In its own file: it replaces ``ticketSummary``
 * for every test in the module. */

import { expect, inject, test, vi } from "vitest";
import { client, createDb, post } from "./test-db";

const failing = vi.hoisted(() => ({ on: false }));

vi.mock("./queries", async (original) => {
  const mod = await original<typeof import("./queries")>();
  return {
    ...mod,
    ticketSummary: (...args: Parameters<typeof mod.ticketSummary>) => {
      if (failing.on) throw new Error("interrupted");
      return mod.ticketSummary(...args);
    },
  };
});

test("a move whose reply fails is rolled back", async () => {
  const db = await createDb(inject("template"));
  try {
    await db.sql`insert into herjev.incidents (id, fingerprint, created_at, level, notify, project, outcome)
                 values ('a1', 'fp-a1', ${Date.now() / 1000}, 'error', true, 'shop', 'notified')`;
    const c = client(db.name);
    failing.on = true;
    await expect(c.fetch("/api/tickets/fp-a1/status", post({ from: "new", to: "inprogress" }))).rejects.toThrow("interrupted");
    failing.on = false;
    expect(((await (await c.get("/api/tickets/fp-a1")).json()) as any).status).toBe("new");
    expect(await db.sql`select * from herjev.tickets`).toHaveLength(0);
  } finally {
    await db.drop();
  }
});
