/** The portal API against a real Postgres, connected as the portal role. */

import type postgres from "postgres";
import { afterEach, beforeEach, describe, expect, inject, test } from "vitest";
import { TICKET_STATUSES, canTransition } from "./queries";
import { client, createDb, post, type Client, type TestDb } from "./test-db";

// [id, project, service, level, outcome, title, age in hours]
const ROWS: [string, string, string, string, string, string, number][] = [
  ["a1", "shop", "api", "error", "notified", "db connection refused", 1],
  ["a2", "shop", "api", "warn", "not_notified", "slow request", 2],
  ["a3", "shop", "web", "fatal", "notified", "out of memory", 3],
  ["a4", "shop", "web", "warn", "suppressed", "slow request", 4],
  ["b1", "crm", "worker", "panic", "rate_limited", "index out of range", 30],
  ["b2", "crm", "worker", "error", "not_notified", "token is expired", 50],
];
// Rows that are the same error; every other row is its own fingerprint.
const SHARED_FP: Record<string, string> = { a2: "fp-slow", a4: "fp-slow" };
const WEEK = { since: "7d" };
const now = () => Date.now() / 1000;

interface Incident {
  id: string;
  fingerprint?: string;
  project?: string;
  service?: string | null;
  level?: string;
  outcome?: string;
  title?: string;
  created_at?: number;
  count?: number;
}

/** One incident as the watcher records it: the row, its trace steps and a shared blob. */
async function record(sql: postgres.Sql, inc: Incident) {
  const title = inc.title ?? "ERROR db: connection refused to 10.0.0.5:5432";
  const level = inc.level ?? "error";
  const outcome = inc.outcome ?? "not_notified";
  const sample = { message: title, level, source: "api", host: "api-1", service: inc.service ?? null, raw: title };
  await sql`
    insert into herjev.incidents ${sql({
      id: inc.id, fingerprint: inc.fingerprint ?? `fp-${inc.id}`, created_at: inc.created_at ?? now(), level,
      source: "api", host: "api-1", service: inc.service ?? null, title, count: inc.count ?? 3,
      notify: outcome === "notified", urgency: null, category: null, decision_reason: "r", decider: null,
      analysis: null, channels: sql.json(outcome === "notified" ? ["a"] : []), delivery_errors: sql.json([]),
      state: sql.json({ title, samples: [sample], context: [sample] }), project: inc.project ?? "api",
      outcome, decision_raw: sql.json({ answers: { x: 1 } }), duration_ms: 20,
    })}`;
  const t = now();
  await sql`insert into herjev.trace_blobs (key, body) values ('k1', 'static question set') on conflict do nothing`;
  await sql`
    insert into herjev.incident_steps ${sql([
      { incident_id: inc.id, seq: 1, at: t, stage: "aggregate", message: `${level} x1`, body: null, elapsed_ms: null, blob: null },
      { incident_id: inc.id, seq: 2, at: t, stage: "decide:jev", message: "questions", body: null, elapsed_ms: null, blob: "k1" },
      { incident_id: inc.id, seq: 3, at: t, stage: "decide:jev", message: "← HTTP 200", body: '{"answers": {}}', elapsed_ms: 12.5, blob: null },
      { incident_id: inc.id, seq: 4, at: t, stage: "decision", message: "notify=False", body: null, elapsed_ms: null, blob: null },
    ])}`;
}

async function seed(sql: postgres.Sql) {
  const t = now();
  for (const [id, project, service, level, outcome, title, age] of ROWS) {
    await record(sql, { id, project, service, level, outcome, title, fingerprint: SHARED_FP[id], created_at: t - age * 3600 });
  }
}

const setStatus = (sql: postgres.Sql, fp: string, status: string) => sql`
  insert into herjev.tickets (fingerprint, status, updated_at) values (${fp}, ${status}, ${now()})
  on conflict (fingerprint) do update set status = excluded.status`;

const ids = async (resp: Response) => ((await resp.json()) as any).items.map((i: any) => i.id);
const column = (data: any, status: string) => data.columns.find((c: any) => c.status === status);
const fps = (data: any, status: string) => column(data, status).items.map((t: any) => t.fingerprint);
const move = (c: Client, fp: string, from: string, to: string, headers?: Record<string, string>) =>
  c.fetch(`/api/tickets/${fp}/status`, post({ from, to }, headers));

let db: TestDb;
let c: Client;

beforeEach(async () => {
  db = await createDb(inject("template"));
  await seed(db.sql);
  c = client(db.name);
});

afterEach(async () => {
  await db.drop();
});

