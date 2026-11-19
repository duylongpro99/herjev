/** SQL over the incident store, returning plain objects for the API.
 *
 * The portal connects as ``herjev_portal``, which can read every table and write only
 * ``tickets`` (see ``tickets.ts``) and send requests in ``channel_sends`` (``sends.ts``). Every value is bound as a parameter; dynamic parts
 * are ``sql`` fragments, never concatenated strings. */

import type { Sql } from "./db";

type Row = Record<string, any>;
type Fragment = ReturnType<Sql["unsafe"]>;

/** A reply with this status and ``{"error": message}``. */
export class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

export const badRequest = (message: string) => new HttpError(400, message);

export const TICKET_STATUSES = ["new", "inprogress", "cancel", "review", "done"] as const;
export type TicketStatus = (typeof TICKET_STATUSES)[number];
const TERMINAL_STATUSES: readonly string[] = ["cancel", "done"];
const OPEN_STATUSES: readonly string[] = ["new", "inprogress", "review"];

export const isStatus = (value: string): value is TicketStatus =>
  (TICKET_STATUSES as readonly string[]).includes(value);

/** Whether a person may move a ticket from ``src`` to ``dst``. Cancel and Done are
 * terminal: nothing moves out of them by hand (a Done ticket only returns to New on its
 * own, when the watcher sees its error again). */
export function canTransition(src: string, dst: string): boolean {
  return isStatus(src) && isStatus(dst) && src !== dst && !TERMINAL_STATUSES.includes(src);
}

export function transitions(): Record<TicketStatus, TicketStatus[]> {
  return Object.fromEntries(
    TICKET_STATUSES.map((s) => [s, TICKET_STATUSES.filter((d) => canTransition(s, d))]),
  ) as Record<TicketStatus, TicketStatus[]>;
}

const LEVEL_RANK: Record<string, number> = {
  trace: 5, debug: 10, info: 20, warn: 30, error: 40, fatal: 50, panic: 60,
};
// Built from the constant above only, so it is safe to splice in unbound.
const RANK_SQL =
  "case level " + Object.entries(LEVEL_RANK).map(([k, v]) => `when '${k}' then ${v}`).join(" ") + " else 0 end";

const LIST_COLUMNS = [
  "id", "fingerprint", "created_at", "project", "service", "source", "host", "level", "title",
  "count", "notify", "urgency", "category", "decision_reason", "decider", "outcome",
  "channels", "duration_ms",
];
const TICKET_FIELDS = [
  "fingerprint", "status", "updated_at", "reopened_at", "title", "project", "service",
  "latest_id", "incidents", "occurrences", "first_seen", "last_seen", "latest_outcome",
];
const MAX_BUCKETS = 500;
const UNIT_SECONDS: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400 };
/** ``service`` filter value for incidents that have no service. */
export const NO_SERVICE = "-";

export interface Filters {
  project?: string | null;
  service?: string | null;
  level?: string | null;
  outcome?: string | null;
  q?: string | null;
  since?: number | null;
  until?: number | null;
}

export const nowSeconds = () => Date.now() / 1000;

export function parseDuration(value: string): number {
  const m = /^(\d+(?:\.\d+)?)([smhd])$/.exec(value.trim());
  if (!m) throw badRequest(`invalid duration '${value}'; use e.g. 30m, 1h, 24h, 7d`);
  return Number(m[1]) * UNIT_SECONDS[m[2]];
}

/** ``1h``/``24h``/``7d`` mean "that long ago"; a number is an epoch in seconds. */
export function parseTime(value: string | null | undefined, now?: number): number | null {
  if (value == null || value === "") return null;
  const epoch = value.trim() === "" ? NaN : Number(value);
  if (Number.isNaN(epoch)) return (now ?? nowSeconds()) - parseDuration(value);
  if (!Number.isFinite(epoch)) throw badRequest(`invalid time '${value}'`);
  return epoch;
}

/** An integer query parameter, clamped to 1..500 like every page size here. */
export function parseLimit(value: string | null | undefined, fallback = 50): number {
  if (value == null) return fallback;
  if (!/^\s*[+-]?\d+\s*$/.test(value)) throw badRequest(`invalid limit '${value}'`);
  return Math.max(1, Math.min(Number(value), 500));
}

