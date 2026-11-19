/** Test databases on a real Postgres server, like the Python suite's fixtures: one
 * migrated template per test file, and a fresh copy of it per test. */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import postgres from "postgres";
import { handle, type Context, type Env } from "./app";

export const ADMIN_DSN = process.env.HERJEV_TEST_DATABASE_URL ?? "postgresql://postgres:postgres@127.0.0.1:5432/postgres";
const MIGRATIONS = join(import.meta.dirname, "../../supabase/migrations");
const ROLE_PASSWORDS = { herjev_watcher: "watcher", herjev_portal: "portal" };
// The setup sets those well-known passwords server-wide: never on a shared server.
const TEST_HOST = new URL(ADMIN_DSN).hostname;
if (!["127.0.0.1", "localhost", "[::1]", "postgres"].includes(TEST_HOST)) {
  throw new Error(`HERJEV_TEST_DATABASE_URL must point at a local, disposable server, not ${TEST_HOST}`);
}

export function dsn(dbname: string, user?: keyof typeof ROLE_PASSWORDS): string {
  const url = new URL(ADMIN_DSN);
  url.pathname = `/${dbname}`;
  if (user) {
    url.username = user;
    url.password = ROLE_PASSWORDS[user];
  }
  return url.toString();
}

declare module "vitest" {
  export interface ProvidedContext {
    template: string;
  }
}

const unique = (prefix: string) => `${prefix}_${process.pid}_${Math.random().toString(36).slice(2, 10)}`;

/** Server-level statements (create/drop database, role passwords) on the admin database. */
async function admin<T>(fn: (sql: postgres.Sql) => Promise<T>): Promise<T> {
  const sql = postgres(ADMIN_DSN, { max: 1, onnotice: () => {} });
  try {
    return await fn(sql);
  } finally {
    await sql.end();
  }
}

/** Create and migrate the template; returns its name. Fails, never skips, without a server.
 * Runs once per test run (``global-setup.ts``): migrating alters cluster-wide roles, which
 * two test files migrating at once would race on. */
export async function createTemplate(): Promise<string> {
  const name = unique("herjev_ts_tmpl");
  await admin(async (sql) => {
    await sql.unsafe(`create database ${name}`);
    for (const [role, password] of Object.entries(ROLE_PASSWORDS)) {
      // Roles exist once the first migration ever ran on this server; before that, the
      // migration below creates them and the loop after it sets the passwords.
      await sql.unsafe(`do $$ begin if exists (select from pg_roles where rolname = '${role}') then
        alter role ${role} password '${password}'; end if; end $$`);
    }
  });
  const tmpl = postgres(dsn(name), { max: 1, onnotice: () => {} });
  try {
    for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
      await tmpl.unsafe(readFileSync(join(MIGRATIONS, file), "utf8")).simple();
    }
    for (const [role, password] of Object.entries(ROLE_PASSWORDS)) {
      await tmpl.unsafe(`alter role ${role} password '${password}'`);
    }
  } finally {
    await tmpl.end();
  }
  return name;
}

export async function dropDatabase(name: string): Promise<void> {
  await admin((sql) => sql.unsafe(`drop database if exists ${name} with (force)`));
}

export interface TestDb {
  name: string;
  /** Superuser connection to this database, for seeding and checks. */
  sql: postgres.Sql;
  drop(): Promise<void>;
}

/** A copy of the migrated ``template``, or an empty database for ``false``. */
export async function createDb(template: string | false): Promise<TestDb> {
  const name = unique("herjev_ts");
  await admin((sql) => sql.unsafe(template ? `create database ${name} template ${template}` : `create database ${name}`));
  const sql = postgres(dsn(name), { max: 2, onnotice: () => {} });
  return {
    name,
    sql,
    async drop() {
      await sql.end();
      await dropDatabase(name);
    },
  };
}

/** Failed logins per key in this process, like the rate limit binding with no expiry. */
export function memoryLimiter(limit = 10): Env["LOGIN_LIMITER"] {
  const seen = new Map<string, number>();
  return {
    async limit({ key }) {
      const n = (seen.get(key) ?? 0) + 1;
      seen.set(key, n);
      return { success: n <= limit };
    },
  };
}

export interface Client {
  env: Env;
  /** Requests ``path`` on http://127.0.0.1:8787, keeping a cookie set by a login. */
  fetch(path: string, init?: RequestInit & { params?: Record<string, string | number> }): Promise<Response>;
  get(path: string, params?: Record<string, string | number>, headers?: HeadersInit): Promise<Response>;
}

/** The Worker as `wrangler dev` runs it, connected to ``db`` as the portal role. */
export function client(db: string, overrides: Partial<Env> = {}): Client {
  const env: Env = {
    HYPERDRIVE: { connectionString: dsn(db, "herjev_portal") },
    ASSETS: { fetch: async () => new Response("<html>app</html>", { headers: { "content-type": "text/html" } }) },
    LOGIN_LIMITER: memoryLimiter(),
    ...overrides,
  };
  let cookie = "";
  const c: Client = {
    env,
    async fetch(path, init = {}) {
      const { params, ...rest } = init;
      const url = new URL(path, "http://127.0.0.1:8787");
      for (const [k, v] of Object.entries(params ?? {})) url.searchParams.set(k, String(v));
      const headers = new Headers(rest.headers);
      if (!headers.has("host")) headers.set("host", url.host);
      if (cookie && !headers.has("cookie")) headers.set("cookie", cookie);
      const pending: Promise<unknown>[] = [];
      const ctx: Context = { waitUntil: (p) => void pending.push(p) };
      const resp = await handle(new Request(url, { ...rest, headers }), env, ctx);
      await Promise.all(pending);
      const set = resp.headers.get("set-cookie");
      if (set) cookie = set.split(";")[0];
      return resp;
    },
    get: (path, params, headers) => c.fetch(path, { params, headers }),
  };
  return c;
}

export const post = (body: unknown, headers: Record<string, string> = {}): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json", ...headers },
  body: JSON.stringify(body),
});