describe("incidents", () => {
  test("projects group services with max level", async () => {
    const data: any = await (await c.get("/api/projects", { since: "24h" })).json();
    expect(data.map((p: any) => p.project)).toEqual(["shop"]);
    expect([data[0].count, data[0].max_level]).toEqual([4, "fatal"]);
    expect(Object.fromEntries(data[0].services.map((s: any) => [s.service, [s.count, s.max_level]])))
      .toEqual({ api: [2, "error"], web: [2, "fatal"] });
    const all: any = await (await c.get("/api/projects", WEEK)).json();
    expect(Object.fromEntries(all.map((p: any) => [p.project, p.max_level]))).toEqual({ shop: "fatal", crm: "panic" });
  });

  test("incident filters", async () => {
    expect(await ids(await c.get("/api/incidents", WEEK))).toEqual(["a1", "a2", "a3", "a4", "b1", "b2"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, project: "crm" }))).toEqual(["b1", "b2"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, project: "shop", service: "web" }))).toEqual(["a3", "a4"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, level: "error,fatal" }))).toEqual(["a1", "a3", "b2"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, outcome: "suppressed,rate_limited" }))).toEqual(["a4", "b1"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, q: "slow" }))).toEqual(["a2", "a4"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, q: "100%_" }))).toEqual([]);
    expect(await ids(await c.get("/api/incidents", { since: "24h" }))).toEqual(["a1", "a2", "a3", "a4"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, until: String(now() - 10 * 3600) }))).toEqual(["b1", "b2"]);
    const item = ((await (await c.get("/api/incidents", { project: "shop", limit: 1 })).json()) as any).items[0];
    expect([item.outcome, item.notify, item.channels]).toEqual(["notified", true, ["a"]]);
  });

  test("search ignores case and matches %, _ and non-ASCII text literally", async () => {
    await record(db.sql, { id: "u1", title: 'ERROR kết nối cơ sở dữ liệu thất bại "pg"' });
    await record(db.sql, { id: "p1", title: "disk 100%_full" });
    expect(await ids(await c.get("/api/incidents", { ...WEEK, q: "SLOW REQUEST" }))).toEqual(["a2", "a4"]);
    for (const q of ["thất bại", "THẤT BẠI", '"pg"']) {
      expect(await ids(await c.get("/api/incidents", { ...WEEK, q })), q).toEqual(["u1"]);
    }
    expect(await ids(await c.get("/api/incidents", { ...WEEK, q: "100%_" }))).toEqual(["p1"]);
    expect(await ids(await c.get("/api/incidents", { ...WEEK, q: "100_%" }))).toEqual([]);
    // A sample message only in state is found too.
    await db.sql`update herjev.incidents set title = 'other' where id = 'u1'`;
    expect(await ids(await c.get("/api/incidents", { ...WEEK, q: "dữ liệu" }))).toEqual(["u1"]);
  });

  test("service-less incidents have their own filter", async () => {
    await record(db.sql, { id: "n1", project: "shop", service: null });
    await record(db.sql, { id: "n2", project: "shop", service: "" });
    const shop = ((await (await c.get("/api/projects")).json()) as any).find((p: any) => p.project === "shop");
    const blank = shop.services.filter((s: any) => s.service === "");
    expect(blank.map((s: any) => s.count)).toEqual([2]);
    expect((await ids(await c.get("/api/incidents", { project: "shop", service: "-" }))).sort()).toEqual(["n1", "n2"]);
  });

  test("pagination has no duplicates or gaps", async () => {
    const seen: string[] = [];
    let before: string | null = null;
    do {
      const page: any = await (await c.get("/api/incidents", { ...WEEK, limit: 4, ...(before ? { before } : {}) })).json();
      seen.push(...page.items.map((i: any) => i.id));
      before = page.next_before;
    } while (before);
    expect(seen).toEqual(["a1", "a2", "a3", "a4", "b1", "b2"]);
  });

  test("pagination keeps ties on created_at apart by id", async () => {
    const t = now() - 60;
    for (const id of ["t1", "t2", "t3"]) await record(db.sql, { id, project: "tie", created_at: t + 0.123456 });
    const seen: string[] = [];
    let before: string | null = null;
    do {
      const page: any = await (await c.get("/api/incidents", { project: "tie", limit: 1, ...(before ? { before } : {}) })).json();
      seen.push(...page.items.map((i: any) => i.id));
      before = page.next_before;
    } while (before);
    expect(seen).toEqual(["t3", "t2", "t1"]);
  });

  test("detail has ordered steps with resolved blob", async () => {
    const d: any = await (await c.get("/api/incidents/a1")).json();
    expect(d.steps.map((s: any) => s.seq)).toEqual([1, 2, 3, 4]);
    expect([d.steps[1].body, d.steps[1].blob]).toEqual(["static question set", "k1"]);
    expect(d.steps[2].elapsed_ms).toBe(12.5);
    expect(d.state.samples.length).toBeGreaterThan(0);
    expect(d.decision_raw).toEqual({ answers: { x: 1 } });
    expect((await c.get("/api/incidents/nope")).status).toBe(404);
  });

  test("activity buckets by level", async () => {
    const data: any = await (await c.get("/api/activity", { since: "24h", bucket: "1h" })).json();
    const totals: Record<string, number> = {};
    for (const b of data.series) for (const [lv, n] of Object.entries(b.counts)) totals[lv] = (totals[lv] ?? 0) + (n as number);
    expect(totals).toEqual({ error: 1, warn: 2, fatal: 1 });
    expect(data.series.length).toBeGreaterThanOrEqual(24);
    expect(data.series.length).toBeLessThanOrEqual(25);
  });

  test("activity truncates fractional times into their bucket", async () => {
    // floor, not round: 1.9 buckets in is the second bucket.
    await record(db.sql, { id: "f1", project: "frac", created_at: 1000 + 1.9 * 60 });
    const data: any = await (await c.get("/api/activity", { project: "frac", since: "1000", until: "1600", bucket: "1m" })).json();
    expect(data.series.findIndex((b: any) => b.counts.error)).toBe(1);
  });
});

