import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useMatch, useNavigate, useSearchParams } from "react-router-dom";

import { api, type Filters, onUnauthorized, serviceLabel } from "./api";
import { useTicketBoard } from "./board";
import { Board, useLaneView } from "./components/Board";
import { type Command, CommandPalette } from "./components/CommandPalette";
import { FilterBar, LEVEL_FILTERS, levelFilterOf } from "./components/FilterBar";
import { Icon } from "./components/Icon";
import { LiveStatus, WatcherStatus } from "./components/LiveStatus";
import { ServicesDialog } from "./components/ServicesDialog";
import { StatsPanel } from "./components/StatsPanel";
import { TicketPanel } from "./components/TicketPanel";
import { TokenGate } from "./components/TokenGate";
import { useDebounced, usePoll } from "./hooks";
import { groupIntoLanes, LANE_GROUPS, type LaneGroup } from "./lanes";
import type { TicketStats } from "./types";
import { OUTCOME_LABELS } from "./workflow";

const BUCKETS: Record<string, string> = { "1h": "2m", "24h": "30m", "7d": "6h" };
/** Search params that belong to the open ticket, not to the board's filters. */
const PANEL_PARAMS = ["occ", "panel"];
const LANE_UNITS: Record<LaneGroup, string> = { project: "project", service: "service", level: "mức" };
const IS_MAC = /mac|iphone|ipad/i.test(navigator.platform);

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
}

/** Never settles: a poll with nothing to ask for stays empty instead of sending a duplicate. */
const idle = () => new Promise<TicketStats>(() => {});

