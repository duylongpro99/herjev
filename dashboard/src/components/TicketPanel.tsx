import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, serviceLabel } from "../api";
import { ago, fullTime, shortTime } from "../format";
import { POLL_MS, usePoll } from "../hooks";
import { moveTargets, moveVerb, STATUS_LABELS, TERMINAL } from "../tickets";
import type { Analysis, IncidentDetail as Incident, TicketDetail, TicketStatus, TicketSummary } from "../types";
import { sinceAlert } from "../via";
import { OUTCOME_LABELS } from "../workflow";
import { ChannelSends } from "./ChannelSends";
import { Handoff } from "./Handoff";
import { Icon } from "./Icon";
import { IncidentDetail } from "./IncidentDetail";
import { LevelBadge } from "./LevelBadge";
import { StatusPill } from "./StatusPill";

interface Props {
  fp: string;
  /** Selected occurrence (incident id); the newest when unset. */
  occ: string | null;
  full: boolean;
  /** Opened from a link (an alert, a shared URL) rather than from the board. */
  via: boolean;
  /** Where shared links point; the page's own origin when unset. */
  publicUrl: string | null | undefined;
  /** Status to show, counting a move the server has not confirmed yet. */
  statusOf: (fp: string, fallback: TicketStatus) => TicketStatus;
  onMove: (ticket: TicketSummary, to: TicketStatus) => Promise<void>;
  onSelectOcc: (id: string) => void;
  onToggleFull: () => void;
  onClose: () => void;
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select, textarea, summary, [tabindex]:not([tabindex="-1"])';

/** "Sao chép" that says for a moment whether the clipboard took the text. */
function CopyButton({ text, label, className = "btn sm", children }: { text: string; label: string; className?: string; children?: React.ReactNode }) {
  const [state, setState] = useState<"" | "ok" | "fail">("");
  useEffect(() => {
    if (!state) return;
    const t = setTimeout(() => setState(""), 1600);
    return () => clearTimeout(t);
  }, [state]);
  // There is no clipboard API on plain http from a non-local host: say so instead of throwing.
  const copy = () =>
    (navigator.clipboard?.writeText(text) ?? Promise.reject()).then(() => setState("ok"), () => setState("fail"));
  return (
    <button
      type="button"
      className={className}
      aria-label={label}
      title={label}
      onClick={copy}
    >
      {children}
      <span aria-live="polite">{state === "ok" ? "Đã chép" : state === "fail" ? "Không chép được" : children ? "" : "Sao chép"}</span>
    </button>
  );
}

function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const word = pct >= 80 ? "cao" : pct >= 65 ? "trung bình" : "thấp";
  return (
    <div className="conf" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Độ tin cậy">
      Độ tin cậy
      <span className="conf-bar" aria-hidden="true">
        {Array.from({ length: 10 }, (_, i) => (
          <i key={i} className={i < Math.round(pct / 10) ? "on" : ""} />
        ))}
      </span>
      <strong>{pct}%</strong>
      <span>· {word}</span>
    </div>
  );
}