describe("auth and errors", () => {
  test("token required via header or cookie", async () => {
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    expect((await c.get("/api/projects")).status).toBe(401);
    expect((await c.get("/api/projects", {}, { Authorization: "Bearer nope" })).status).toBe(401);
    expect((await c.get("/api/projects", {}, { Authorization: "Bearer s3cret" })).status).toBe(200);
    expect((await c.fetch("/api/login", post({ token: "nope" }))).status).toBe(401);
    const resp = await c.fetch("/api/login", post({ token: "s3cret" }));
    expect(resp.status).toBe(200);
    const set = resp.headers.get("set-cookie")!.toLowerCase();
    expect(set).toContain("httponly");
    expect(set).toContain("samesite=strict");
    expect(set).not.toContain("secure"); // http://127.0.0.1; https adds Secure
    expect((await c.get("/api/projects")).status).toBe(200); // cookie now set on the client
    expect((await c.get("/")).status).toBe(200); // the UI shell is public
  });

  test("the login cookie is Secure over https", async () => {
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    const resp = await c.fetch("https://herjev.example.workers.dev/api/login", post({ token: "s3cret" }));
    expect(resp.headers.get("set-cookie")).toMatch(/; Secure$/);
  });

  test("failed logins are throttled per client address", async () => {
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    const from = (ip: string) => ({ "cf-connecting-ip": ip });
    for (let i = 0; i < 10; i++) expect((await c.fetch("/api/login", post({ token: "nope" }, from("1.1.1.1")))).status).toBe(401);
    expect((await c.fetch("/api/login", post({ token: "nope" }, from("1.1.1.1")))).status).toBe(429);
    expect((await c.fetch("/api/login", post({ token: "nope" }, from("2.2.2.2")))).status).toBe(401);
    // Only failures count, so the right token still logs in.
    expect((await c.fetch("/api/login", post({ token: "s3cret" }, from("1.1.1.1")))).status).toBe(200);
  });

  test("unmigrated database returns 503", async () => {
    const empty = await createDb(false);
    try {
      const resp = await client(empty.name).get("/api/incidents");
      expect(resp.status).toBe(503);
      expect(((await resp.json()) as any).error).toContain("migrate");
      expect((await client(empty.name).get("/api/tickets")).status).toBe(503);
      const moved = await move(client(empty.name), "fp", "new", "done");
      expect(moved.status).toBe(503);
      expect(((await moved.json()) as any).error).toContain("migrate");
      const status: any = await (await client(empty.name).get("/api/status")).json();
      expect([status.watcher, status.db_exists]).toEqual([null, false]);
    } finally {
      await empty.drop();
    }
  });

  test("unreachable database returns 503", async () => {
    const resp = await client(db.name, { HYPERDRIVE: { connectionString: "postgresql://x:y@127.0.0.1:1/none" } })
      .get("/api/projects");
    expect(resp.status).toBe(503);
  });

  test("reads never write", async () => {
    const snapshot = () => db.sql`
      select (select md5(string_agg(i::text, ',' order by id)) from herjev.incidents i) as incidents,
             (select count(*)::int from herjev.tickets) as tickets`;
    const before = await snapshot();
    for (const path of ["/api/projects", "/api/incidents/a1", "/api/tickets", "/api/tickets/fp-a1", "/api/tickets/stats",
                        "/api/activity", "/api/status", "/api/channels", "/api/tickets/fp-a1/sends",
                        "/api/tickets/fp-a1/handoff"]) {
      expect((await c.get(path)).status, path).toBe(200);
    }
    expect(await snapshot()).toEqual(before);
  });

  test("unknown routes, bad parameters and wrong methods", async () => {
    expect((await c.get("/")).status).toBe(200); // handed to the assets binding
    expect(await (await c.get("/incidents/a1")).text()).toBe("<html>app</html>");
    const unknown = await c.get("/api/unknown");
    expect([unknown.status, await unknown.json()]).toEqual([404, { error: "not found" }]);
    expect((await c.get("/api/incidents", { before: "bad:x" })).status).toBe(400);
    expect((await c.get("/api/activity", { since: "1e400" })).status).toBe(400);
    expect((await c.get("/api/activity", { bucket: "soon" })).status).toBe(400);
    expect((await c.get("/api/incidents", { limit: "many" })).status).toBe(400);
    expect((await c.fetch("/api/projects", { method: "POST" })).status).toBe(405);
    expect((await c.get("/api/tickets/fp-a1/status")).status).toBe(405);
  });

  test("without a token only loopback hosts are served", async () => {
    for (const host of ["127.0.0.1:8787", "localhost:8787", "[::1]:8787", "localhost"]) {
      expect((await c.get("/api/status", {}, { Host: host })).status, host).toBe(200);
    }
    const resp = await c.get("/api/incidents", {}, { Host: "herjev.example.workers.dev" });
    expect(resp.status).toBe(500);
    expect(((await resp.json()) as any).error).toContain("HERJEV_DASHBOARD_TOKEN is not set");
    // With a token, the token is the gate and any host name works.
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    const ok = await c.get("/api/status", {}, { Host: "herjev.example.workers.dev", Authorization: "Bearer s3cret" });
    expect(ok.status).toBe(200);
  });

  test("status reports the watcher and the public URL", async () => {
    let data: any = await (await c.get("/api/status")).json();
    expect(data).toEqual({ watcher: null, db_path: "supabase", db_exists: true, public_url: null });
    await db.sql`insert into herjev.watcher_status (id, body, updated_at) values (1, ${db.sql.json({ alerts_sent: 2 })}, 0)`;
    c = client(db.name, { HERJEV_PUBLIC_URL: "https://h" });
    data = await (await c.get("/api/status")).json();
    expect([data.watcher, data.public_url]).toEqual([{ alerts_sent: 2 }, "https://h"]);
  });
});

