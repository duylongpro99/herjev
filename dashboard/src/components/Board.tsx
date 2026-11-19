import {
  type Announcements,
  type CollisionDetection,
  DndContext,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  type KeyboardCoordinateGetter,
  MouseSensor,
  pointerWithin,
  rectIntersection,
  TouchSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import type { BoardState } from "../board";
import { groupIntoLanes, LANE_GROUPS, type LaneGroup, laneOf } from "../lanes";
import { readStore, writeStore } from "../store";
import { canMove, COLUMN_ORDER, STATUS_LABELS } from "../tickets";
import type { TicketColumn, TicketStatus, TicketSummary } from "../types";
import { ColumnHeads } from "./BoardColumn";
import { type DropState, Lane } from "./Lane";
import { CardOverlay, TicketCard } from "./TicketCard";

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/** Enter on a focused button or link already activates it. */
function isActivatable(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "BUTTON" || el.tagName === "A");
}

const dragged = (data: unknown) => (data as { ticket: TicketSummary } | undefined)?.ticket;
const cellStatus = (data: unknown) => (data as { status: TicketStatus } | undefined)?.status;

// The pointer decides when there is one; keyboard drags have none.
const collision: CollisionDetection = (args) => {
  const hits = pointerWithin(args);
  return hits.length ? hits : rectIntersection(args);
};

/**
 * Arrow keys step through the cells of the card's own lane that accept it, in status
 * order: left and right on wide screens, up and down where the cells stack.
 */
const laneCoordinates: KeyboardCoordinateGetter = (event, { context, currentCoordinates }) => {
  const step = event.code === "ArrowRight" || event.code === "ArrowDown" ? 1 : event.code === "ArrowLeft" || event.code === "ArrowUp" ? -1 : 0;
  const { collisionRect, droppableRects, droppableContainers } = context;
  if (!step || !collisionRect) return undefined;
  event.preventDefault();
  const cells = droppableContainers
    .getEnabled()
    .map((c) => ({ status: cellStatus(c.data.current), rect: droppableRects.get(c.id) }))
    .filter((c) => c.rect && c.status)
    .sort((a, b) => COLUMN_ORDER.indexOf(a.status!) - COLUMN_ORDER.indexOf(b.status!));
  // `over` and `collisionRect` lag a frame behind fast key presses; the coordinates do not.
  const cx = currentCoordinates.x + collisionRect.width / 2;
  const cy = currentCoordinates.y + collisionRect.height / 2;
  const at = cells.findIndex((c) => c.rect!.left <= cx && cx <= c.rect!.right && c.rect!.top <= cy && cy <= c.rect!.bottom);
  const target = cells[(at < 0 ? (step > 0 ? -1 : cells.length) : at) + step];
  if (!target) return currentCoordinates;
  const r = target.rect!;
  return { x: r.left + (r.width - collisionRect.width) / 2, y: r.top + Math.min(40, (r.height - collisionRect.height) / 2) };
};

const FRESH_MS = 2400;
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const NARROW = "(max-width: 960px)"; // the accordion breakpoint in board.css

/** A card's place inside the board, so scrolling alone never reads as movement. */
function placeOf(el: HTMLElement, board: HTMLElement): { x: number; y: number } {
  const r = el.getBoundingClientRect();
  const b = board.getBoundingClientRect();
  return { x: r.left - b.left, y: r.top - b.top };
}

/**
 * Tickets with activity since the previous load: new on the board, or seen again.
 * Older tickets arriving through "Tải thêm" are not news.
 */
function useFresh(columns: TicketColumn[] | null): ReadonlySet<string> {
  const seen = useRef<Map<string, number> | null>(null);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (!columns) {
      seen.current = null; // filters changed: the next board is a first load
      return;
    }
    const items = columns.flatMap((c) => c.items);
    const prev = seen.current;
    seen.current = new Map(items.map((t) => [t.fingerprint, t.last_seen]));
    if (!prev) return;
    const newest = Math.max(0, ...prev.values());
    const hits = items.filter((t) => t.last_seen > (prev.get(t.fingerprint) ?? newest)).map((t) => t.fingerprint);
    if (!hits.length) return;
    setFresh(new Set(hits));
    const timer = setTimeout(() => setFresh(new Set()), FRESH_MS);
    return () => clearTimeout(timer);
  }, [columns]);
  return fresh;
}