/** ``"<epoch>:<key>"``, the opaque ``next_before`` cursor of the previous page. */
function parseCursor(before: string): [number, string] {
  const i = before.indexOf(":");
  const at = i < 0 ? before : before.slice(0, i);
  const n = at.trim() === "" ? NaN : Number(at);
  if (!Number.isFinite(n)) throw badRequest(`invalid cursor '${before}'`);
  return [n, i < 0 ? "" : before.slice(i + 1)];
}

const csv = (value?: string | null) => (value ?? "").split(",").map((v) => v.trim()).filter(Boolean);

const maxLabel = (rank: number) =>
  Object.entries(LEVEL_RANK).find(([, v]) => v === rank)?.[0] ?? "unknown";

function decode(row: Row, columns?: string[]): Row {
  const d = columns ? Object.fromEntries(columns.map((k) => [k, row[k]])) : { ...row };
  d.project = d.project || d.source;
  return d;
}

const and = (sql: Sql, parts: Fragment[]) =>
  parts.length ? parts.reduce((a, b) => sql`${a} and ${b}`) : sql`true`;

function where(sql: Sql, f: Filters): Fragment {
  const parts: Fragment[] = [];
  // Plain column comparisons, so idx_incidents_project(project, service, created_at) applies.
  if (f.project) parts.push(sql`project = ${f.project}`);
  if (f.service === NO_SERVICE) parts.push(sql`coalesce(service, '') = ''`);
  else if (f.service) parts.push(sql`service = ${f.service}`);
  const levels = csv(f.level);
  if (levels.length) parts.push(sql`level in ${sql(levels)}`);
  const outcomes = csv(f.outcome);
  if (outcomes.length) parts.push(sql`outcome in ${sql(outcomes)}`);
  if (f.q) {
    const like = "%" + f.q.replace(/[\\%_]/g, "\\$&") + "%";
    parts.push(sql`(title ilike ${like} escape '\\' or state::text ilike ${like} escape '\\')`);
  }
  if (f.since != null) parts.push(sql`created_at >= ${f.since}`);
  if (f.until != null) parts.push(sql`created_at < ${f.until}`);
  return and(sql, parts);
}

export async function projects(sql: Sql, since: number | null): Promise<Row[]> {
  const rows = await sql`
    select coalesce(project, source) as project, coalesce(service, '') as service, count(*) as count,
           max(${sql.unsafe(RANK_SQL)}) as max_rank, max(created_at) as last_seen
    from herjev.incidents where created_at >= ${since ?? 0}
    group by 1, 2 order by 1, 3 desc`;
  const out = new Map<string, Row>();
  for (const r of rows) {
    let p = out.get(r.project);
    if (!p) out.set(r.project, (p = { project: r.project, services: [], count: 0, max_rank: 0, last_seen: 0 }));
    p.services.push({ service: r.service, count: r.count, max_level: maxLabel(r.max_rank), last_seen: r.last_seen });
    p.count += r.count;
    p.max_rank = Math.max(p.max_rank, r.max_rank);
    p.last_seen = Math.max(p.last_seen, r.last_seen);
  }
  return [...out.values()]
    .sort((a, b) => b.count - a.count || (a.project < b.project ? -1 : a.project > b.project ? 1 : 0))
    .map(({ max_rank, ...p }) => ({ ...p, max_level: maxLabel(max_rank) }));
}

/** Newest first. ``before`` is the ``next_before`` cursor of the previous page. */
export async function incidents(sql: Sql, f: Filters, limit = 50, before?: string | null) {
  const parts = [where(sql, f)];
  if (before) {
    const [at, id] = parseCursor(before);
    parts.push(sql`(created_at < ${at} or (created_at = ${at} and id < ${id}))`);
  }
  const rows = await sql`
    select ${sql(LIST_COLUMNS)} from herjev.incidents where ${and(sql, parts)}
    order by created_at desc, id desc limit ${limit + 1}`;
  const last = rows[limit - 1];
  return {
    items: rows.slice(0, limit).map((r) => decode(r, LIST_COLUMNS)),
    next_before: rows.length > limit ? `${last.created_at}:${last.id}` : null,
  };
}

