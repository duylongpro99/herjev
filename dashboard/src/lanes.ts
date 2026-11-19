import { byActivity, COLUMN_ORDER, TERMINAL } from "./tickets";
import type { Level, TicketColumn, TicketStatus, TicketSummary } from "./types";

export type LaneGroup = "project" | "service" | "level";

export const LANE_GROUPS: { value: LaneGroup; label: string }[] = [
  { value: "project", label: "Project" },
  { value: "service", label: "Service" },
  { value: "level", label: "Mức độ" },
];

export const LEVEL_RANK: Record<Level, number> = {
  trace: 0, debug: 1, info: 2, warn: 3, error: 4, fatal: 5, panic: 6,
};

/** Lanes grouped by level read worst first. */
const LEVELS_DESC = (Object.keys(LEVEL_RANK) as Level[]).sort((a, b) => LEVEL_RANK[b] - LEVEL_RANK[a]);

export interface Lane {
  key: string;
  name: string;
  /** The project, for service lanes. */
  sub: string;
  cells: Record<TicketStatus, TicketSummary[]>;
  open: number;
  closed: number;
  openByLevel: Partial<Record<Level, number>>;
  /** The worst level among open tickets, or null when nothing is open. */
  worst: Level | null;
}

export function laneOf(t: TicketSummary, by: LaneGroup): { key: string; name: string; sub: string } {
  if (by === "level") return { key: t.max_level, name: t.max_level, sub: "" };
  if (by === "project") return { key: t.project || "—", name: t.project || "—", sub: "" };
  const service = t.service || "—";
  return { key: `${t.project}/${service}`, name: service, sub: t.project };
}

function emptyCells(): Record<TicketStatus, TicketSummary[]> {
  return Object.fromEntries(COLUMN_ORDER.map((s) => [s, []])) as unknown as Record<TicketStatus, TicketSummary[]>;
}

/**
 * The board's tickets regrouped into lanes, one per project, service or level.
 * Lanes with the worst open level come first, then the busiest, then by name; level
 * lanes keep severity order. Each cell lists its worst ticket first. With `hideEmpty`, lanes with nothing open are left out
 * and counted in `hidden`.
 */
export function groupIntoLanes(
  columns: TicketColumn[],
  by: LaneGroup,
  opts: { hideEmpty?: boolean } = {},
): { lanes: Lane[]; hidden: number } {
  const lanes = new Map<string, Lane>();
  for (const col of columns) {
    for (const t of col.items) {
      const id = laneOf(t, by);
      let lane = lanes.get(id.key);
      if (!lane) {
        lane = { ...id, cells: emptyCells(), open: 0, closed: 0, openByLevel: {}, worst: null };
        lanes.set(id.key, lane);
      }
      const status = t.status;
      lane.cells[status].push(t);
      if (TERMINAL.has(status)) {
        lane.closed += 1;
      } else {
        lane.open += 1;
        lane.openByLevel[t.max_level] = (lane.openByLevel[t.max_level] ?? 0) + 1;
        if (!lane.worst || LEVEL_RANK[t.max_level] > LEVEL_RANK[lane.worst]) lane.worst = t.max_level;
      }
    }
  }
  // Inside a cell the worst ticket reads first, then the most recent.
  for (const lane of lanes.values()) {
    for (const s of COLUMN_ORDER) lane.cells[s].sort((a, b) => LEVEL_RANK[b.max_level] - LEVEL_RANK[a.max_level] || byActivity(a, b));
  }
  const rank = (l: Lane) => (l.worst ? LEVEL_RANK[l.worst] : -1);
  const all = [...lanes.values()].sort(
    by === "level"
      ? (a, b) => LEVELS_DESC.indexOf(a.key as Level) - LEVELS_DESC.indexOf(b.key as Level)
      : (a, b) => rank(b) - rank(a) || b.open - a.open || a.name.localeCompare(b.name) || a.sub.localeCompare(b.sub),
  );
  if (!opts.hideEmpty) return { lanes: all, hidden: 0 };
  const shown = all.filter((l) => l.open > 0);
  return { lanes: shown, hidden: all.length - shown.length };
}