/** The LLM's analysis as steps to follow; the checklist is this reader's scratch state. */
function Runbook({ analysis, crit, latest }: { analysis: Analysis | null; crit: boolean; latest: boolean }) {
  const [checks, setChecks] = useState<boolean[]>([]);
  const a = analysis;
  const actions = a?.recommended_actions ?? [];
  const done = actions.filter((_, i) => checks[i]).length;
  return (
    <>
      <div className="rb-head">
        <h3>Runbook · phân tích của LLM</h3>
        {a && <Confidence value={a.confidence} />}
      </div>
      {!a ? (
        <p className="muted">Lần xảy ra này không có phân tích của LLM.</p>
      ) : (
        <>
          <ol className="runbook">
            <li className="rb-step">
              <h4>Tóm tắt</h4>
              <p className="lead">{a.summary || "Chưa có phân tích."}</p>
            </li>
            <li className="rb-step">
              <h4>Ảnh hưởng</h4>
              <p className={`impact${crit ? " crit" : ""}`}>{a.impact || "Chưa rõ."}</p>
            </li>
            {a.probable_causes.length > 0 && (
              <li className="rb-step">
                <h4>Nguyên nhân khả dĩ</h4>
                <ol className="causes">
                  {a.probable_causes.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ol>
              </li>
            )}
            {actions.length > 0 && (
              <li className="rb-step">
                <h4>
                  Việc cần làm
                  <span className="check-progress num">
                    {done}/{actions.length} đã xong
                  </span>
                </h4>
                <ul className="checks">
                  {actions.map((x, i) => (
                    <li key={i}>
                      <label>
                        <input
                          type="checkbox"
                          checked={!!checks[i]}
                          onChange={(e) => setChecks((prev) => Object.assign([...prev], { [i]: e.target.checked }))}
                        />
                        <span>{x}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </li>
            )}
            {a.diagnostic_commands.length > 0 && (
              <li className="rb-step">
                <h4>Lệnh chẩn đoán</h4>
                {a.diagnostic_commands.map((c, i) => (
                  <div key={i} className="cmd">
                    <code>{c}</code>
                    <CopyButton text={c} label={`Sao chép lệnh ${i + 1}`} />
                  </div>
                ))}
              </li>
            )}
          </ol>
          <p className="ai-note">
            Phân tích tự động ({a.provider}) từ log của lần xuất hiện {latest ? "mới nhất" : "đang chọn"}. Kiểm chứng trước
            khi chạy lệnh trên production.
          </p>
        </>
      )}
    </>
  );
}

/** What changed since the alert the reader came from. */
function ViaBanner({ ticket, occ, sentAt }: { ticket: TicketSummary; occ: string | null; sentAt: number | null }) {
  // Without the alert's time nothing reads as "after the alert": only the current state shows.
  const since = sinceAlert(ticket, sentAt ?? Number.POSITIVE_INFINITY);
  return (
    <div className={`via${since.tone ? ` ${since.tone}` : ""}`} role="status">
      <p className="via-src">
        Mở từ thông báo
        {occ && <> · lần xảy ra #{occ}</>}
        {sentAt != null && <> · gửi {ago(sentAt)}</>}
      </p>
      <p className="via-msg">{since.message}</p>
      {since.more && <p className="via-more">{since.more}</p>}
    </div>
  );
}

function CloseButton({ via, onClose }: { via: boolean; onClose: () => void }) {
  return via ? (
    <button type="button" className="icon-btn back-btn" data-close title="Về bảng ticket (Esc)" onClick={onClose}>
      <Icon name="close" />
      Bảng ticket
    </button>
  ) : (
    <button type="button" className="icon-btn" data-close aria-label="Đóng chi tiết" title="Đóng (Esc)" onClick={onClose}>
      <Icon name="close" />
    </button>
  );
}

function Body({ fp, occ, full, via, publicUrl, statusOf, onMove, onSelectOcc, onToggleFull, onClose }: Props) {
  const loadTicket = useCallback((signal: AbortSignal) => api.ticket(fp, signal), [fp]);
  const { data: t, error, loading, reload } = usePoll<TicketDetail>(loadTicket, fp);
  const selected = occ ?? t?.items[0]?.id ?? null;

  // The selected occurrence; one still collecting cooldown repeats is polled.
  // Until the ticket names its newest occurrence there is nothing to load.
  const loadInc = useCallback(
    (signal: AbortSignal) => (selected ? api.incident(selected, signal) : new Promise<Incident>(() => {})),
    [selected],
  );
  const [live, setLive] = useState(false);
  const inc = usePoll<Incident>(loadInc, selected ?? "", selected && live ? POLL_MS : 0, !selected);
  useEffect(() => setLive(inc.data?.outcome === "suppressed"), [inc.data?.outcome]);

  // The occurrence the alert named; picking another one in the list does not change it.
  const [opened] = useState({ fp, occ });
  const alertOcc = opened.fp === fp ? opened.occ : null;

  // The alert's time: from the occurrence list, or from the occurrence itself when it is
  // older than the list; unknown when the occurrence is gone.
  const [sentAt, setSentAt] = useState<number | null>(null);
  useEffect(() => {
    if (!via || !alertOcc || !t) return;
    const listed = t.items.find((i) => i.id === alertOcc);
    if (listed) return setSentAt(listed.created_at);
    const ctrl = new AbortController();
    api.incident(alertOcc, ctrl.signal).then((i) => setSentAt(i.created_at), () => setSentAt(null));
    return () => ctrl.abort();
  }, [via, alertOcc, t === null]);

  // Keyed and always inside `.d-tools`, so it survives the switch from loading to loaded
  // and keeps the focus the drawer gave it on opening.
  const closeBtn = <CloseButton key="close" via={via} onClose={onClose} />;

  if (loading && !t) {
    return (
      <>
        <div className="d-bar">
          <span className="crumb">Đang tải…</span>
          <span className="d-tools">{closeBtn}</span>
        </div>
        <div className="d-scroll panel-loading" aria-busy="true">
          <h2 id="d-title" className="sr-only">
            Đang tải ticket
          </h2>
          <span className="sk sk-badge" />
          <span className="sk sk-title" />
          <span className="sk sk-line short" />
          <span className="sk sk-line" />
          <span className="sk sk-line" />
        </div>
      </>
    );
  }

  // A link can outlive its ticket (retention purge, wrong fingerprint): say so and offer the board.
  if (!t) {
    const gone = error instanceof ApiError && error.status === 404;
    return (
      <>
        <div className="d-bar">
          <span className="crumb">{occ ? `Thông báo #${occ}` : "Ticket"}</span>
          <span className="d-tools">{closeBtn}</span>
        </div>
        <div className="d-scroll">
          <div className="gone">
            <h2 id="d-title">{gone ? "Không tìm thấy ticket này" : "Không tải được ticket"}</h2>
            <p>
              {gone
                ? `${occ ? `Thông báo về lần xảy ra #${occ}` : "Liên kết này"} trỏ tới một ticket không còn trong dữ liệu, có thể đã bị xoá theo hạn lưu trữ.`
                : (error?.message ?? "Lỗi không rõ.")}
            </p>
            <button type="button" className="btn primary" onClick={onClose}>
              Xem bảng ticket
            </button>
          </div>
        </div>
      </>
    );
  }

  const status = statusOf(fp, t.status);
  const current = { ...t, status };
  const targets = moveTargets(t.transitions, status);
  const base = (publicUrl || window.location.origin).replace(/\/+$/, "");
  const link = `${base}/tickets/${encodeURIComponent(fp)}${selected ? `?occ=${encodeURIComponent(selected)}` : ""}`;
  const latest = selected === t.items[0]?.id;

  return (
    <>
      <div className="d-bar">
        <span className="crumb">
          {t.project} / {serviceLabel(t.service)} · {STATUS_LABELS[status]}
        </span>
        <span className="d-tools">
          <CopyButton text={link} label="Chép link tới ticket" className="icon-btn">
            <Icon name="link" />
          </CopyButton>
          <button type="button" className="icon-btn hide-narrow" onClick={onToggleFull}
            aria-label={full ? "Thu về nửa màn hình" : "Mở toàn màn hình"} title={full ? "Thu về nửa màn hình" : "Mở toàn màn hình"}>
            <Icon name={full ? "collapse" : "expand"} />
          </button>
          {closeBtn}
        </span>
      </div>
      <div className="d-scroll">
        {via && <ViaBanner ticket={current} occ={alertOcc} sentAt={sentAt} />}
        <div className="d-eyebrow">
          <LevelBadge level={t.max_level} />
          <StatusPill status={status} />
          {t.reopened_at != null && (
            <span className="tag-reopen" title={`Mở lại lúc ${fullTime(t.reopened_at)}`}>
              Mở lại
            </span>
          )}
          <span className="fp mono">#{t.fingerprint}</span>
        </div>
        <h2 className="d-title" id="d-title">
          {t.title}
        </h2>
        <dl className="meta">
          <div>
            <dt>Project / service</dt>
            <dd>
              {t.project}/{serviceLabel(t.service)}
            </dd>
          </div>
          <div>
            <dt>Host</dt>
            <dd className="mono">{inc.data?.host || "—"}</dd>
          </div>
          <div>
            <dt>Nhóm lỗi</dt>
            <dd>{inc.data?.category || "—"}</dd>
          </div>
          <div>
            <dt>Số lần · sự cố</dt>
            <dd className="num">
              ×{t.occurrences} · {t.incidents} sự cố
            </dd>
          </div>
          <div>
            <dt>Lần cuối</dt>
            <dd title={fullTime(t.last_seen)}>{ago(t.last_seen)}</dd>
          </div>
          <div>
            <dt>Lần đầu</dt>
            <dd title={fullTime(t.first_seen)}>{ago(t.first_seen)}</dd>
          </div>
        </dl>
        <div className="d-actions" role="group" aria-label="Chuyển trạng thái">
          {TERMINAL.has(status) || !targets.length ? (
            <span className="closed-note">
              <Icon name="lock" />
              Ticket đã {status === "done" ? "hoàn thành" : "huỷ"} — trạng thái cuối, không chuyển bằng tay.
            </span>
          ) : (
            targets.map((to, i) => (
              <button key={to} type="button" className={`btn ${i === 0 ? "primary" : "ghost"}`}
                onClick={() => onMove(current, to).then(reload)}>
                {moveVerb(status, to)}
              </button>
            ))
          )}
        </div>

        {inc.data && inc.data.id === selected ? (
          <Runbook key={selected} analysis={inc.data.analysis} crit={t.max_level === "fatal" || t.max_level === "panic"} latest={latest} />
        ) : inc.error ? (
          <p className="state-msg">Không tải được lần xảy ra: {inc.error.message}</p>
        ) : (
          <div className="panel-loading" aria-busy="true">
            <span className="sk sk-line" />
            <span className="sk sk-line short" />
          </div>
        )}

        <Handoff key={`${fp}:${selected ?? ""}`} fp={fp} occ={selected} />

        <ChannelSends key={fp} fp={fp} />

        <section className="section">
          <h3>Lần xuất hiện ({t.incidents})</h3>
          <ul className="occ-list">
            {t.items.map((i) => (
              <li key={i.id}>
                <button type="button" className="occ-row" aria-pressed={i.id === selected} onClick={() => onSelectOcc(i.id)}>
                  <span className="mono num">{shortTime(i.created_at)}</span>
                  <LevelBadge level={i.level} />
                  <span className={`out-${i.outcome}`}>{OUTCOME_LABELS[i.outcome]?.toLowerCase() ?? i.outcome}</span>
                  <span className="mono num occ-count">×{i.count}</span>
                </button>
              </li>
            ))}
          </ul>
          {t.incidents > t.items.length && <p className="faint">Chỉ hiện {t.items.length} lần gần nhất trong {t.incidents}.</p>}
        </section>

        {inc.data && inc.data.id === selected && <IncidentDetail inc={inc.data} />}
      </div>
    </>
  );
}

/**
 * The open ticket as a modal drawer over the board: half the width on desktop, the whole
 * screen on phones or with `?panel=full`. Tab stays inside, Esc closes.
 */
export function TicketPanel(props: Props) {
  const ref = useRef<HTMLElement>(null);
  const { onClose } = props;

  useEffect(() => {
    document.body.classList.add("drawer-open");
    ref.current?.querySelector<HTMLElement>("[data-close]")?.focus();
    return () => document.body.classList.remove("drawer-open");
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector("dialog[open], .gate")) return; // the palette or the token gate owns the keys
      const drawer = ref.current;
      if (!drawer) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "Tab") {
        const items = [...drawer.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
        if (!items.length) return;
        const first = items[0], last = items[items.length - 1];
        const inside = drawer.contains(document.activeElement);
        if (e.shiftKey && (!inside || document.activeElement === first)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && (!inside || document.activeElement === last)) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <aside ref={ref} className={`drawer${props.full ? " full" : ""}`} role="dialog" aria-modal="true" aria-labelledby="d-title">
        <Body {...props} />
      </aside>
    </>
  );
}
