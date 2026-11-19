import { ms } from "../format";
import type { Step } from "../types";
import { type WorkflowNode, jevRows } from "../workflow";
import { JsonBlock } from "./JsonBlock";

const STATUS_TEXT = { ran: "", skipped: "không chạy", failed: "lỗi" } as const;

function StepRow({ step, hideMessage = false }: { step: Step; hideMessage?: boolean }) {
  return (
    <li className="step">
      {!hideMessage && <div className="step-line">
        <span className="step-stage mono">{step.stage}</span>
        <span className="step-msg">{step.message}</span>
        {step.elapsed_ms != null && <span className="mono num faint">{ms(step.elapsed_ms)}</span>}
      </div>}
      {step.body != null && (
        <details>
          <summary>{step.blob ? "nội dung tĩnh" : step.message.startsWith("→") ? "request" : step.message.startsWith("←") ? "response" : "chi tiết"}</summary>
          <JsonBlock text={step.body} />
        </details>
      )}
    </li>
  );
}

function JevTable({ steps }: { steps: Step[] }) {
  const rows = jevRows(steps);
  if (!rows.length) return null;
  return (
    <table className="jev">
      <colgroup>
        <col className="q" />
        <col className="a" />
        <col className="r" />
        <col />
      </colgroup>
      <thead>
        <tr>
          <th>Câu hỏi</th>
          <th>Trả lời</th>
          <th>Ngưỡng</th>
          <th>Kết quả</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.question}>
            <td className="mono">{r.question}</td>
            <td className="mono">{r.answer}</td>
            <td className="mono">{r.rule}</td>
            <td>{r.result}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function NodeBody({ node }: { node: WorkflowNode }) {
  if (!node.steps.length) return null;
  if (node.n === 2 && node.attempts.length) {
    return (
      <>
        {node.attempts.map((a, i) => (
          <div key={i} className={`attempt${a.failed ? " failed" : ""}`}>
            <div className="attempt-head">
              {i + 1}. {a.name}
              {a.failed ? " — lỗi, chuyển sang decider kế tiếp" : ""}
            </div>
            {a.name === "jev" && <JevTable steps={a.steps} />}
            <ul className="steps">
              {a.steps.map((s) => (
                <StepRow key={s.seq} step={s} />
              ))}
            </ul>
          </div>
        ))}
      </>
    );
  }
  // A single step that only repeats the node summary adds nothing but its body.
  const only = node.steps.length === 1 && node.steps[0].message === node.summary;
  if (only && node.steps[0].body == null) return null;
  return (
    <ul className="steps">
      {node.steps.map((s) => (
        <StepRow key={s.seq} step={s} hideMessage={only} />
      ))}
    </ul>
  );
}

export function WorkflowTimeline({ nodes }: { nodes: WorkflowNode[] }) {
  return (
    <ol className="timeline">
      {nodes.map((node) => (
        <li key={node.key} className={`node ${node.status}`}>
          <span className="node-num" aria-hidden="true">
            {node.n}
          </span>
          <div>
            <div className="node-head">
              <span className="node-title">
                {node.n}. {node.title}
              </span>
              <span className="faint mono">{node.key}</span>
              {STATUS_TEXT[node.status] && <span className={node.status === "failed" ? "error-text" : "faint"}>{STATUS_TEXT[node.status]}</span>}
              <span className="node-ms mono num">{ms(node.elapsedMs)}</span>
            </div>
            <div className="node-sum">{node.summary}</div>
            <NodeBody node={node} />
          </div>
        </li>
      ))}
    </ol>
  );
}