/**
 * Collapsed lanes and "Ẩn lane trống", remembered per browser. A lane the user never
 * toggled starts expanded, except on narrow screens where only the hottest lane does.
 */
export function useLaneView(group: LaneGroup) {
  const [toggled, setToggled] = useState<Record<string, boolean>>(() => readStore("herjev.lanes.collapsed", {}));
  const [hideEmpty, setHideEmptyState] = useState<boolean>(() => readStore("herjev.lanes.hideEmpty", true));
  const [narrow] = useState(() => window.matchMedia(NARROW).matches);
  const collapsed = (key: string, index: number) => toggled[`${group}:${key}`] ?? (narrow && index > 0);
  const setCollapsed = (key: string, value: boolean) =>
    setToggled((prev) => {
      const next = { ...prev, [`${group}:${key}`]: value };
      writeStore("herjev.lanes.collapsed", next);
      return next;
    });
  const setHideEmpty = (value: boolean) => {
    setHideEmptyState(value);
    writeStore("herjev.lanes.hideEmpty", value);
  };
  return { collapsed, setCollapsed, hideEmpty, setHideEmpty };
}

export type LaneView = ReturnType<typeof useLaneView>;

const label = (status: TicketStatus | undefined) => (status ? STATUS_LABELS[status] : "");

const announcements: Announcements = {
  onDragStart: ({ active }) =>
    `Đã nhấc ticket ${dragged(active.data.current)?.title ?? ""}, đang ở cột ${label(dragged(active.data.current)?.status)}.`,
  onDragOver: ({ over }) =>
    over ? `Ticket đang ở trên cột ${label(cellStatus(over.data.current))}.` : "Ticket không ở trên cột nào.",
  onDragEnd: ({ active, over }) =>
    over && cellStatus(over.data.current) !== dragged(active.data.current)?.status
      ? `Đã thả ticket vào cột ${label(cellStatus(over.data.current))}.`
      : "Đã thả ticket, ticket giữ nguyên cột.",
  onDragCancel: ({ active }) => `Đã huỷ kéo, ticket vẫn ở cột ${label(dragged(active.data.current)?.status)}.`,
};

const instructions = {
  draggable:
    "Nhấn Space để nhấc ticket. Khi đang kéo, dùng phím mũi tên để chọn cột trong cùng lane, nhấn Space để thả, hoặc Esc để huỷ.",
};

interface Props {
  state: BoardState;
  group: LaneGroup;
  onGroup: (group: LaneGroup) => void;
  /** Collapsed lanes and "Ẩn lane trống", owned by the page so the palette can change them. */
  view: LaneView;
  /** Search string for links into a ticket: the board's filters. */
  search: string;
  openFp: string | undefined;
  /** How a card link navigates: push from the board, replace while a panel is open. */
  openNav: { replace?: boolean; state: unknown };
  /** Bring this card into view and focus it, pulsing it when asked; `seq` repeats the request. */
  reveal: { fp: string; seq: number; pulse: boolean } | null;
  filtersActive: boolean;
  onClearFilters: () => void;
  onOpen: (fp: string) => void;
}

