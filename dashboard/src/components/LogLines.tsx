import type { IncidentState } from "../types";

/** Context lines in order; the incident's own (sample) lines are highlighted. */
export function LogLines({ state }: { state: IncidentState | null }) {
  const samples = new Set(state?.samples ?? []);
  const context = state?.context ?? [];
  // Older samples can fall out of the context ring buffer: show them first.
  const missing = (state?.samples ?? []).filter((s) => !context.includes(s));
  const lines = [...missing, ...context];
  if (!lines.length) return <p className="muted">Không có dòng log nào được lưu cho incident này.</p>;
  return (
    <pre className="log">
      {lines.map((line, i) => (
        <span key={i} className={`log-line${samples.has(line) ? " sample" : ""}`}>
          {line}
        </span>
      ))}
    </pre>
  );
}
