import { NO_SERVICE, serviceLabel } from "../api";
import { OUTCOME_LABELS } from "../workflow";
import type { Level, Outcome, ProjectSummary } from "../types";
import { Icon } from "./Icon";

export const RANGES = ["1h", "24h", "7d"] as const;

/** Level chips: each maps to the API's comma-separated `level` filter. */
export const LEVEL_FILTERS: { id: string; label: string; value: string; levels: Level[] }[] = [
  { id: "all", label: "Tất cả", value: "", levels: [] },
  { id: "crit", label: "Panic + Fatal", value: "fatal,panic", levels: ["fatal", "panic"] },
  { id: "error", label: "Error", value: "error", levels: ["error"] },
  { id: "low", label: "Warn + Info", value: "warn,info", levels: ["warn", "info"] },
];

/** The chip a `level` param selects; a hand-written list no chip matches selects none. */
export const levelFilterOf = (level: string) => {
  const key = level.split(",").filter(Boolean).sort().join(",");
  return LEVEL_FILTERS.find((f) => [...f.levels].sort().join(",") === key);
};

export function RangePicker({ since, onChange }: { since: string; onChange: (v: string) => void }) {
  return (
    <span className="seg" role="group" aria-label="Khoảng thời gian">
      {RANGES.map((r) => (
        <button key={r} type="button" aria-pressed={since === r} onClick={() => onChange(r)}>
          {r}
        </button>
      ))}
    </span>
  );
}

interface Props {
  level: string;
  project: string;
  service: string;
  outcome: string;
  since: string;
  query: string;
  projects: ProjectSummary[] | null;
  /** Open tickets per level, ignoring the level filter, for the chip counts. */
  openByLevel: Partial<Record<Level, number>> | null;
  onChange: (patch: Record<string, string>) => void;
  onQuery: (q: string) => void;
}

export function FilterBar({ level, project, service, outcome, since, query, projects, openByLevel, onChange, onQuery }: Props) {
  const current = levelFilterOf(level);
  const services = projects?.find((p) => p.project === project)?.services ?? [];
  const active = !!(level || project || service || outcome || query);
  const count = (f: (typeof LEVEL_FILTERS)[number]) =>
    openByLevel == null
      ? ""
      : f.levels.length
        ? f.levels.reduce((a, lv) => a + (openByLevel[lv] ?? 0), 0)
        : Object.values(openByLevel).reduce((a, n) => a + (n ?? 0), 0);
  return (
    <div className="filters">
      <div className="filter-row">
        <span className="filters-label" id="chips-level">
          Mức độ
        </span>
        <div className="chips" role="group" aria-labelledby="chips-level">
          {LEVEL_FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className="chip"
              aria-pressed={current?.id === f.id}
              title="Số ticket đang mở"
              onClick={() => onChange({ level: f.value })}
            >
              <span className={`mk mk-${f.id}`} aria-hidden="true" />
              {f.label}
              <span className="chip-n num">{count(f)}</span>
            </button>
          ))}
        </div>
        <div className="filters-end">
          <RangePicker since={since} onChange={(v) => onChange({ since: v })} />
        </div>
      </div>
      <div className="filter-row">
        <span className="filters-label" id="chips-project">
          Project
        </span>
        <div className="chips" role="group" aria-labelledby="chips-project">
          <button type="button" className="chip" aria-pressed={!project} onClick={() => onChange({ project: "", service: "" })}>
            Tất cả
          </button>
          {projects?.map((p) => (
            <button
              key={p.project}
              type="button"
              className="chip"
              aria-pressed={project === p.project}
              title={`${p.count} incident trong khoảng thời gian đang chọn`}
              onClick={() => onChange({ project: project === p.project ? "" : p.project, service: "" })}
            >
              <span className={`mk mk-lv-${p.max_level}`} aria-hidden="true" />
              {p.project}
              <span className="chip-n num">{p.count}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="filter-row">
        {project && services.length > 0 && (
          <label className="field">
            <span className="filters-label">Service</span>
            <select className="select" value={service} onChange={(e) => onChange({ service: e.target.value })}>
              <option value="">tất cả</option>
              {services.map((s) => (
                <option key={s.service} value={s.service || NO_SERVICE}>
                  {serviceLabel(s.service)} ({s.count})
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span className="filters-label">Kết quả</span>
          <select className="select" value={outcome} onChange={(e) => onChange({ outcome: e.target.value })}>
            <option value="">tất cả</option>
            {(Object.keys(OUTCOME_LABELS) as Outcome[]).map((o) => (
              <option key={o} value={o}>
                {OUTCOME_LABELS[o].toLowerCase()}
              </option>
            ))}
          </select>
        </label>
        <label className="search">
          <Icon name="search" />
          <input
            className="input"
            type="search"
            placeholder="Tìm trong tiêu đề và log"
            aria-label="Tìm trong tiêu đề và log"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            onKeyDown={(e) => {
              // Esc on an empty box hands the keyboard back to the board.
              if (e.key === "Escape" && !query) e.currentTarget.blur();
            }}
          />
        </label>
        {active && (
          <div className="filters-end">
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                onQuery("");
                onChange({ level: "", project: "", service: "", outcome: "", q: "" });
              }}
            >
              Xoá bộ lọc
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