describe("tickets", () => {
  test("tickets group incidents by fingerprint", async () => {
    const data: any = await (await c.get("/api/tickets", WEEK)).json();
    expect(data.columns.map((col: any) => col.status)).toEqual([...TICKET_STATUSES]);
    const fresh = column(data, "new");
    expect(fresh.total).toBe(5);
    expect(data.columns.slice(1).every((col: any) => col.total === 0)).toBe(true);
    expect(fps(data, "new")).toEqual(["fp-a1", "fp-slow", "fp-a3", "fp-b1", "fp-b2"]);
    const slow = fresh.items[1];
    expect([slow.incidents, slow.occurrences, slow.max_level]).toEqual([2, 6, "warn"]);
    expect([slow.latest_id, slow.service, slow.project, slow.status]).toEqual(["a2", "api", "shop", "new"]);
    expect(slow.first_seen).toBeLessThan(slow.last_seen);
    expect(data.transitions.new).toEqual(["inprogress", "cancel", "review", "done"]);
    expect([data.transitions.done, data.transitions.cancel]).toEqual([[], []]);
  });

  test("time range applies only to new, cancel and done", async () => {
    await setStatus(db.sql, "fp-b2", "inprogress"); // last seen 50h ago
    await setStatus(db.sql, "fp-b1", "done"); // last seen 30h ago
    const data: any = await (await c.get("/api/tickets", { since: "24h" })).json();
    expect(fps(data, "inprogress")).toEqual(["fp-b2"]);
    expect([fps(data, "done"), column(data, "done").total]).toEqual([[], 0]);
    expect(fps(data, "new")).toEqual(["fp-a1", "fp-slow", "fp-a3"]);
  });

  test("ticket filters apply to every column", async () => {
    await setStatus(db.sql, "fp-b2", "review");
    let data: any = await (await c.get("/api/tickets", { ...WEEK, project: "shop" })).json();
    expect([fps(data, "review"), fps(data, "new")]).toEqual([[], ["fp-a1", "fp-slow", "fp-a3"]]);
    data = await (await c.get("/api/tickets", { ...WEEK, q: "token" })).json();
    expect([fps(data, "review"), fps(data, "new")]).toEqual([["fp-b2"], []]);
    // A ticket matches when any occurrence does: a4 is the only suppressed row of fp-slow.
    data = await (await c.get("/api/tickets", { ...WEEK, outcome: "suppressed" })).json();
    expect(fps(data, "new")).toEqual(["fp-slow"]);
  });

  test("ticket stats count every ticket with the board rules", async () => {
    await setStatus(db.sql, "fp-b2", "inprogress"); // last seen 50h ago, still open
    await setStatus(db.sql, "fp-a3", "done");
    const stats: any = await (await c.get("/api/tickets/stats", { since: "24h" })).json();
    expect(stats.by_status).toEqual({ new: 2, inprogress: 1, cancel: 0, review: 0, done: 1 });
    expect([stats.open, stats.open_by_level]).toEqual([3, { error: 2, warn: 1 }]);
    // Occurrences in the last 24h of open tickets: a1 (3) and both fp-slow rows (3 + 3).
    expect([stats.occurrences_24h, stats.incidents_24h]).toEqual([9, 3]);
    const board: any = await (await c.get("/api/tickets", { since: "24h" })).json();
    expect(Object.fromEntries(board.columns.map((col: any) => [col.status, col.total]))).toEqual(stats.by_status);

    const shop: any = await (await c.get("/api/tickets/stats", { since: "24h", project: "shop" })).json();
    expect([shop.open, shop.by_status.done, shop.by_status.inprogress]).toEqual([2, 1, 0]);
    const panic: any = await (await c.get("/api/tickets/stats", { ...WEEK, level: "panic,fatal" })).json();
    expect([panic.open_by_level, panic.by_status.done]).toEqual([{ panic: 1 }, 1]);
  });

  test("ticket column pagination", async () => {
    const seen: string[] = [];
    let before: string | null = null;
    do {
      const data: any = await (await c.get("/api/tickets", { ...WEEK, status: "new", limit: 2, ...(before ? { before } : {}) })).json();
      expect(data.columns.map((col: any) => [col.status, col.total])).toEqual([["new", 5]]);
      seen.push(...data.columns[0].items.map((t: any) => t.fingerprint));
      before = data.columns[0].next_before;
    } while (before);
    expect(seen).toEqual(["fp-a1", "fp-slow", "fp-a3", "fp-b1", "fp-b2"]);
    expect((await c.get("/api/tickets", { status: "bogus" })).status).toBe(400);
  });

  test("ticket detail lists occurrences newest first", async () => {
    const d: any = await (await c.get("/api/tickets/fp-slow")).json();
    expect(d.items.map((i: any) => i.id)).toEqual(["a2", "a4"]);
    expect([d.incidents, d.status, d.transitions.new.length > 0]).toEqual([2, "new", true]);
    expect((await c.get("/api/tickets/nope")).status).toBe(404);
  });

  test("every transition is applied or refused", async () => {
    for (const src of TICKET_STATUSES) {
      for (const dst of TICKET_STATUSES) {
        await setStatus(db.sql, "fp-a1", src);
        const resp = await move(c, "fp-a1", src, dst);
        const after = ((await (await c.get("/api/tickets/fp-a1")).json()) as any).status;
        if (canTransition(src, dst)) {
          expect([resp.status, ((await resp.json()) as any).status, after], `${src}→${dst}`).toEqual([200, dst, dst]);
        } else {
          expect([resp.status, after], `${src}→${dst}`).toEqual([409, src]);
        }
      }
    }
  });

  test("a moved ticket stays moved and stale moves are refused", async () => {
    expect((await move(c, "fp-a1", "new", "inprogress")).status).toBe(200);
    expect(fps(await (await c.get("/api/tickets", WEEK)).json(), "inprogress")).toEqual(["fp-a1"]);
    const resp = await move(c, "fp-a1", "new", "review"); // another user already moved it
    expect(resp.status).toBe(409);
    expect(((await resp.json()) as any).error).toContain("inprogress");
  });

  test("two concurrent moves of one ticket: one wins, the other is refused", async () => {
    const [a, b] = await Promise.all([move(c, "fp-a1", "new", "inprogress"), move(c, "fp-a1", "new", "review")]);
    expect([a.status, b.status].sort()).toEqual([200, 409]);
    const winner = a.status === 200 ? "inprogress" : "review";
    expect(((await (await c.get("/api/tickets/fp-a1")).json()) as any).status).toBe(winner);
  });

  test("ticket status rejects bad requests", async () => {
    expect((await move(c, "nope", "new", "inprogress")).status).toBe(404);
    expect((await move(c, "fp-a1", "new", "bogus")).status).toBe(400);
    const form = await c.fetch("/api/tickets/fp-a1/status", {
      method: "POST", body: "from=new&to=done", headers: { "content-type": "application/x-www-form-urlencoded" },
    });
    expect(form.status).toBe(415);
    const garbled = await c.fetch("/api/tickets/fp-a1/status", {
      method: "POST", body: "not json", headers: { "content-type": "application/json" },
    });
    expect(garbled.status).toBe(400);
    expect((await c.fetch("/api/tickets/fp-a1/status", post(["new", "done"]))).status).toBe(400);
    expect(((await (await c.get("/api/tickets/fp-a1")).json()) as any).status).toBe("new");
  });

  test("ticket status requires the token", async () => {
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    expect((await move(c, "fp-a1", "new", "done")).status).toBe(401);
    expect((await move(c, "fp-a1", "new", "done", { Authorization: "Bearer s3cret" })).status).toBe(200);
  });
});

