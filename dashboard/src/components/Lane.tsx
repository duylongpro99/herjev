import { useDroppable } from "@dnd-kit/core";
import type { ReactNode } from "react";

import { type Lane as LaneData, type LaneGroup, LEVEL_RANK } from "../lanes";
import { COLUMN_ORDER, STATUS_LABELS, TERMINAL } from "../tickets";
import type { Level, TicketStatus, TicketSummary } from "../types";
import { Icon } from "./Icon";
import { LevelBadge } from "./LevelBadge";

/** Heat segments read worst first. */
const levelsDesc = (lane: LaneData) =>
  (Object.keys(lane.openByLevel) as Level[]).sort((a, b) => LEVEL_RANK[b] - LEVEL_RANK[a]);

/** Where the dragged card may land, as the lane's cells see it. */
export type DropState = "ok" | "no" | "home" | null;

/** A droppable cell's id: one status inside one lane. */
export const cellId = (status: TicketStatus, laneKey: string) => `${status}@${laneKey}`;

function Cell({ status, lane, drop, children }: { status: TicketStatus; lane: LaneData; drop: DropState; children: ReactNode }) {
  const items = lane.cells[status];
  const { setNodeRef, isOver } = useDroppable({
    id: cellId(status, lane.key),
    data: { lane: lane.key, status },
    disabled: drop === "no" || drop === null,
  });
  const cls = ["cell", `st-${status}`, TERMINAL.has(status) && "terminal", !items.length && "is-empty",
    drop === "ok" && "drop-ok", drop === "no" && "drop-no", drop === "ok" && isOver && "drop-over"].filter(Boolean).join(" ");
  return (
    <div ref={setNodeRef} className={cls} role="group" aria-label={`${STATUS_LABELS[status]}, ${items.length} ticket`}>
      <div className="cell-label" aria-hidden="true">
        {STATUS_LABELS[status]} <span className="count num">{items.length}</span>
      </div>
      {items.length > 0 && <div className="cell-sum num">{items.length} ticket</div>}
      {children}
    </div>
  );
}

interface Props {
  lane: LaneData;
  group: LaneGroup;
  collapsed: boolean;
  onToggle: () => void;
  /** Drop state per status while a card is dragged; null otherwise. */
  dropOf: (lane: string, status: TicketStatus) => DropState;
  renderCard: (t: TicketSummary) => ReactNode;
}

export function Lane({ lane, group, collapsed, onToggle, dropOf, renderCard }: Props) {
  const id = `lane-${lane.key.replace(/[^a-z0-9]/gi, "-")}`;
  const levels = levelsDesc(lane);
  return (
    <section className="lane" data-lane={lane.key} data-worst={lane.worst ?? "none"} data-collapsed={collapsed} aria-labelledby={id}>
      <div className="lane-head">
        <span className="strip" aria-hidden="true" />
        <button type="button" className="lane-toggle" id={id} aria-expanded={!collapsed} onClick={onToggle}>
          <Icon name="chevron-down" className="chev" size={16} />
          <span className="lane-name">
            {lane.sub && <small>{lane.sub}</small>}
            {group === "level" ? lane.name.toUpperCase() : lane.name}
          </span>
          <span className="lane-open num">
            <b>{lane.open}</b> mở
          </span>
        </button>
        <div className="lane-meta">
          {lane.worst ? <LevelBadge level={lane.worst} /> : <span className="lvl lvl-none">Yên</span>}
          <span className="heat" aria-hidden="true">
            {levels.length ? (
              levels.map((lv) => <span key={lv} style={{ flex: lane.openByLevel[lv], background: `var(--sev-${lv})` }} />)
            ) : (
              <span className="empty" />
            )}
          </span>
        </div>
        <span className="sr-only">
          {levels.length ? levels.map((lv) => `${lane.openByLevel[lv]} ${lv}`).join(", ") : "Không có ticket đang mở"}
        </span>
        {lane.closed > 0 && <span className="lane-closed num">{lane.closed} đã đóng</span>}
      </div>
      {!collapsed &&
        COLUMN_ORDER.map((status) => (
          <Cell key={status} status={status} lane={lane} drop={dropOf(lane.key, status)}>
            {lane.cells[status].map(renderCard)}
          </Cell>
        ))}
    </section>
  );
}
