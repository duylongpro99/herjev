import type { Sql } from "./db";
import { HttpError, badRequest, nowSeconds, watcherState } from "./queries";
import { WATCHER_STALE_SECONDS } from "./sends";

/** More changes than any source has services; caps one request. */
const MAX_CHANGES = 500;
const MAX_NAME = 100;

export interface Service {
  /** The service as the source reports it (a Loki `service_name` value). */
  label: string;
  /** The name it is shown under: the portal's name, else the config's. */
  name: string;
  /** The config's name, which a cleared portal name falls back to. */
  default_name: string;
  /** The name set in the portal, or null. */
  display_name: string | null;
  watched: boolean;
  /** Chosen or renamed in the portal, but the source no longer reports it. */
  missing: boolean;
}

export interface SourceServices {
  name: string;
  /** True while the portal's choice decides what is watched; false: the config does. */
  chosen: boolean;
  services: Service[];
}

interface Published {
  label: string;
  name: string;
  watched: boolean;
}

interface Row {
  source: string;
  label: string;
  watched: boolean;
  display_name: string | null;
}

/** Each source's published services (by the watcher, which can ask the source), as
 * a source name → services map. Entries that are not objects with a label are dropped. */
function published(body: any): Map<string, Published[]> {
  const out = new Map<string, Published[]>();
  const raw = body?.services;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return out;
  for (const [source, list] of Object.entries(raw)) {
    if (!Array.isArray(list)) continue;
    out.set(source, list
      .filter((s: any) => s && typeof s === "object" && typeof s.label === "string")
      .map((s: any) => ({ label: s.label, name: String(s.name ?? s.label), watched: s.watched !== false })));
  }
  return out;
}

/** The services of every source that can choose them, with the portal's choices applied the
 * way the watcher applies them: when any of a source's rows is watched, exactly those rows
 * are; otherwise the config decides. A portal name replaces the config's. */
export async function services(sql: Sql, now = nowSeconds()) {
  const state = await watcherState(sql);
  const online = state != null && now - state.updated_at < WATCHER_STALE_SECONDS;
  const catalogs = published(state?.body);
  const rows = (await sql`
    select source, label, watched, display_name from herjev.source_services order by source, label`) as unknown as Row[];
  const sources: SourceServices[] = [...catalogs].map(([name, catalog]) => {
    const mine = new Map(rows.filter((r) => r.source === name).map((r) => [r.label, r]));
    const chosen = [...mine.values()].some((r) => r.watched);
    const entry = (label: string, defaultName: string, defaultWatched: boolean, missing: boolean): Service => {
      const row = mine.get(label);
      return {
        label, default_name: defaultName, display_name: row?.display_name ?? null,
        name: row?.display_name || defaultName,
        watched: chosen ? !!row?.watched : defaultWatched, missing,
      };
    };
    const known = new Set(catalog.map((s) => s.label));
    return {
      name, chosen,
      services: [
        ...catalog.map((s) => entry(s.label, s.name, s.watched, false)),
        ...[...mine.values()].filter((r) => !known.has(r.label)).map((r) => entry(r.label, r.label, false, true)),
      ],
    };
  });
  return { online, sources };
}

interface Change {
  label: string;
  watched?: boolean;
  display_name?: string | null;
}

function parseChanges(body: Record<string, unknown> | null): { source: string; changes: Change[] } {
  const usage = 'body must be {"source": "<name>", "changes": [{"label": "<service>", "watched"?: bool, "display_name"?: "<name>"|null}]}';
  if (!body || typeof body.source !== "string" || !Array.isArray(body.changes) || !body.changes.length) {
    throw badRequest(usage);
  }
  if (body.changes.length > MAX_CHANGES) throw badRequest(`at most ${MAX_CHANGES} changes per request`);
  const changes = body.changes.map((c: any): Change => {
    if (!c || typeof c !== "object" || typeof c.label !== "string" || !c.label) throw badRequest(usage);
    if (c.watched !== undefined && typeof c.watched !== "boolean") throw badRequest(usage);
    let name = c.display_name;
    if (name !== undefined && name !== null && typeof name !== "string") throw badRequest(usage);
    if (typeof name === "string") {
      name = name.trim() || null;
      if (name && name.length > MAX_NAME) throw badRequest(`a service name has at most ${MAX_NAME} characters`);
    }
    return { label: c.label, watched: c.watched, display_name: name };
  });
  return { source: body.source, changes };
}

/** Choose or rename services of one source: the portal's third write. Only services the
 * source reports, or that already have a row, can be changed. The watcher applies the
 * rows on its next sync (every 15 s). */
export async function chooseServices(sql: Sql, body: Record<string, unknown> | null) {
  const { source, changes } = parseChanges(body);
  const current = (await services(sql)).sources.find((s) => s.name === source);
  if (!current) throw new HttpError(404, `source '${source.slice(0, 100)}' publishes no services`);
  const known = new Set(current.services.map((s) => s.label));
  const unknown = changes.filter((c) => !known.has(c.label)).map((c) => c.label.slice(0, 100));
  if (unknown.length) throw badRequest(`unknown service(s): ${unknown.join(", ")}`);
  await sql.begin(async (tx) => {
    for (const c of changes) {
      await tx`
        insert into herjev.source_services (source, label, watched, display_name)
        values (${source}, ${c.label}, ${c.watched ?? false}, ${c.display_name ?? null})
        on conflict (source, label) do update set
          watched = case when ${c.watched !== undefined} then excluded.watched else herjev.source_services.watched end,
          display_name = case when ${c.display_name !== undefined}
                              then excluded.display_name else herjev.source_services.display_name end,
          updated_at = extract(epoch from now())`;
    }
  });
  return services(sql);
}