describe("sends", () => {
  const CHANNELS = [{ name: "teams", type: "teams", auto: false }, { name: "slack", type: "slack", auto: true }];
  const heartbeat = (body: unknown, age = 0) => db.sql`
    insert into herjev.watcher_status (id, body, updated_at) values (1, ${db.sql.json(body as any)}, ${now() - age})
    on conflict (id) do update set body = excluded.body, updated_at = excluded.updated_at`;
  const send = (fp: string, body: unknown, headers?: Record<string, string>) =>
    c.fetch(`/api/tickets/${fp}/sends`, post(body, headers));

  test("channels lists what the watcher built, auto: false included", async () => {
    expect(await (await c.get("/api/channels")).json()).toEqual({ online: false, channels: [] });
    await heartbeat({ channels: CHANNELS });
    expect(await (await c.get("/api/channels")).json()).toEqual({ online: true, channels: CHANNELS });
    // A watcher that published only names takes no sends.
    await heartbeat({ channels: ["teams"] });
    expect(((await (await c.get("/api/channels")).json()) as any).channels).toEqual([]);
    await heartbeat({ channels: CHANNELS }, 120);
    expect(((await (await c.get("/api/channels")).json()) as any).online).toBe(false);
  });

  test("a send queues the newest occurrence for each channel, pending", async () => {
    await heartbeat({ channels: CHANNELS });
    const resp = await send("fp-slow", { channels: ["teams", "slack", "teams"] });
    expect(resp.status).toBe(201);
    const items = ((await resp.json()) as any).items;
    expect(items.map((i: any) => [i.channel, i.incident_id, i.status])).toEqual([
      ["teams", "a2", "pending"], ["slack", "a2", "pending"]]);
    // Sending again is allowed: one row per send.
    expect((await send("fp-slow", { channels: ["teams"] })).status).toBe(201);
    const history: any = await (await c.get("/api/tickets/fp-slow/sends")).json();
    expect(history.items).toHaveLength(3);
    expect(history.items[0]).toMatchObject({ channel: "teams", status: "pending", error: null, finished_at: null });
    expect(((await (await c.get("/api/tickets/fp-a1/sends")).json()) as any).items).toEqual([]);
  });

  test("a request the watcher never picked up reads as expired once too old", async () => {
    await db.sql`insert into herjev.channel_sends (fingerprint, incident_id, channel, requested_at, status)
                 values ('fp-a1', 'a1', 'teams', ${now() - 700}, 'pending'), ('fp-a1', 'a1', 'slack', ${now() - 700}, 'sent')`;
    const items = ((await (await c.get("/api/tickets/fp-a1/sends")).json()) as any).items;
    expect(items.map((i: any) => [i.channel, i.status])).toEqual(expect.arrayContaining([["teams", "expired"], ["slack", "sent"]]));
    expect(items.find((i: any) => i.channel === "teams").error).toContain("in time");
  });

  test("a send is refused for unknown channels, unknown tickets and an offline watcher", async () => {
    const count = async () => (await db.sql`select count(*)::int as n from herjev.channel_sends`)[0].n;
    expect((await send("fp-a1", { channels: ["teams"] })).status).toBe(400); // nothing published yet
    await heartbeat({ channels: CHANNELS });
    const unknown = await send("fp-a1", { channels: ["teams", "pager"] });
    expect([unknown.status, ((await unknown.json()) as any).error]).toEqual([400, "unknown channel(s): pager"]);
    expect((await send("nope", { channels: ["teams"] })).status).toBe(404);
    for (const body of [{}, { channels: [] }, { channels: "teams" }, { channels: [1] }, ["teams"],
                        { channels: Array.from({ length: 51 }, (_, i) => `c${i}`) }]) {
      expect((await send("fp-a1", body)).status, JSON.stringify(body)).toBe(400);
    }
    const form = await c.fetch("/api/tickets/fp-a1/sends", {
      method: "POST", body: "channels=teams", headers: { "content-type": "application/x-www-form-urlencoded" },
    });
    expect(form.status).toBe(415);
    await heartbeat({ channels: CHANNELS }, 120);
    const offline = await send("fp-a1", { channels: ["teams"] });
    expect([offline.status, ((await offline.json()) as any).error]).toEqual([409, "the watcher is offline; a send needs it running"]);
    expect(await count()).toBe(0);
  });

  test("sends require the token", async () => {
    await heartbeat({ channels: CHANNELS });
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    expect((await send("fp-a1", { channels: ["teams"] })).status).toBe(401);
    expect((await c.get("/api/channels")).status).toBe(401);
    expect((await send("fp-a1", { channels: ["teams"] }, { Authorization: "Bearer s3cret" })).status).toBe(201);
  });
});