/** One incident's own fields, without its trace steps. */
export async function incidentRow(sql: Sql, id: string): Promise<Row | null> {
  const [row] = await sql`select * from herjev.incidents where id = ${id}`;
  return row ? decode(row) : null;
}

export async function incident(sql: Sql, id: string): Promise<Row | null> {
  const row = await incidentRow(sql, id);
  if (!row) return null;
  const steps = await sql`
    select s.seq, s.at, s.stage, s.message, coalesce(s.body, b.body) as body, s.elapsed_ms, s.blob
    from herjev.incident_steps s left join herjev.trace_blobs b on b.key = s.blob
    where s.incident_id = ${id} order by s.seq`;
  return { ...row, steps: [...steps] };
}

/** Incident counts per time bucket and level, zero-filled from ``since`` to now. */
export async function activity(sql: Sql, f: Filters, bucket: number, now = nowSeconds()) {
  const since = f.since ?? now - 86400;
  const until = f.until ?? now;
  bucket = Math.max(bucket, (until - since) / MAX_BUCKETS, 1);
  const rows = await sql`
    select floor((created_at - ${since}) / ${bucket})::int as b, level, count(*) as n
    from herjev.incidents where ${where(sql, { ...f, since, until })} group by 1, 2`;
  const n = Math.max(1, Math.floor((until - since) / bucket) + 1);
  const series = Array.from({ length: n }, (_, i) => ({ t: since + i * bucket, counts: {} as Record<string, number> }));
  for (const r of rows) if (r.b >= 0 && r.b < n) series[r.b].counts[r.level] = r.n;
  return { since, until, bucket, series };
}

/** Every fingerprint group whose occurrences match ``match``, with its stored status
 * (none stored means "new") and the fields of its newest occurrence. */
function ticketRows(sql: Sql, match: Fragment, keep: Fragment, page: Fragment, limit: number) {
  return sql`
    with grouped as (
      select fingerprint, count(*) as incidents, sum(count) as occurrences,
             min(created_at) as first_seen, max(created_at) as last_seen,
             max(${sql.unsafe(RANK_SQL)}) as max_rank
      from herjev.incidents where ${match} group by fingerprint
    ), visible as (
      select g.*, coalesce(t.status, 'new') as status, t.updated_at, t.reopened_at
      from grouped g left join herjev.tickets t on t.fingerprint = g.fingerprint
    ), counted as (
      select *, count(*) over (partition by status) as total from visible where ${keep}
    ), paged as (
      select *, row_number() over (partition by status order by last_seen desc, fingerprint desc) as rn
      from counted where ${page}
    )
    select p.*, i.id as latest_id, i.title, coalesce(i.project, i.source) as project, i.service,
           i.outcome as latest_outcome
    from paged p join herjev.incidents i on i.id = (
      select id from herjev.incidents where fingerprint = p.fingerprint
      order by created_at desc, id desc limit 1)
    where p.rn <= ${limit} order by p.status, p.rn`;
}

/** The time range only applies to New, Cancel and Done: In progress and Review always
 * list every ticket in them. */
const keepInRange = (sql: Sql, since?: number | null) =>
  since == null ? sql`true` : sql`(status in ('inprogress', 'review') or last_seen >= ${since})`;

function ticketItem(r: Row): Row {
  return { ...Object.fromEntries(TICKET_FIELDS.map((k) => [k, r[k]])), max_level: maxLabel(r.max_rank) };
}

/** Tickets per board column, newest activity first. A ticket matches when any of its
 * occurrences matches the filters. ``status`` returns that one column; ``before`` is
 * the column's ``next_before`` cursor. */
