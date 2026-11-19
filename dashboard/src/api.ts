import type {
  Activity,
  ChannelList,
  ChannelSend,
  IncidentDetail,
  ProjectSummary,
  ServiceChange,
  ServiceList,
  Status,
  TicketBoard,
  TicketDetail,
  TicketStats,
  TicketStatus,
  TicketSummary,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();

/** Called whenever the API answers 401, so the token form can appear. */
export function onUnauthorized(fn: Listener): () => void {
  unauthorizedListeners.add(fn);
  return () => unauthorizedListeners.delete(fn);
}

type Params = Record<string, string | number | null | undefined>;

async function get<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const query = qs.toString();
  const url = query ? `${path}?${query}` : path;
  return parse<T>(await fetch(url, { credentials: "same-origin", signal }));
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return parse<T>(
    await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

async function parse<T>(resp: Response): Promise<T> {
  await check(resp);
  return resp.json() as Promise<T>;
}

/** Throws an ApiError for a failed reply, telling the token form about a 401. */
async function check(resp: Response): Promise<void> {
  if (resp.status === 401) {
    unauthorizedListeners.forEach((fn) => fn());
  }
  if (!resp.ok) {
    let message = `${resp.status} ${resp.statusText}`;
    try {
      message = (await resp.json()).error ?? message;
    } catch {
      /* keep the HTTP status text */
    }
    throw new ApiError(resp.status, message);
  }
}

/** `service` filter value for incidents that have no service (matches the API). */
export const NO_SERVICE = "-";
export const serviceLabel = (service: string) =>
  service && service !== NO_SERVICE ? service : "(không có service)";

export interface Filters {
  project?: string;
  service?: string;
  level?: string;
  outcome?: string;
  q?: string;
  since?: string;
}

export const api = {
  projects: (since: string, signal?: AbortSignal) => get<ProjectSummary[]>("/api/projects", { since }, signal),
  incident: (id: string, signal?: AbortSignal) =>
    get<IncidentDetail>(`/api/incidents/${encodeURIComponent(id)}`, {}, signal),
  activity: (f: Filters, bucket: string, signal?: AbortSignal) =>
    get<Activity>("/api/activity", { ...f, bucket }, signal),
  status: (signal?: AbortSignal) => get<Status>("/api/status", {}, signal),
  tickets: (f: Filters, signal?: AbortSignal) => get<TicketBoard>("/api/tickets", { ...f }, signal),
  /** One column: the page after `before` (a `next_before` cursor), or its newest `limit` tickets. */
  ticketColumn: (f: Filters, status: TicketStatus, page: { before?: string; limit?: number }, signal?: AbortSignal) =>
    get<TicketBoard>("/api/tickets", { ...f, status, ...page }, signal),
  ticketStats: (f: Filters, signal?: AbortSignal) => get<TicketStats>("/api/tickets/stats", { ...f }, signal),
  ticket: (fp: string, signal?: AbortSignal) =>
    get<TicketDetail>(`/api/tickets/${encodeURIComponent(fp)}`, {}, signal),
  /** Moves a ticket; a 409 ApiError carries the server's reason (stale `from` or a forbidden move). */
  setTicketStatus: (fp: string, from: TicketStatus, to: TicketStatus) =>
    post<TicketSummary>(`/api/tickets/${encodeURIComponent(fp)}/status`, { from, to }),
  channels: (signal?: AbortSignal) => get<ChannelList>("/api/channels", {}, signal),
  ticketSends: (fp: string, signal?: AbortSignal) =>
    get<{ items: ChannelSend[] }>(`/api/tickets/${encodeURIComponent(fp)}/sends`, {}, signal),
  /** Queues the ticket's newest occurrence for each channel; the watcher delivers it. */
  sendTicket: (fp: string, channels: string[]) =>
    post<{ items: ChannelSend[] }>(`/api/tickets/${encodeURIComponent(fp)}/sends`, { channels }),
  services: (signal?: AbortSignal) => get<ServiceList>("/api/services", {}, signal),
  /** Choose or rename services of one source; the watcher applies it within about 15 s. */
  chooseServices: (source: string, changes: ServiceChange[]) =>
    post<ServiceList>("/api/services", { source, changes }),
  /** The ticket's handoff bundle (Markdown) for occurrence `occ`, or its newest. */
  async handoffText(fp: string, occ: string | null): Promise<string> {
    const qs = occ ? `?occ=${encodeURIComponent(occ)}` : "";
    const resp = await fetch(`/api/tickets/${encodeURIComponent(fp)}/handoff${qs}`, { credentials: "same-origin" });
    await check(resp);
    return resp.text();
  },
  async login(token: string): Promise<boolean> {
    const resp = await fetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    return resp.ok;
  },
};
