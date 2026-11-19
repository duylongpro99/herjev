import { useEffect, useId, useRef, useState } from "react";

import { shortTime } from "../format";
import { readStore, writeStore } from "../store";
import type { Activity, Level, Status, TicketStats } from "../types";
import { Icon } from "./Icon";

/** Stacked bottom to top, the most severe at the baseline. */
const SERIES: { key: Level; label: string }[] = [
  { key: "panic", label: "Panic" },
  { key: "fatal", label: "Fatal" },
  { key: "error", label: "Error" },
  { key: "warn", label: "Warn" },
];
const RANGE_TITLE: Record<string, string> = { "1h": "1 giờ", "24h": "24 giờ", "7d": "7 ngày" };
const nf = new Intl.NumberFormat("vi-VN");

interface Bucket {
  t: number;
  counts: Record<string, number>;
  total: number;
}

const niceMax = (v: number) => {
  const step = v <= 20 ? 5 : v <= 50 ? 10 : v <= 100 ? 20 : 10 ** Math.floor(Math.log10(v));
  return Math.max(step, Math.ceil(v / step) * step);
};

function topRounded(x: number, y: number, w: number, h: number, r: number): string {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function Swatch({ level, hatch }: { level: Level; hatch: string }) {
  return level === "panic" ? (
    <svg className="sw" viewBox="0 0 12 12" aria-hidden="true">
      <rect width="12" height="12" rx="3" fill={`url(#${hatch})`} />
    </svg>
  ) : (
    <span className="sw" style={{ background: `var(--sev-${level})` }} aria-hidden="true" />
  );
}

/** Incidents per bucket as stacked bars, with a tooltip, arrow-key reading and a table view. */
function ActivityChart({ activity, since, hatch }: { activity: Activity; since: string; hatch: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [active, setActive] = useState(-1);
  const [spoken, setSpoken] = useState("");
  const [tipLeft, setTipLeft] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const buckets: Bucket[] = activity.series.map((b) => {
    const counts: Record<string, number> = {};
    let total = 0;
    for (const s of SERIES) {
      counts[s.key] = b.counts[s.key] ?? 0;
      total += counts[s.key];
    }
    return { t: b.t, counts, total };
  });
  const n = buckets.length;
  const range = (i: number) => `${shortTime(buckets[i].t)}–${shortTime(buckets[i].t + activity.bucket).slice(-5)}`;

  const { w: W, h: Hh } = size;
  const small = W < 560;
  const padL = 32, padR = 8, padT = 22, padB = 24;
  const iw = W - padL - padR, ih = Hh - padT - padB;
  const drawable = n > 0 && iw >= 120 && ih >= 40;
  const max = niceMax(Math.max(1, ...buckets.map((b) => b.total)));
  const slot = n ? iw / n : 0;
  const gap = small ? 2 : 4;
  const bw = Math.max(2, slot - gap);
  const every = small ? 6 : 3;
  // The tick next to the right-aligned "bây giờ" label is dropped when the two would touch.
  const crowdedEnd = every * slot < 84;

  const show = (i: number, announce: boolean) => {
    setActive(i);
    if (announce) {
      const b = buckets[i];
      setSpoken(`${range(i)}: ${SERIES.map((s) => `${s.label} ${b.counts[s.key]}`).join(", ")}, tổng ${b.total}`);
    }
  };
  // Place the tooltip beside the bucket, flipped to the left when it would overflow.
  useEffect(() => {
    if (active < 0 || !tipRef.current) return;
    const cx = padL + (active + 0.5) * slot;
    const tw = tipRef.current.offsetWidth;
    setTipLeft(Math.max(0, cx + 12 + tw > W ? cx - tw - 12 : cx + 12));
  }, [active, slot, W]);

  const at = active >= 0 && active < n ? buckets[active] : null;
  return (
    <>
      <div
        ref={ref}
        className="chart-wrap"
        tabIndex={0}
        role="group"
        aria-roledescription="biểu đồ cột chồng"
        aria-label={`Số incident theo mức độ trong ${RANGE_TITLE[since] ?? since} qua. Dùng phím mũi tên trái phải để xem từng khoảng.`}
        onPointerMove={(e) => {
          if (!drawable) return;
          const i = Math.floor((e.clientX - e.currentTarget.getBoundingClientRect().left - padL) / slot);
          if (i >= 0 && i < n) {
            if (i !== active) show(i, false);
          } else setActive(-1);
        }}
        onPointerLeave={(e) => {
          if (document.activeElement !== e.currentTarget) setActive(-1);
        }}
        onFocus={() => n && show(active >= 0 ? active : n - 1, true)}
        onBlur={() => setActive(-1)}
        onKeyDown={(e) => {
          if (!n) return;
          const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
          if (e.key === "Home") {
            e.preventDefault();
            show(0, true);
          } else if (e.key === "End") {
            e.preventDefault();
            show(n - 1, true);
          } else if (e.key === "Escape") {
            setActive(-1);
          } else if (d) {
            e.preventDefault();
            show(Math.min(n - 1, Math.max(0, (active < 0 ? n - 1 : active) + d)), true);
          }
        }}
      >
        {drawable && (
          <svg className="plot" aria-hidden="true" width={W} height={Hh}>
            <defs>
              <pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="6" height="6" fill="var(--sev-fatal)" />
                <rect width="3" height="6" fill="var(--sev-panic)" />
              </pattern>
            </defs>
            {[0, max / 2, max].map((v) => {
              const y = padT + ih - (v / max) * ih;
              return (
                <g key={v}>
                  <line className="grid" x1={padL} x2={W - padR} y1={y} y2={y} />
                  <text className="axis" x={padL - 8} y={y + 4} textAnchor="end">
                    {v}
                  </text>
                </g>
              );
            })}
            {buckets.map((b, i) => {
              const x = padL + i * slot + gap / 2;
              const segs = SERIES.filter((s) => b.counts[s.key] > 0);
              let y = padT + ih;
              const off = n - 1 - i;
              const last = i === n - 1;
              return (
                <g key={b.t}>
                  <rect className={`hit${i === active ? " on" : ""}`} x={padL + i * slot} y={padT} width={slot} height={ih} />
                  {segs.map((s, si) => {
                    const h = (b.counts[s.key] / max) * ih;
                    const top = si === segs.length - 1;
                    const hh = Math.max(1, h - (top ? 0 : 2)); // a 2px gap between stacked segments
                    y -= h;
                    const fill = s.key === "panic" ? `url(#${hatch})` : `var(--sev-${s.key})`;
                    return top ? (
                      <path key={s.key} d={topRounded(x, y, bw, hh, 3)} fill={fill} pointerEvents="none" />
                    ) : (
                      <rect key={s.key} x={x} y={y + 2} width={bw} height={hh} fill={fill} pointerEvents="none" />
                    );
                  })}
                  {off % every === 0 && !(crowdedEnd && off === every) && (
                    <text className="axis" x={x + bw / 2} y={Hh - 4} textAnchor={last ? "end" : "middle"}>
                      {last ? "bây giờ" : shortTime(b.t).slice(-5)}
                    </text>
                  )}
                  {last && (
                    <text className="now-label num" x={x + bw} y={padT + ih - (b.total / max) * ih - 6} textAnchor="end">
                      {b.total}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
        <div ref={tipRef} className={`tooltip${at ? " show" : ""}`} style={{ left: tipLeft, top: padT }} aria-hidden="true">
          {at && (
            <>
              <div className="t-head num">{range(active)}</div>
              {[...SERIES].reverse().map((s) => (
                <div key={s.key} className="row">
                  <Swatch level={s.key} hatch={hatch} />
                  <span>{s.label}</span>
                  <b>{at.counts[s.key]}</b>
                </div>
              ))}
              <div className="row total">
                <span />
                <span>Tổng</span>
                <b>{at.total}</b>
              </div>
            </>
          )}
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {spoken}
      </p>
      <div className="chart-foot">
        <p className="chart-hint">Di chuột hoặc dùng phím ← → để xem từng khoảng. Panic được tô sọc để phân biệt với fatal.</p>
        <details className="table-view">
          <summary>Xem dạng bảng</summary>
          <div className="table-scroll">
            <table className="data-table" aria-label="Số incident theo thời gian và mức độ">
              <thead>
                <tr>
                  <th scope="col">Thời gian</th>
                  {SERIES.map((s) => (
                    <th key={s.key} scope="col">
                      {s.label}
                    </th>
                  ))}
                  <th scope="col">Tổng</th>
                </tr>
              </thead>
              <tbody>
                {buckets
                  .map((b, i) => (
                    <tr key={b.t}>
                      <td>{range(i)}</td>
                      {SERIES.map((s) => (
                        <td key={s.key}>{b.counts[s.key]}</td>
                      ))}
                      <td>
                        <b>{b.total}</b>
                      </td>
                    </tr>
                  ))
                  .reverse()}
              </tbody>
            </table>
          </div>
        </details>
      </div>
    </>
  );
}

interface Props {
  stats: TicketStats | null;
  status: Status | null;
  activity: Activity | null;
  since: string;
  /** What the filters narrow the numbers to, e.g. "Error, project shop"; empty when unfiltered. */
  scope: string;
}

const COLLAPSED_KEY = "herjev.stats.collapsed";

/** The KPI row and, under it, the activity chart at full width; collapsible to give the board the screen. */
export function StatsPanel({ stats, status, activity, since, scope }: Props) {
  const [collapsed, setCollapsed] = useState<boolean>(() => readStore(COLLAPSED_KEY, false));
  const toggle = () => {
    setCollapsed((c) => {
      writeStore(COLLAPSED_KEY, !c);
      return !c;
    });
  };
  const hatch = `hatch-${useId().replace(/:/g, "")}`;
  const w = status?.watcher;
  const crit = stats ? (stats.open_by_level.panic ?? 0) + (stats.open_by_level.fatal ?? 0) : 0;
  const kpis = [
    {
      label: "Ticket đang mở",
      value: stats ? nf.format(stats.open) : "…",
      sub: stats
        ? `${stats.by_status.new} mới · ${stats.by_status.inprogress} đang xử lý · ${stats.by_status.review} chờ review`
        : "",
    },
    {
      label: "Fatal/panic đang mở",
      value: stats ? nf.format(crit) : "…",
      crit: crit > 0,
      sub: stats ? `${stats.open_by_level.panic ?? 0} panic · ${stats.open_by_level.fatal ?? 0} fatal` : "",
    },
    {
      label: "Lần xuất hiện 24 giờ (ticket mở)",
      value: stats ? nf.format(stats.occurrences_24h) : "…",
      sub: stats ? `trong ${nf.format(stats.incidents_24h)} đợt sự cố` : "",
    },
    {
      label: "Watcher hoạt động",
      value: w?.uptime_seconds != null ? nf.format(Math.floor(w.uptime_seconds / 3600)) : "—",
      unit: w?.uptime_seconds != null ? "giờ" : "",
      sub: w ? `${w.sources?.length ?? 0} nguồn log · ${nf.format(w.alerts_sent ?? 0)} cảnh báo đã gửi` : "Chưa có dữ liệu watcher",
      global: true,
    },
  ];
  return (
    <section className="stats" data-collapsed={collapsed} aria-labelledby="stats-title">
      <div className="section-head">
        <h2>
          <button
            type="button"
            className="stats-toggle"
            id="stats-title"
            aria-expanded={!collapsed}
            aria-controls="stats-body"
            title={collapsed ? "Mở rộng phần tình hình" : "Thu gọn để dành chỗ cho bảng ticket"}
            onClick={toggle}
          >
            Tình hình hiện tại
            <Icon name="chevron-down" className="chev" size={18} />
          </button>
        </h2>
        <span className="scope">
          {scope ? (
            <>
              Chỉ số ticket theo bộ lọc: <b>{scope}</b>
            </>
          ) : (
            "Chỉ số ticket: tất cả project và mức độ"
          )}
        </span>
      </div>
      <div className="stats-grid" id="stats-body" hidden={collapsed}>
        <dl className="kpis" aria-busy={!stats || undefined}>
          {kpis.map((k) => (
            <div key={k.label} className={`kpi${k.crit ? " crit" : ""}`}>
              <dt>{k.label}</dt>
              <dd className="big num">
                {k.value}
                {k.unit && <small>{k.unit}</small>}
              </dd>
              <dd className="sub">{k.sub}</dd>
              {k.global && <dd className="global">Toàn hệ thống</dd>}
            </div>
          ))}
        </dl>
        <div className="panel" role="group" aria-labelledby="chart-title">
          <div className="panel-head">
            <h3 id="chart-title">Hoạt động {RANGE_TITLE[since] ?? since} qua</h3>
            <ul className="legend">
              {SERIES.map((s) => (
                <li key={s.key}>
                  <Swatch level={s.key} hatch={hatch} />
                  {s.label}{" "}
                  <b className="num">{activity ? activity.series.reduce((a, b) => a + (b.counts[s.key] ?? 0), 0) : "…"}</b>
                </li>
              ))}
            </ul>
            <span className="scope">Theo bộ lọc đang chọn · giờ máy</span>
          </div>
          {activity ? (
            <ActivityChart activity={activity} since={since} hatch={hatch} />
          ) : (
            <div className="chart-wrap panel-loading" aria-busy="true" />
          )}
        </div>
      </div>
    </section>
  );
}