describe("services", () => {
  const CATALOG = {
    coolify: [
      { label: "api-t4y7u1i9o3p6a2s8d5f0g3h1", name: "api", watched: true },
      { label: "k3v9q2m7x1c8b4n6z0p5r2t7", name: "k3v9q2m7x1c8b4n6z0p5r2t7", watched: true },
      { label: "worker-t4y7u1i9o3p6a2s8d5f0g3h1", name: "worker", watched: true },
    ],
  };
  const publish = (body: unknown) => db.sql`
    insert into herjev.watcher_status (id, body, updated_at) values (1, ${db.sql.json(body as any)}, ${now()})
    on conflict (id) do update set body = excluded.body, updated_at = excluded.updated_at`;
  const choose = (body: unknown, headers?: Record<string, string>) => c.fetch("/api/services", post(body, headers));
  const view = async (resp: Response) => {
    const data: any = await resp.json();
    return data.sources.map((s: any) => [s.name, s.chosen, s.services.map((x: any) => `${x.name}${x.watched ? "*" : ""}${x.missing ? "?" : ""}`)]);
  };

  test("lists what the watcher published, with the config's defaults", async () => {
    expect(await (await c.get("/api/services")).json()).toEqual({ online: false, sources: [] });
    await publish({ services: CATALOG });
    expect(await view(await c.get("/api/services")))
      .toEqual([["coolify", false, ["api*", "k3v9q2m7x1c8b4n6z0p5r2t7*", "worker*"]]]);
  });

  test("choosing services and naming one; watched rows decide while any is watched", async () => {
    await publish({ services: CATALOG });
    const label = "k3v9q2m7x1c8b4n6z0p5r2t7";
    let resp = await choose({ source: "coolify", changes: [
      { label, watched: true, display_name: "  web-api " }, { label: "api-t4y7u1i9o3p6a2s8d5f0g3h1", watched: true }] });
    expect(resp.status).toBe(200);
    expect(await view(resp)).toEqual([["coolify", true, ["api*", "web-api*", "worker"]]]);
    // Renaming keeps the choice; clearing the name falls back to the config's.
    resp = await choose({ source: "coolify", changes: [{ label: "worker-t4y7u1i9o3p6a2s8d5f0g3h1", display_name: "jobs" }] });
    expect(await view(resp)).toEqual([["coolify", true, ["api*", "web-api*", "jobs"]]]);
    resp = await choose({ source: "coolify", changes: [{ label, display_name: "" }] });
    expect(await view(resp)).toEqual([["coolify", true, ["api*", "k3v9q2m7x1c8b4n6z0p5r2t7*", "jobs"]]]);
    // Unticking every service hands the choice back to the config.
    resp = await choose({ source: "coolify", changes: [
      { label, watched: false }, { label: "api-t4y7u1i9o3p6a2s8d5f0g3h1", watched: false }] });
    expect(await view(resp)).toEqual([["coolify", false, ["api*", "k3v9q2m7x1c8b4n6z0p5r2t7*", "jobs*"]]]);
    // A chosen service the source stopped reporting stays listed, so it can be unticked.
    await choose({ source: "coolify", changes: [{ label, watched: true }] });
    await publish({ services: { coolify: CATALOG.coolify.filter((s) => s.label !== label) } });
    expect(await view(await c.get("/api/services"))).toEqual([["coolify", true, ["api", "jobs", `${label}*?`]]]);
  });

  test("refuses unknown sources and services, bad bodies, and needs the token", async () => {
    await publish({ services: CATALOG });
    const count = async () => (await db.sql`select count(*)::int as n from herjev.source_services`)[0].n;
    expect((await choose({ source: "other", changes: [{ label: "x", watched: true }] })).status).toBe(404);
    const unknown = await choose({ source: "coolify", changes: [{ label: "nope", watched: true }] });
    expect([unknown.status, ((await unknown.json()) as any).error]).toEqual([400, "unknown service(s): nope"]);
    for (const body of [{}, { source: "coolify" }, { source: "coolify", changes: [] },
                        { source: "coolify", changes: [{ label: "worker-t4y7u1i9o3p6a2s8d5f0g3h1", watched: "yes" }] },
                        { source: "coolify", changes: [{ label: "worker-t4y7u1i9o3p6a2s8d5f0g3h1", display_name: 3 }] },
                        { source: "coolify", changes: [{ label: "worker-t4y7u1i9o3p6a2s8d5f0g3h1", display_name: "x".repeat(101) }] }]) {
      expect((await choose(body)).status, JSON.stringify(body)).toBe(400);
    }
    expect(await count()).toBe(0);
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    expect((await c.get("/api/services")).status).toBe(401);
    const body = { source: "coolify", changes: [{ label: "worker-t4y7u1i9o3p6a2s8d5f0g3h1", watched: true }] };
    expect((await choose(body)).status).toBe(401);
    expect((await choose(body, { Authorization: "Bearer s3cret" })).status).toBe(200);
  });
});