export function Board({ state, group, onGroup, view, search, openFp, openNav, reveal, filtersActive, onClearFilters, onOpen }: Props) {
  const { columns, transitions, move } = state;
  const [active, setActive] = useState<TicketSummary | null>(null);
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  // The release of a mouse drag would otherwise click the card it ends on.
  const justDragged = useRef(false);
  const boardRef = useRef<HTMLDivElement>(null);
  const places = useRef(new Map<string, { x: number; y: number }>());
  // The card just dropped is already where the pointer left it: it settles instead of sliding.
  const dropped = useRef<string | null>(null);
  const fresh = useFresh(columns);

  const all = useMemo(() => (columns ? groupIntoLanes(columns, group).lanes : []), [columns, group]);
  const shown = view.hideEmpty ? all.filter((l) => l.open > 0) : all;
  const hidden = all.length - shown.length;
  const isCollapsed = (key: string) => view.collapsed(key, all.findIndex((l) => l.key === key));

  // Cards that change place glide there from where they were; cards new to the board fade
  // in. The first load staggers every card in, top to bottom.
  useLayoutEffect(() => {
    const board = boardRef.current;
    if (!board) return;
    const motion = !active && !reducedMotion();
    const first = places.current.size === 0;
    const next = new Map<string, { x: number; y: number }>();
    board.querySelectorAll<HTMLElement>(".card[data-fp]").forEach((el, i) => {
      const fp = el.dataset.fp!;
      const now = placeOf(el, board);
      next.set(fp, now);
      if (!motion) return;
      const was = places.current.get(fp);
      if (first) {
        el.animate([{ transform: "translateY(6px)", opacity: 0 }, { transform: "none", opacity: 1 }], {
          duration: 260,
          delay: Math.min(i * 24, 360),
          easing: "cubic-bezier(0.2, 0, 0, 1)",
          fill: "backwards",
        });
      } else if (fp === dropped.current) {
        el.animate([{ transform: "scale(0.96)", opacity: 0.7 }, { transform: "none", opacity: 1 }], {
          duration: 180,
          easing: "cubic-bezier(0.2, 0, 0, 1)",
        });
      } else if (!was) {
        el.animate([{ transform: "translateY(-6px)", opacity: 0 }, { transform: "none", opacity: 1 }], {
          duration: 220,
          easing: "cubic-bezier(0.2, 0, 0, 1)",
        });
      } else if (Math.abs(was.x - now.x) > 1 || Math.abs(was.y - now.y) > 1) {
        el.animate([{ transform: `translate(${was.x - now.x}px, ${was.y - now.y}px)` }, { transform: "none" }], {
          duration: 260,
          easing: "cubic-bezier(0.2, 0, 0, 1)",
        });
      }
    });
    places.current = next;
    dropped.current = null;
  });

  // Reveal: open the card's lane, then scroll to it, focus its link and maybe pulse it.
  // Each reveal runs once: a later reload of the board must not scroll or pulse again.
  const pendingReveal = useRef<{ fp: string; pulse: boolean } | null>(null);
  const revealed = useRef(0);
  useEffect(() => {
    if (!reveal || !columns || reveal.seq === revealed.current) return;
    revealed.current = reveal.seq;
    const ticket = columns.flatMap((c) => c.items).find((t) => t.fingerprint === reveal.fp);
    // The ticket is not on this board (filtered out, or gone): the keyboard lands on the board.
    if (!ticket) {
      document.getElementById("board-title")?.focus({ preventScroll: true });
      return;
    }
    const key = laneOf(ticket, group).key;
    if (view.hideEmpty && !shown.some((l) => l.key === key)) view.setHideEmpty(false);
    if (isCollapsed(key)) view.setCollapsed(key, false);
    pendingReveal.current = { fp: reveal.fp, pulse: reveal.pulse };
  }, [reveal, columns === null]);
  useEffect(() => {
    const target = pendingReveal.current;
    const el = target && document.getElementById(`tk-${target.fp}`);
    if (!target || !el) return;
    pendingReveal.current = null;
    el.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    el.querySelector<HTMLElement>(".open-btn")?.focus({ preventScroll: true });
    if (!target.pulse) return;
    el.classList.remove("arrived");
    void el.offsetWidth; // restart the pulse
    el.classList.add("arrived");
    el.addEventListener("animationend", () => el.classList.remove("arrived"), { once: true });
  });

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 6 } }),
    // Enter on the focused card opens it, so only Space picks a card up.
    useSensor(KeyboardSensor, {
      keyboardCodes: { start: ["Space"], cancel: ["Escape"], end: ["Space", "Enter"] },
      coordinateGetter: laneCoordinates,
    }),
  );

  const activeLane = active ? laneOf(active, group).key : null;
  const dropOf = useCallback(
    (lane: string, status: TicketStatus): DropState => {
      if (!active) return null;
      if (lane !== activeLane) return "no";
      if (status === active.status) return "home";
      return canMove(transitions, active.status, status) ? "ok" : "no";
    },
    [active, activeLane, transitions],
  );

  const onDragStart = ({ active }: DragStartEvent) => setActive(dragged(active.data.current) ?? null);
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    setActive(null);
    justDragged.current = true;
    setTimeout(() => (justDragged.current = false));
    const ticket = dragged(active.data.current);
    const to = cellStatus(over?.data.current);
    const lane = (over?.data.current as { lane?: string } | undefined)?.lane;
    if (ticket && to && to !== ticket.status && lane === laneOf(ticket, group).key && canMove(transitions, ticket.status, to)) {
      dropped.current = ticket.fingerprint;
      move(ticket, to);
    }
  };

  // Keyboard: j/k move within a cell, h/l across the lane's cells, Shift+J/K across lanes,
  // Enter opens. The cursor is a fingerprint, so cards arriving from polling never shift it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey || active) return;
      if (document.querySelector("dialog[open], .drawer")) return; // the drawer and palette own the keys
      const rows = shown.filter((l) => !isCollapsed(l.key)).map((l) => COLUMN_ORDER.map((s) => l.cells[s]));
      if (!rows.length) return;
      if ("hjklJK".includes(e.key) && e.key.length === 1) {
        e.preventDefault();
        const current = cursor ?? openFp;
        let li = -1, si = 0, ri = 0;
        rows.forEach((cells, l) =>
          cells.forEach((items, s) => {
            const r = items.findIndex((t) => t.fingerprint === current);
            if (r >= 0) [li, si, ri] = [l, s, r];
          }),
        );
        const firstFilled = (l: number, prefer = 0) =>
          rows[l][prefer]?.length ? prefer : rows[l].findIndex((items) => items.length > 0);
        if (li < 0) {
          li = rows.findIndex((cells) => cells.some((items) => items.length));
          if (li < 0) return;
          si = firstFilled(li);
          ri = 0;
        } else if (e.key === "j" || e.key === "k") {
          ri = Math.min(rows[li][si].length - 1, Math.max(0, ri + (e.key === "j" ? 1 : -1)));
        } else if (e.key === "h" || e.key === "l") {
          const step = e.key === "l" ? 1 : -1;
          let next = si + step;
          while (next >= 0 && next < COLUMN_ORDER.length && !rows[li][next].length) next += step;
          if (next < 0 || next >= COLUMN_ORDER.length) return;
          si = next;
          ri = Math.min(ri, rows[li][si].length - 1);
        } else {
          const step = e.key === "J" ? 1 : -1;
          let next = li + step;
          while (next >= 0 && next < rows.length && firstFilled(next) < 0) next += step;
          if (next < 0 || next >= rows.length) return;
          li = next;
          si = firstFilled(li, si);
          ri = 0;
        }
        const fp = rows[li][si][ri].fingerprint;
        setCursor(fp);
        const el = document.getElementById(`tk-${fp}`);
        el?.scrollIntoView({ block: "nearest", inline: "nearest" });
        el?.querySelector<HTMLElement>(".open-btn")?.focus({ preventScroll: true });
      } else if (e.key === "Enter" && cursor && !isActivatable(e.target)) {
        onOpen(cursor);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const bar = (
    <div className="board-bar">
      <h2 id="board-title" tabIndex={-1}>Bảng ticket</h2>
      <div className="controls">
        <div role="group" aria-labelledby="group-label">
          <span className="seg-label" id="group-label">
            Nhóm theo
          </span>
          <span className="seg">
            {LANE_GROUPS.map((g) => (
              <button key={g.value} type="button" aria-pressed={group === g.value} onClick={() => onGroup(g.value)}>
                {g.label}
              </button>
            ))}
          </span>
        </div>
        <label className="switch">
          <input type="checkbox" checked={view.hideEmpty} onChange={(e) => view.setHideEmpty(e.target.checked)} /> Ẩn lane trống
        </label>
      </div>
    </div>
  );

  if (!columns) {
    return (
      <>
        {bar}
        {state.error ? (
          <div className="notice" role="alert">
            <strong>Không tải được bảng ticket</strong>
            {state.error.message}
            <br />
            <button type="button" className="btn" onClick={state.reload}>
              Thử lại
            </button>
          </div>
        ) : (
          <BoardSkeleton />
        )}
      </>
    );
  }

  const byService = group === "service";
  return (
    <>
      {bar}
      <main className="board-wrap" aria-labelledby="board-title">
        <DndContext
          sensors={sensors}
          collisionDetection={collision}
          accessibility={{ announcements, screenReaderInstructions: instructions }}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onDragCancel={() => setActive(null)}
        >
          <div
            ref={boardRef}
            className={`board${active ? " dragging" : ""}`}
            aria-describedby="board-help"
            onClickCapture={(e) => {
              if (justDragged.current) {
                e.preventDefault();
                e.stopPropagation();
              }
            }}
          >
            <ColumnHeads columns={columns} group={group} loadingMore={state.loadingMore} onLoadMore={state.loadMore} />
            {!all.length ? (
              <div className="notice">
                <strong>Không có ticket nào khớp bộ lọc</strong>
                {filtersActive ? "Thử bỏ bớt điều kiện lọc." : "Chưa có sự cố nào trong khoảng thời gian này."}
                {filtersActive && (
                  <>
                    <br />
                    <button type="button" className="btn" onClick={onClearFilters}>
                      Xoá bộ lọc
                    </button>
                  </>
                )}
              </div>
            ) : (
              !shown.length && (
                <div className="notice">
                  <strong>Không có ticket nào đang mở</strong>
                  Mọi lane đều đã xử lý xong. Tắt “Ẩn lane trống” để xem ticket đã đóng.
                </div>
              )
            )}
            {shown.map((lane) => (
              <Lane
                key={lane.key}
                lane={lane}
                group={group}
                collapsed={isCollapsed(lane.key)}
                onToggle={() => view.setCollapsed(lane.key, !isCollapsed(lane.key))}
                dropOf={dropOf}
                renderCard={(t) => (
                  <TicketCard
                    key={t.fingerprint}
                    ticket={t}
                    transitions={transitions}
                    to={{ pathname: `/tickets/${encodeURIComponent(t.fingerprint)}`, search }}
                    nav={openNav}
                    byService={byService}
                    selected={t.fingerprint === openFp}
                    cursor={t.fingerprint === cursor}
                    pending={state.pending.has(t.fingerprint)}
                    fresh={fresh.has(t.fingerprint)}
                    onMove={move}
                  />
                )}
              />
            ))}
            {hidden > 0 && (
              <p className="hidden-note">
                Đã ẩn {hidden} lane không có ticket đang mở.{" "}
                <button type="button" className="link-btn" onClick={() => view.setHideEmpty(false)}>
                  Hiện tất cả lane
                </button>
              </p>
            )}
          </div>
          <DragOverlay dropAnimation={null}>{active && <CardOverlay ticket={active} byService={byService} />}</DragOverlay>
        </DndContext>
        <p className="sr-only" id="board-help">
          Kéo thả thẻ giữa các cột trong cùng lane, hoặc dùng nút Chuyển trên mỗi thẻ. Bấm vào thẻ để mở chi tiết. Phím j k
          chọn thẻ trong ô, h l sang ô bên cạnh, Shift J K sang lane khác.
        </p>
      </main>
    </>
  );
}

/** The board's shape while its first load is running, so nothing jumps when it arrives. */
function BoardSkeleton() {
  return (
    <main className="board-wrap" aria-busy="true" aria-label="Đang tải bảng ticket">
      <div className="board">
        <div className="colhead">
          <div>Đang tải…</div>
          {COLUMN_ORDER.map((status) => (
            <div key={status}>
              <div className="name">{STATUS_LABELS[status]}</div>
            </div>
          ))}
        </div>
        {[0, 1].map((lane) => (
          <section key={lane} className="lane" data-worst="none" aria-hidden="true">
            <div className="lane-head">
              <span className="strip" />
              <span className="sk sk-line" />
            </div>
            {COLUMN_ORDER.map((status, i) => (
              <div key={status} className="cell">
                {Array.from({ length: Math.max(0, 2 - i - lane) }, (_, j) => (
                  <div key={j} className="card skeleton">
                    <span className="sk sk-badge" />
                    <span className="sk sk-line" />
                    <span className="sk sk-line short" />
                  </div>
                ))}
              </div>
            ))}
          </section>
        ))}
      </div>
    </main>
  );
}