export async function tickets(sql: Sql, f: Filters, limit = 50, status?: string | null, before?: string | null) {
  if (status != null && !isStatus(status)) throw badRequest(`unknown status '${status}'`);
  const page: Fragment[] = [];
  if (status) page.push(sql`status = ${status}`);
  if (before) {
    const [at, fp] = parseCursor(before);
    page.push(sql`(last_seen < ${at} or (last_seen = ${at} and fingerprint < ${fp}))`);
  }
  const rows = await ticketRows(sql, where(sql, { ...f, since: null }), keepInRange(sql, f.since),
                                and(sql, page), limit + 1);
  const byStatus = new Map<string, Row[]>();
  for (const r of rows) byStatus.set(r.status, [...(byStatus.get(r.status) ?? []), r]);
  const columns = (status ? [status] : TICKET_STATUSES).map((st) => {
    const col = byStatus.get(st) ?? [];
    const last = col[limit - 1];
    return {
      status: st,
      total: col.length ? col[0].total : 0,
      items: col.slice(0, limit).map(ticketItem),
      next_before: col.length > limit ? `${last.last_seen}:${last.fingerprint}` : null,
    };
  });
  return { columns, transitions: transitions() };
}

/** Board totals that paging hides: tickets per status, open tickets per worst level,
 * and how often open tickets occurred in the last 24 hours. Filters and the time range
 * apply exactly as in {@link tickets}. */
export async function ticketStats(sql: Sql, f: Filters, now = nowSeconds()) {
  const recent = now - 86400;
  const rows = await sql`
    with grouped as (
      select fingerprint, max(${sql.unsafe(RANK_SQL)}) as max_rank, max(created_at) as last_seen,
             sum(case when created_at >= ${recent} then count else 0 end) as recent_occurrences,
             sum(case when created_at >= ${recent} then 1 else 0 end) as recent_incidents
      from herjev.incidents where ${where(sql, { ...f, since: null })} group by fingerprint
    ), visible as (
      select g.*, coalesce(t.status, 'new') as status
      from grouped g left join herjev.tickets t on t.fingerprint = g.fingerprint
    )
    -- A sum of bigints is numeric, which arrives as a string; cast back to stay a number.
    select status, max_rank, count(*) as n, sum(recent_occurrences)::bigint as occurrences,
           sum(recent_incidents)::bigint as incidents
    from visible where ${keepInRange(sql, f.since)} group by status, max_rank`;
  const byStatus = Object.fromEntries(TICKET_STATUSES.map((s) => [s, 0])) as Record<string, number>;
  const openByLevel: Record<string, number> = {};
  let open = 0, occurrences = 0, incidentsRecent = 0;
  for (const r of rows) {
    byStatus[r.status] += r.n;
    if (OPEN_STATUSES.includes(r.status)) {
      const level = maxLabel(r.max_rank);
      openByLevel[level] = (openByLevel[level] ?? 0) + r.n;
      open += r.n;
      occurrences += r.occurrences ?? 0;
      incidentsRecent += r.incidents ?? 0;
    }
  }
  return { open, open_by_level: openByLevel, by_status: byStatus,
           occurrences_24h: occurrences, incidents_24h: incidentsRecent };
}

export async function ticketSummary(sql: Sql, fp: string): Promise<Row | null> {
  const [row] = await ticketRows(sql, sql`fingerprint = ${fp}`, sql`true`, sql`true`, 1);
  return row ? ticketItem(row) : null;
}

/** One ticket over all its occurrences, with the newest 100 occurrences. */
export async function ticket(sql: Sql, fp: string): Promise<Row | null> {
  const d = await ticketSummary(sql, fp);
  if (!d) return null;
  const rows = await sql`
    select ${sql(LIST_COLUMNS)} from herjev.incidents where fingerprint = ${fp}
    order by created_at desc, id desc limit 100`;
  return { ...d, items: rows.map((r) => decode(r, LIST_COLUMNS)), transitions: transitions() };
}

/** The watcher's latest status and when it wrote it, or null before it first published one. */
export async function watcherState(sql: Sql): Promise<{ body: Row; updated_at: number } | null> {
  const [row] = await sql`select body, updated_at from herjev.watcher_status where id = 1`;
  return row ? { body: row.body, updated_at: row.updated_at } : null;
}

/** The watcher's latest status, or null before it first published one. */
export async function watcherStatus(sql: Sql): Promise<Row | null> {
  return (await watcherState(sql))?.body ?? null;
}