function Shell({ epoch }: { epoch: number }) {
  // One component for board and drawer, so opening a ticket keeps the board's state.
  const fp = useMatch("/tickets/:fp")?.params.fp;
  const legacyId = useMatch("/incidents/:id")?.params.id;
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const filters: Filters = {
    project: params.get("project") ?? "",
    service: params.get("service") ?? "",
    level: params.get("level") ?? "",
    outcome: params.get("outcome") ?? "",
    q: params.get("q") ?? "",
    since: params.get("since") || "24h",
  };
  const filterKey = JSON.stringify(filters) + epoch;
  const group = (LANE_GROUPS.some((g) => g.value === params.get("group")) ? params.get("group") : "project") as LaneGroup;
  const view = useLaneView(group);

  const update = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(params);
      for (const [k, v] of Object.entries(patch)) {
        if (v) next.set(k, v);
        else next.delete(k);
      }
      setParams(next, { replace: true, state: location.state });
    },
    [params, setParams, location.state],
  );

  // Search box: typed locally, written to the URL after a short pause.
  const [query, setQuery] = useState(filters.q ?? "");
  const debounced = useDebounced(query);
  useEffect(() => {
    if (debounced !== (params.get("q") ?? "")) update({ q: debounced });
  }, [debounced]);
  // Clearing the filters from elsewhere (the palette, the empty board) empties the box too.
  useEffect(() => {
    if (!params.get("q")) setQuery("");
  }, [params.get("q")]);

  const projects = usePoll((s) => api.projects(filters.since!, s), `${filters.since}${epoch}`);
  const status = usePoll((s) => api.status(s), `status${epoch}`);
  const activity = usePoll((s) => api.activity(filters, BUCKETS[filters.since!] ?? "1h", s), filterKey);
  // The level chips count every level, so their numbers ignore the level filter; the KPIs
  // follow it, which needs a second request only while a level is chosen.
  const allLevels = { ...filters, level: "" };
  const statsAll = usePoll((s) => api.ticketStats(allLevels, s), JSON.stringify(allLevels) + epoch);
  const statsLevel = usePoll((s) => (filters.level ? api.ticketStats(filters, s) : idle()), filterKey);
  const stats = filters.level ? statsLevel.data : statsAll.data;
  const board = useTicketBoard(filters, filterKey);

  const boardParams = new URLSearchParams(params);
  PANEL_PARAMS.forEach((k) => boardParams.delete(k));
  const boardSearch = boardParams.toString() ? `?${boardParams}` : "";
  const full = params.get("panel") === "full";
  // A ticket opened from an alert or a shared link, not from a card on this board.
  const via = !(location.state as { fromBoard?: boolean } | null)?.fromBoard;

  // Closing hands the keyboard back to the card; after a link it also pulses the card,
  // so the reader sees where the ticket lives on the board.
  const [reveal, setReveal] = useState<{ fp: string; seq: number; pulse: boolean } | null>(null);

  // Opening from the board pushes a history entry; closing goes back to it when there is
  // one, so Esc, the close button and the browser's back button all land on the board.
  // Opening another ticket while the drawer is open replaces the entry and keeps its state.
  const openNav = fp ? { replace: true, state: location.state } : { state: { fromBoard: true } };
  const open = (ticketFp: string) =>
    navigate({ pathname: `/tickets/${encodeURIComponent(ticketFp)}`, search: boardSearch }, openNav);
  const close = () => {
    if (fp) setReveal((r) => ({ fp, seq: (r?.seq ?? 0) + 1, pulse: via }));
    if (via) navigate({ pathname: "/", search: boardSearch }, { replace: true });
    else navigate(-1);
  };
  // Switching occurrence or drawer size replaces the entry and keeps its state.
  const panelParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true, state: location.state });
  };

  // Links from before the board point at one incident: open its ticket on that occurrence.
  const [legacyError, setLegacyError] = useState("");
  useEffect(() => {
    if (!legacyId) return;
    const ctrl = new AbortController();
    setLegacyError("");
    api
      .incident(legacyId, ctrl.signal)
      .then((inc) => {
        const next = new URLSearchParams(boardParams);
        next.set("occ", legacyId);
        navigate({ pathname: `/tickets/${encodeURIComponent(inc.fingerprint)}`, search: `?${next}` }, { replace: true });
      })
      .catch((e: Error) => {
        if (!ctrl.signal.aborted) setLegacyError(e.message);
      });
    return () => ctrl.abort();
  }, [legacyId, epoch]);

  // Ctrl/⌘K and "/" open the palette from anywhere outside a field.
  const [palette, setPalette] = useState(false);
  const [servicesOpen, setServicesOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const combo = (e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k";
      const slash = e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping(e.target);
      if (!combo && !slash) return;
      if (document.querySelector("dialog[open], .gate")) return;
      e.preventDefault();
      setPalette(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const filtersActive = !!(filters.level || filters.project || filters.service || filters.outcome || filters.q);
  const clearFilters = () => update({ level: "", project: "", service: "", outcome: "", q: "" });

  const commands: Command[] = [
    ...LEVEL_FILTERS.map((f) => ({
      group: "Lọc",
      label: f.value ? `Chỉ hiện ${f.label}` : "Hiện mọi mức độ",
      ctx: levelFilterOf(filters.level!)?.id === f.id ? "đang chọn" : undefined,
      icon: "filter" as const,
      run: () => update({ level: f.value }),
    })),
    ...(filtersActive ? [{ group: "Lọc", label: "Xoá bộ lọc", icon: "filter" as const, run: clearFilters }] : []),
    ...LANE_GROUPS.map((g) => ({
      group: "Bảng",
      label: `Nhóm lane theo ${g.label.toLowerCase()}`,
      ctx: group === g.value ? "đang chọn" : undefined,
      icon: "board" as const,
      run: () => update({ group: g.value === "project" ? "" : g.value }),
    })),
    {
      group: "Bảng",
      label: view.hideEmpty ? "Hiện lane trống" : "Ẩn lane trống",
      icon: "board",
      run: () => view.setHideEmpty(!view.hideEmpty),
    },
    { group: "Nguồn", label: "Chọn và đổi tên service được theo dõi", icon: "filter", run: () => setServicesOpen(true) },
  ];
  const tickets = useMemo(() => board.columns?.flatMap((c) => c.items) ?? [], [board.columns]);

  const lanes = useMemo(() => (board.columns ? groupIntoLanes(board.columns, group).lanes : []), [board.columns, group]);
  const openLanes = lanes.filter((l) => l.open > 0);
  const hot = openLanes.filter((l) => l.worst === "fatal" || l.worst === "panic").length;
  const openTotal = stats?.open ?? openLanes.reduce((a, l) => a + l.open, 0);
  const summary = board.columns
    ? `${openTotal} ticket đang mở trên ${openLanes.length} ${LANE_UNITS[group]}` +
      (hot ? ` · ${hot} lane có fatal/panic` : "") +
      ". Lane nóng nhất nằm trên cùng."
    : board.error
      ? "Không tải được dữ liệu."
      : "Đang tải dữ liệu…";

  const scope = [
    filters.level && (levelFilterOf(filters.level)?.label ?? filters.level),
    filters.project && `project ${filters.project}`,
    filters.service && `service ${serviceLabel(filters.service)}`,
    filters.outcome && (OUTCOME_LABELS[filters.outcome as keyof typeof OUTCOME_LABELS] ?? filters.outcome).toLowerCase(),
    filters.q && `“${filters.q}”`,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="page">
      <header className="top">
        <div className="top-title">
          <div className="brand">
            <b>herjev</b>
            <span aria-hidden="true">/</span>
            <span>Sự cố</span>
          </div>
          <h1>Bảng sự cố theo lane</h1>
          <p className="sub">{summary}</p>
        </div>
        <div className="top-end">
          <span className="top-status">
            <LiveStatus updatedAt={board.updatedAt} error={board.error} onRefresh={board.reload} />
            <WatcherStatus status={status.data} />
          </span>
          <button type="button" className="btn" aria-haspopup="dialog" onClick={() => setServicesOpen(true)}>
            <Icon name="filter" />
            Service
          </button>
          <button
            type="button"
            className="search-btn"
            aria-keyshortcuts="Meta+K Control+K /"
            aria-haspopup="dialog"
            onClick={() => setPalette(true)}
          >
            <Icon name="search" />
            <span className="txt">Tìm ticket hoặc lệnh</span>
            <span className="keys" aria-hidden="true">
              <kbd>{IS_MAC ? "⌘" : "Ctrl"}</kbd>
              <kbd>K</kbd>
            </span>
          </button>
        </div>
      </header>

      <FilterBar
        level={filters.level!}
        project={filters.project!}
        service={filters.service!}
        outcome={filters.outcome!}
        since={filters.since!}
        query={query}
        projects={projects.data}
        openByLevel={statsAll.data?.open_by_level ?? null}
        onChange={update}
        onQuery={setQuery}
      />

      <StatsPanel stats={stats} status={status.data} activity={activity.data} since={filters.since!} scope={scope} />

      <Board
        state={board}
        group={group}
        onGroup={(g) => update({ group: g === "project" ? "" : g })}
        view={view}
        search={boardSearch}
        openFp={fp}
        openNav={openNav}
        reveal={reveal}
        filtersActive={filtersActive}
        onClearFilters={clearFilters}
        onOpen={open}
      />

      {fp && (
        <TicketPanel
          key={`${fp}:${epoch}`}
          fp={fp}
          occ={params.get("occ")}
          full={full}
          via={via}
          publicUrl={status.data?.public_url}
          statusOf={board.statusOf}
          onMove={board.move}
          onSelectOcc={(id) => panelParam("occ", id)}
          onToggleFull={() => panelParam("panel", full ? "" : "full")}
          onClose={close}
        />
      )}
      {legacyId && (
        <div className="toast" role="status">
          <span>{legacyError ? `Không mở được incident ${legacyId}: ${legacyError}` : "Đang mở ticket…"}</span>
        </div>
      )}

      <ServicesDialog open={servicesOpen} onClose={() => setServicesOpen(false)} />

      <CommandPalette
        open={palette}
        onClose={() => setPalette(false)}
        commands={commands}
        tickets={tickets}
        onOpenTicket={(t) => open(t.fingerprint)}
      />

      {board.toast && (
        <div className="toast" role="alert">
          <Icon name="alert" play />
          <span>{board.toast}</span>
          <button type="button" className="btn" onClick={board.dismissToast}>
            Đóng
          </button>
        </div>
      )}
    </div>
  );
}

export function App() {
  const [needToken, setNeedToken] = useState(false);
  const [epoch, setEpoch] = useState(0);
  useEffect(() => onUnauthorized(() => setNeedToken(true)), []);
  return (
    <>
      <Shell epoch={epoch} />
      {needToken && (
        <TokenGate
          onDone={() => {
            setNeedToken(false);
            setEpoch((e) => e + 1);
          }}
        />
      )}
    </>
  );
}
