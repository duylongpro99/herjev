/** The portal Worker: the JSON API under /api over the incident store. Everything else
 * is the built UI, served by the assets layer before this code runs. */

import { QUERY_CANCELED, UNDEFINED_COLUMN, UNDEFINED_SCHEMA, UNDEFINED_TABLE, connect, type Sql } from "./db";
import * as queries from "./queries";
import { HttpError, badRequest, parseDuration, parseLimit, parseTime, type Filters } from "./queries";
import { handoff } from "./handoff";
import { channels, requestSends, sends } from "./sends";
import { chooseServices, services } from "./services";
import { setTicketStatus } from "./tickets";

export interface Env {
  HYPERDRIVE: { connectionString: string };
  ASSETS: { fetch(request: Request): Promise<Response> };
  LOGIN_LIMITER: { limit(options: { key: string }): Promise<{ success: boolean }> };
  HERJEV_DASHBOARD_TOKEN?: string;
  HERJEV_PUBLIC_URL?: string;
}

export interface Context {
  waitUntil(promise: Promise<unknown>): void;
}

export const COOKIE = "herjev_token";
const LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "::1"];
// Postgres errors raised by the client before or after a query reached the server.
const CONNECTION_ERRORS = ["CONNECT_TIMEOUT", "CONNECTION_CLOSED", "CONNECTION_ENDED", "CONNECTION_DESTROYED",
                           "ECONNREFUSED", "ECONNRESET", "ETIMEDOUT", "ENOTFOUND"];

