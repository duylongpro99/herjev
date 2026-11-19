import { fullTime, ms, URGENCY_LABELS } from "../format";
import type { IncidentDetail as Incident } from "../types";
import { buildWorkflow, verdictLine } from "../workflow";
import { LogLines } from "./LogLines";
import { WorkflowTimeline } from "./WorkflowTimeline";

/** One occurrence of a ticket: its verdict, log, decision timeline and delivery errors. */
export function IncidentDetail({ inc }: { inc: Incident }) {
  const window = inc.state?.window_seconds ?? 0;
  return (
    <article aria-label={`Lần xảy ra ${inc.id}`}>
      <div className="d-head">
        <div className="d-meta">
          <span className="num">×{inc.count}</span>
          <span>{fullTime(inc.created_at)}</span>
          <span>trong {window.toFixed(1)} s</span>
          <span className="mono">id {inc.id}</span>
          {inc.duration_ms != null && <span>xử lý {ms(inc.duration_ms)}</span>}
        </div>
        <div className={`d-verdict out-${inc.outcome}`}>{verdictLine(inc)}</div>
        {inc.outcome !== "suppressed" && (
          <div className="d-meta">
            <span>
              urgency {inc.urgency} ({URGENCY_LABELS[inc.urgency]})
            </span>
            <span>decider {inc.decider}</span>
            {inc.channels.length > 0 && <span>kênh {inc.channels.join(", ")}</span>}
          </div>
        )}
      </div>

      <section className="section">
        <h3>Log</h3>
        <LogLines state={inc.state} />
      </section>

      <section className="section">
        <h3>Quyết định</h3>
        {inc.steps.length ? (
          <WorkflowTimeline nodes={buildWorkflow(inc)} />
        ) : (
          <p className="muted">Lần xảy ra này được ghi trước khi herjev lưu từng bước nên không có timeline.</p>
        )}
      </section>

      {inc.delivery_errors?.length > 0 && (
        <section className="section">
          <h3>Lỗi gửi</h3>
          <ul>
            {inc.delivery_errors.map((e, i) => (
              <li key={i} className="mono">
                {e}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