describe("handoff", () => {
  test("the bundle of the newest occurrence, or of a chosen one, as uncached Markdown", async () => {
    const resp = await c.get("/api/tickets/fp-slow/handoff");
    expect(resp.status).toBe(200);
    expect(resp.headers.get("content-type")).toBe("text/markdown; charset=utf-8");
    expect(resp.headers.get("cache-control")).toBe("no-store");
    expect(resp.headers.get("x-content-type-options")).toBe("nosniff");
    const md = await resp.text();
    expect(md).toContain("# herjev incident slow request");
    expect(md).toContain("- Ticket: http://127.0.0.1:8787/tickets/fp-slow?occ=a2");
    expect(md).toContain("Không có dự đoán");
    const older = await (await c.get("/api/tickets/fp-slow/handoff", { occ: "a4" })).text();
    expect(older).toContain("?occ=a4");
    expect((await c.fetch("/api/tickets/fp-slow/handoff", { method: "HEAD" })).status).toBe(200);
  });

  test("the ticket link uses the public URL and the analyzer's prediction is included", async () => {
    await db.sql`update herjev.incidents set analysis = ${db.sql.json({ summary: "pool exhausted",
      probable_causes: ["pool too small"], impact: "", recommended_actions: [], diagnostic_commands: [],
      confidence: 0.5, provider: "claude" })} where id = 'a1'`;
    c = client(db.name, { HERJEV_PUBLIC_URL: "https://portal.example/" });
    const md = await (await c.get("/api/tickets/fp-a1/handoff")).text();
    expect(md).toContain("- Ticket: https://portal.example/tickets/fp-a1?occ=a1");
    expect(md).toContain("**Tóm tắt:** pool exhausted");
    expect(md).toContain("1. pool too small");
  });

  test("unknown tickets, occurrences of another ticket and writes are refused", async () => {
    const unknown = await c.get("/api/tickets/fp-nope/handoff");
    expect([unknown.status, await unknown.json()]).toEqual([404, { error: "ticket not found" }]);
    const other = await c.get("/api/tickets/fp-slow/handoff", { occ: "a1" });
    expect([other.status, await other.json()]).toEqual([404, { error: "incident not found" }]);
    expect((await c.get("/api/tickets/fp-slow/handoff", { occ: "zz" })).status).toBe(404);
    expect((await c.fetch("/api/tickets/fp-slow/handoff", post({}))).status).toBe(405);
  });

  test("the bundle requires the token", async () => {
    c = client(db.name, { HERJEV_DASHBOARD_TOKEN: "s3cret" });
    expect((await c.get("/api/tickets/fp-a1/handoff")).status).toBe(401);
    expect((await c.get("/api/tickets/fp-a1/handoff", {}, { Authorization: "Bearer s3cret" })).status).toBe(200);
  });
});