const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  Response.json(body, { status, headers });
const error = (status: number, message: string) => json({ error: message }, status);
// The handoff bundle holds production logs: never cached.
const markdown = (body: string) =>
  new Response(body, {
    headers: { "Content-Type": "text/markdown; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });

/** Host header without its port: ``[::1]:8787`` → ``::1``, ``localhost:8787`` → ``localhost``. */
export function hostname(header: string): string {
  const h = header.trim().toLowerCase();
  if (h.startsWith("[")) return h.includes("]") ? h.slice(1, h.indexOf("]")) : h;
  return (h.match(/:/g) ?? []).length === 1 ? h.slice(0, h.lastIndexOf(":")) : h;
}

function cookie(request: Request, name: string): string {
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return "";
}

/** Constant time in the token's content: both sides are hashed to 32 bytes first, so
 * neither the length nor the first differing byte shows in the timing. */
async function sameToken(supplied: string, token: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([supplied, token].map((s) => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const x = new Uint8Array(a), y = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
  return diff === 0;
}

function suppliedToken(request: Request): string {
  const header = request.headers.get("authorization") ?? "";
  return header.toLowerCase().startsWith("bearer ") ? header.slice(7) : cookie(request, COOKIE);
}

function filters(p: URLSearchParams, since: string | null): Filters {
  return {
    project: p.get("project"), service: p.get("service"), level: p.get("level"),
    outcome: p.get("outcome"), q: p.get("q"), since: parseTime(since), until: parseTime(p.get("until")),
  };
}

function segment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    throw badRequest("invalid path");
  }
}

async function login(request: Request, env: Env, token: string | undefined): Promise<Response> {
  let supplied = "";
  try {
    const body: any = await request.json();
    supplied = String(body?.token ?? "");
  } catch {
    supplied = "";
  }
  if (!token) return json({ ok: true });
  if (!(await sameToken(supplied, token))) {
    // Only failures count: a person who knows the token is never locked out by them.
    const key = request.headers.get("cf-connecting-ip") ?? "local";
    const { success } = await env.LOGIN_LIMITER.limit({ key });
    return success ? error(401, "invalid token") : error(429, "too many failed logins; try again later");
  }
  const secure = new URL(request.url).protocol === "https:" ? "; Secure" : "";
  return json({ ok: true }, 200, {
    "Set-Cookie": `${COOKIE}=${supplied}; HttpOnly; SameSite=Strict; Path=/${secure}`,
  });
}

/** The JSON object body of a write, or null for a body that is not one.
 * JSON only: a cross-site HTML form cannot send it, and a cross-site fetch with this
 * content type needs a CORS preflight the portal never grants. */
async function jsonBody(request: Request): Promise<Record<string, unknown> | null> {
  const type = (request.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") throw new HttpError(415, "send a JSON body");
  try {
    const body = await request.json();
    return body && typeof body === "object" && !Array.isArray(body) ? (body as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

async function moveTicket(request: Request, sql: Sql, fp: string): Promise<Response> {
  const body = await jsonBody(request);
  if (!body || typeof body.from !== "string" || typeof body.to !== "string") {
    throw badRequest('body must be {"from": "<status>", "to": "<status>"}');
  }
  return json(await setTicketStatus(sql, fp, body.from, body.to));
}

async function sendTicket(request: Request, sql: Sql, fp: string): Promise<Response> {
  const body = await jsonBody(request);
  const names = body?.channels;
  if (!Array.isArray(names) || !names.length || !names.every((n) => typeof n === "string")) {
    throw badRequest('body must be {"channels": ["<channel>", ...]}');
  }
  return json(await requestSends(sql, fp, names), 201);
}

async function status(env: Env, sql: Sql): Promise<Response> {
  let watcher: unknown = null, reachable = true;
  try {
    watcher = await queries.watcherStatus(sql);
  } catch {
    reachable = false;
  }
  return json({ watcher, db_path: "supabase", db_exists: reachable, public_url: env.HERJEV_PUBLIC_URL || null });
}

/** The /api routes. Returns null for a path no route knows. */
async function route(request: Request, env: Env, sql: Sql, path: string): Promise<Response | null> {
  const p = new URL(request.url).searchParams;
  const get = request.method === "GET" || request.method === "HEAD";
  const post = request.method === "POST";
  const parts = path.split("/").slice(2); // "/api/tickets/x" → ["tickets", "x"]
  const [name, id, action] = parts;
  const notAllowed = () => error(405, "method not allowed");

  if (parts.length === 1) {
    switch (name) {
      case "projects":
        return get ? json(await queries.projects(sql, parseTime(p.get("since") ?? "24h"))) : notAllowed();
      case "incidents":
        return get
          ? json(await queries.incidents(sql, filters(p, p.get("since")), parseLimit(p.get("limit")), p.get("before")))
          : notAllowed();
      case "activity":
        return get
          ? json(await queries.activity(sql, filters(p, p.get("since") ?? "24h"), parseDuration(p.get("bucket") ?? "1h")))
          : notAllowed();
      case "tickets":
        return get
          ? json(await queries.tickets(sql, filters(p, p.get("since")), parseLimit(p.get("limit")), p.get("status"),
                                       p.get("before")))
          : notAllowed();
      case "status":
        return get ? status(env, sql) : notAllowed();
      case "channels":
        return get ? json(await channels(sql)) : notAllowed();
      case "services":
        if (get) return json(await services(sql));
        return post ? json(await chooseServices(sql, await jsonBody(request))) : notAllowed();
    }
  }
  if (parts.length === 2 && name === "incidents") {
    if (!get) return notAllowed();
    const row = await queries.incident(sql, segment(id));
    return row ? json(row) : error(404, "incident not found");
  }
  if (parts.length === 2 && name === "tickets") {
    if (!get) return notAllowed();
    if (id === "stats") return json(await queries.ticketStats(sql, filters(p, p.get("since"))));
    const row = await queries.ticket(sql, segment(id));
    return row ? json(row) : error(404, "ticket not found");
  }
  if (parts.length === 3 && name === "tickets" && action === "status") {
    return post ? moveTicket(request, sql, segment(id)) : notAllowed();
  }
  if (parts.length === 3 && name === "tickets" && action === "sends") {
    if (get) return json(await sends(sql, segment(id)));
    return post ? sendTicket(request, sql, segment(id)) : notAllowed();
  }
  if (parts.length === 3 && name === "tickets" && action === "handoff") {
    if (!get) return notAllowed();
    const base = env.HERJEV_PUBLIC_URL || new URL(request.url).origin;
    return markdown(await handoff(sql, segment(id), p.get("occ") || null, base));
  }
  return null;
}

function failure(err: unknown): Response {
  if (err instanceof HttpError) return error(err.status, err.message);
  const e = err as { code?: string; message?: string; name?: string };
  if (e?.name === "PostgresError") {
    if ([UNDEFINED_TABLE, UNDEFINED_SCHEMA, UNDEFINED_COLUMN].includes(e.code ?? "")) {
      return error(503, "incident database is not migrated yet; apply supabase/migrations (CI does this on deploy)");
    }
    if (e.code === QUERY_CANCELED) return error(503, "query took too long; narrow the time range or search");
    // The driver message can name hosts and tables; it goes to the Worker log, not the client.
    console.error("database error", e.code, e.message);
    return error(503, "database error; see the Worker log");
  }
  if (CONNECTION_ERRORS.includes(e?.code ?? "")) {
    console.error("database unavailable", e.code, e.message);
    return error(503, "database unavailable; try again shortly");
  }
  throw err;
}

export async function handle(request: Request, env: Env, ctx: Context): Promise<Response> {
  const url = new URL(request.url);
  if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
  const token = env.HERJEV_DASHBOARD_TOKEN || undefined;
  if (!token) {
    // Without a token only a loopback name is served (`wrangler dev`), so a web page cannot
    // read the portal through the user's browser by rebinding its own domain to 127.0.0.1.
    // Deployed, that means every request: a missing secret fails closed.
    if (!LOOPBACK_HOSTS.includes(hostname(request.headers.get("host") ?? url.host))) {
      return error(500, "HERJEV_DASHBOARD_TOKEN is not set; run `npx wrangler secret put HERJEV_DASHBOARD_TOKEN`");
    }
  } else if (url.pathname !== "/api/login" && !(await sameToken(suppliedToken(request), token))) {
    return error(401, "unauthorized");
  }
  if (url.pathname === "/api/login") {
    return request.method === "POST" ? login(request, env, token) : error(405, "method not allowed");
  }

  const sql = connect(env.HYPERDRIVE.connectionString);
  try {
    return (await route(request, env, sql, url.pathname)) ?? error(404, "not found");
  } catch (err) {
    return failure(err);
  } finally {
    ctx.waitUntil(sql.end());
  }
}
