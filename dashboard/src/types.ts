// Shapes returned by the portal API (dashboard/worker/queries.ts).

export type Level = "trace" | "debug" | "info" | "warn" | "error" | "fatal" | "panic";

export type Outcome =
  | "notified"
  | "not_notified"
  | "rate_limited"
  | "suppressed"
  | "delivery_failed"
  | "no_route";

export interface ServiceSummary {
  service: string;
  count: number;
  max_level: Level;
  last_seen: number;
}

export interface ProjectSummary {
  project: string;
  services: ServiceSummary[];
  count: number;
  max_level: Level;
  last_seen: number;
}

export interface IncidentSummary {
  id: string;
  fingerprint: string;
  created_at: number;
  project: string;
  service: string;
  source: string;
  host: string;
  level: Level;
  title: string;
  count: number;
  notify: boolean;
  urgency: number;
  category: string;
  decision_reason: string;
  decider: string;
  outcome: Outcome;
  channels: string[];
  duration_ms: number | null;
}

export interface Step {
  seq: number;
  at: number;
  stage: string;
  message: string;
  body: string | null;
  elapsed_ms: number | null;
  blob: string | null;
}

export interface Analysis {
  summary: string;
  probable_causes: string[];
  impact: string;
  recommended_actions: string[];
  diagnostic_commands: string[];
  confidence: number;
  provider: string;
}

export interface IncidentState {
  level: Level;
  source: string;
  host: string;
  service: string;
  message: string;
  occurrences: number;
  window_seconds: number;
  samples: string[];
  context: string[];
}

export interface IncidentDetail extends IncidentSummary {
  analysis: Analysis | null;
  delivery_errors: string[];
  state: IncidentState;
  decision_raw: Record<string, unknown> | null;
  steps: Step[];
}

export interface Activity {
  since: number;
  until: number;
  bucket: number;
  series: { t: number; counts: Partial<Record<Level, number>> }[];
}

export interface Status {
  watcher: {
    updated_at?: number;
    uptime_seconds?: number;
    incidents?: number;
    alerts_sent?: number;
    sources?: string[];
  } | null;
  db_path: string;
  db_exists: boolean;
  /** Where the dashboard is reachable from outside, for shared links; unset when not configured. */
  public_url?: string | null;
}

/** Counts over every ticket the board's filters match, not just the loaded pages. */
export interface TicketStats {
  open: number;
  open_by_level: Partial<Record<Level, number>>;
  by_status: Record<TicketStatus, number>;
  /** Occurrences and incidents of open tickets in the last 24 hours. */
  occurrences_24h: number;
  incidents_24h: number;
}

export type TicketStatus = "new" | "inprogress" | "cancel" | "review" | "done";

/** Allowed moves per status, as the server enforces them. */
export type Transitions = Record<TicketStatus, TicketStatus[]>;

/** One fingerprint group: every incident that shares a fingerprint. */
export interface TicketSummary {
  fingerprint: string;
  status: TicketStatus;
  updated_at: number | null;
  reopened_at: number | null;
  title: string;
  project: string;
  service: string;
  latest_id: string;
  incidents: number;
  occurrences: number;
  first_seen: number;
  last_seen: number;
  latest_outcome: Outcome;
  max_level: Level;
}

export interface TicketColumn {
  status: TicketStatus;
  total: number;
  items: TicketSummary[];
  next_before: string | null;
}

export interface TicketBoard {
  columns: TicketColumn[];
  transitions: Transitions;
}

/** A channel the watcher built; `auto: false` ones only get sends from the portal. */
export interface Channel {
  name: string;
  type: string;
  auto: boolean;
}

export interface ChannelList {
  /** Whether the watcher's heartbeat is recent: sends are refused while it is offline. */
  online: boolean;
  channels: Channel[];
}

export type SendStatus = "pending" | "sending" | "sent" | "failed" | "expired";

/** One send of a ticket to a channel, asked for in the portal and delivered by the watcher. */
export interface ChannelSend {
  id: number;
  channel: string;
  incident_id: string;
  requested_at: number;
  status: SendStatus;
  error: string | null;
  finished_at: number | null;
}

export interface TicketDetail extends TicketSummary {
  /** The newest occurrences (up to 100), newest first. */
  items: IncidentSummary[];
  transitions: Transitions;
}

/** A service a source reports, as the watcher applies it. */
export interface Service {
  /** The service as the source reports it (a Loki `service_name` value). */
  label: string;
  name: string;
  /** The config's name, which a cleared portal name falls back to. */
  default_name: string;
  display_name: string | null;
  watched: boolean;
  /** Chosen or renamed here, but the source no longer reports it. */
  missing: boolean;
}

export interface SourceServices {
  name: string;
  /** True while the choice made here decides what is watched; false: the config does. */
  chosen: boolean;
  services: Service[];
}

export interface ServiceList {
  online: boolean;
  sources: SourceServices[];
}

export interface ServiceChange {
  label: string;
  watched?: boolean;
  display_name?: string | null;
}