/* Python test (tests/test_dashboard_api.py, removed) → test here
 *
 * test_projects_group_services_with_max_level        → projects group services with max level
 * test_incident_filters                              → incident filters
 * test_pagination_has_no_duplicates_or_gaps          → pagination has no duplicates or gaps
 * test_detail_has_ordered_steps_with_resolved_blob   → detail has ordered steps with resolved blob
 * test_activity_buckets_by_level                     → activity buckets by level
 * test_token_required_via_header_or_cookie           → token required via header or cookie
 * test_unmigrated_or_missing_database_returns_503    → unmigrated database returns 503;
 *                                                      unreachable database returns 503
 * test_dashboard_never_writes                        → reads never write
 * test_static_ui_and_spa_fallback                    → unknown routes, bad parameters and wrong methods
 *                                                      (static files and the SPA fallback are now the
 *                                                      assets layer's job, configured in wrangler.jsonc)
 * test_no_token_serves_only_loopback_or_allowed_hosts → without a token only loopback hosts are served
 *                                                      (no allowed_hosts: a deployed portal needs a token)
 * test_failed_logins_are_throttled                   → failed logins are throttled per client address
 * test_basic_auth_gates_every_path                   → dropped: the portal has token auth only
 * test_basic_auth_failures_are_throttled             → dropped: as above
 * test_bearer_token_still_works_with_basic_auth      → dropped: as above
 * test_basic_auth_needs_user_and_password            → dropped: as above
 * test_status_exposes_public_url                     → status reports the watcher and the public URL
 * test_search_matches_non_ascii_log_text_old_and_new → search ignores case and matches %, _ and non-ASCII
 *                                                      text literally (no JSON-escaped legacy rows remain)
 * test_service_less_incidents_have_their_own_filter  → service-less incidents have their own filter
 * test_run_serves_dashboard_alongside_watcher        → dropped: the watcher no longer serves the portal
 * test_tickets_group_incidents_by_fingerprint        → tickets group incidents by fingerprint
 * test_time_range_applies_only_to_new_cancel_and_done → time range applies only to new, cancel and done
 * test_ticket_filters_apply_to_every_column          → ticket filters apply to every column
 * test_ticket_stats_count_every_ticket_with_the_board_rules → ticket stats count every ticket with the board rules
 * test_ticket_stats_route_is_not_a_fingerprint       → reads never write (GET /api/tickets/stats is 200)
 * test_ticket_column_pagination                      → ticket column pagination
 * test_ticket_detail_lists_occurrences_newest_first  → ticket detail lists occurrences newest first
 * test_every_transition_is_applied_or_refused        → every transition is applied or refused
 * test_moved_ticket_stays_moved_and_stale_moves_are_refused → a moved ticket stays moved and stale moves are refused
 * test_a_move_whose_reply_fails_is_rolled_back       → ticket-rollback.test.ts
 * test_ticket_status_rejects_bad_requests            → ticket status rejects bad requests
 * test_ticket_status_requires_the_token              → ticket status requires the token
 * test_tickets_need_the_migrated_database            → unmigrated database returns 503
 */
