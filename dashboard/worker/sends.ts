import type { Sql } from "./db";
import { HttpError, badRequest, nowSeconds, watcherState } from "./queries";

const SEND_FIELDS = ["id", "channel", "incident_id", "requested_at", "status", "error", "finished_at"];
/** The watcher's ``SEND_TTL_SECONDS``: a request still open after this will never go out. */
export const SEND_TTL_SECONDS = 600;
/** More names than any watcher has channels; caps the request and the error reply. */
const MAX_CHANNELS = 50;
/** A watcher whose heartbeat (every 10 s) is older than this is offline. */
export const WATCHER_STALE_SECONDS = 60;

export interface Channel {
  name: string;
  type: string;
  auto: boolean;
}

/** The channels the watcher built, as it last published them, and whether it is running.
 * A watcher too old to publish channel objects takes no sends, so it lists none. */
export async function channels(sql: Sql, now = nowSeconds()) {
  const state = await watcherState(sql);
  const published: unknown[] = Array.isArray(state?.body?.channels) ? state.body.channels : [];
  const list: Channel[] = published
    .filter((c: any) => c && typeof c === "object" && typeof c.name === "string")
    .map((c: any) => ({ name: c.name, type: String(c.type ?? ""), auto: c.auto !== false }));
  const online = state != null && now - state.updated_at < WATCHER_STALE_SECONDS;
  return { online, channels: list };
}

/** A ticket's sends from the portal, newest first. Only the watcher expires requests, so
 * one it never picked up (it stopped) reads as expired here once it is too old to go out. */
export async function sends(sql: Sql, fp: string, now = nowSeconds()) {
  const stale = now - SEND_TTL_SECONDS;
  const rows = await sql`
    select id, channel, incident_id, requested_at, finished_at,
           case when status in ('pending', 'sending') and requested_at < ${stale} then 'expired' else status end as status,
           case when status in ('pending', 'sending') and requested_at < ${stale}
                then 'the watcher did not pick it up in time' else error end as error
    from herjev.channel_sends where fingerprint = ${fp}
    order by requested_at desc, id desc limit 50`;
  return { items: [...rows] };
}

/** Queue the ticket's newest occurrence for each named channel: the portal's other write.
 * The watcher delivers it (it holds the channel secrets) and records the result. */
export async function requestSends(sql: Sql, fp: string, names: string[]) {
  if (names.length > MAX_CHANNELS) throw badRequest(`at most ${MAX_CHANNELS} channels per send`);
  const wanted = [...new Set(names)];
  const { online, channels: known } = await channels(sql);
  const unknown = wanted.filter((n) => !known.some((c) => c.name === n));
  if (unknown.length) throw badRequest(`unknown channel(s): ${unknown.map((n) => n.slice(0, 100)).join(", ")}`);
  // Refused rather than queued: a request the watcher picks up much later would surprise.
  if (!online) throw new HttpError(409, "the watcher is offline; a send needs it running");
  const [latest] = await sql`
    select id from herjev.incidents where fingerprint = ${fp} order by created_at desc, id desc limit 1`;
  if (!latest) throw new HttpError(404, "ticket not found");
  const rows = await sql`
    insert into herjev.channel_sends ${sql(wanted.map((channel) => ({ fingerprint: fp, incident_id: latest.id, channel })))}
    returning ${sql(SEND_FIELDS)}`;
  return { items: [...rows] };
}
