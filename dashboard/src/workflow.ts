// Turns an incident's ordered trace steps into the 7 numbered pipeline nodes.

import type { IncidentDetail, Outcome, Step } from "./types";

export type NodeStatus = "ran" | "skipped" | "failed";

export interface Attempt {
  name: string;
  steps: Step[];
  failed: boolean;
}

export interface WorkflowNode {
  n: number;
  key: string;
  title: string;
  status: NodeStatus;
  summary: string;
  elapsedMs: number | null;
  steps: Step[];
  /** Only for the classify node: one entry per decider tried. */
  attempts: Attempt[];
}

export const NODE_TITLES: [string, string][] = [
  ["aggregate", "Gom nhóm"],
  ["decide", "Phân loại"],
  ["policy", "Chính sách"],
  ["decision", "Kết luận"],
  ["analyze", "Phân tích"],
  ["route", "Định tuyến"],
  ["deliver", "Gửi"],
];

export const OUTCOME_LABELS: Record<Outcome, string> = {
  notified: "Đã gửi",
  not_notified: "Không gửi",
  rate_limited: "Bị giới hạn tần suất",
  suppressed: "Bị chặn (cooldown)",
  delivery_failed: "Gửi thất bại",
  no_route: "Không có kênh phù hợp",
};

/** Node number (1-7) for a step, given whether the final decision was already made. */
export function nodeFor(stage: string, decided: boolean): number {
  if (stage === "aggregate") return 1;
  if (stage === "decide" || stage.startsWith("decide:")) return 2;
  if (stage === "policy") return 3;
  if (stage === "decision" || stage === "outcome") return 4;
  // An LLM call before the decision is the LLM decider, not the analyzer.
  if (stage.startsWith("llm:")) return decided ? 5 : 2;
  if (stage === "analyze") return 5;
  if (stage === "route") return 6;
  if (stage === "deliver") return 7;
  return 0;
}

const SKIPPED_RE = /^skipped\b/i;
const HTTP_ERROR_RE = /← HTTP [45]\d\d/;
const TRYING_RE = /^trying (\S+)/;

function attemptsOf(steps: Step[], decider: string): Attempt[] {
  const attempts: Attempt[] = [];
  for (const s of steps) {
    const trying = s.stage === "decide" ? TRYING_RE.exec(s.message) : null;
    if (trying) {
      attempts.push({ name: trying[1], steps: [s], failed: false });
      continue;
    }
    if (!attempts.length) {
      const name = s.stage.includes(":") ? s.stage.split(":")[1] : decider || "decider";
      attempts.push({ name, steps: [], failed: false });
    }
    attempts[attempts.length - 1].steps.push(s);
  }
  // In a chain every attempt before the last one failed; the last failed too if the pipeline fell back.
  attempts.forEach((a, i) => {
    a.failed = i < attempts.length - 1 || decider === "fallback" || a.steps.some((s) => HTTP_ERROR_RE.test(s.message));
  });
  return attempts;
}

function notRunReason(n: number, outcome: Outcome, provider: string): string {
  if (outcome === "suppressed") return "không chạy: incident bị chặn bởi cooldown trước khi phân loại";
  if (n === 3) {
    return provider === "fallback"
      ? "không chạy: decider lỗi, dùng quy tắc dự phòng theo level"
      : "không chạy: decider này không áp dụng chính sách";
  }
  if (outcome === "not_notified") return "không chạy: kết luận là không gửi";
  if (outcome === "rate_limited") return "không chạy: đã chạm giới hạn số cảnh báo mỗi giờ";
  return "không chạy";
}

function sumElapsed(steps: Step[]): number | null {
  const timed = steps.filter((s) => s.elapsed_ms != null);
  return timed.length ? timed.reduce((acc, s) => acc + (s.elapsed_ms ?? 0), 0) : null;
}

export function buildWorkflow(inc: Pick<IncidentDetail, "steps" | "outcome" | "decider" | "decision_reason">): WorkflowNode[] {
  const byNode = new Map<number, Step[]>();
  let decided = false;
  for (const s of [...inc.steps].sort((a, b) => a.seq - b.seq)) {
    const n = nodeFor(s.stage, decided);
    if (s.stage === "decision") decided = true;
    if (!n) continue;
    if (!byNode.has(n)) byNode.set(n, []);
    byNode.get(n)!.push(s);
  }
  return NODE_TITLES.map(([key, title], i) => {
    const n = i + 1;
    const steps = byNode.get(n) ?? [];
    const attempts = n === 2 ? attemptsOf(steps, inc.decider) : [];
    const node: WorkflowNode = { n, key, title, status: "ran", summary: "", elapsedMs: sumElapsed(steps), steps, attempts };
    if (!steps.length) {
      node.status = "skipped";
      node.summary = notRunReason(n, inc.outcome, inc.decider);
      return node;
    }
    const meaningful = steps.filter((s) => !s.blob);
    const last = meaningful[meaningful.length - 1] ?? steps[steps.length - 1];
    if (steps.every((s) => SKIPPED_RE.test(s.message))) {
      node.status = "skipped";
      node.summary = "bỏ qua: " + last.message.replace(SKIPPED_RE, "").replace(/^[:\s]+/, "");
    } else if (n === 2) {
      node.status = attempts.length && attempts.every((a) => a.failed) ? "failed" : "ran";
      node.summary = node.status === "failed" ? "mọi decider đều lỗi" : inc.decision_reason || last.message;
    } else if (n === 5 && /^failed/i.test(last.message)) {
      node.status = "failed";
      node.summary = last.message;
    } else if (n === 7 && inc.outcome === "delivery_failed") {
      node.status = "failed";
      node.summary = last.message;
    } else {
      node.summary = n === 1 ? steps[0].message : last.message;
    }
    return node;
  });
}

/** One line for the detail header, e.g. "Không gửi: jev: notify p=0.16, …". */
export function verdictLine(inc: Pick<IncidentDetail, "outcome" | "decision_reason">): string {
  const label = OUTCOME_LABELS[inc.outcome] ?? inc.outcome;
  return inc.decision_reason ? `${label}: ${inc.decision_reason}` : label;
}

export interface JevRow {
  question: string;
  answer: string;
  rule: string;
  result: string;
}

/** Jev questions → raw answer → threshold/rule → result, from the "interpreted answers" step. */
export function jevRows(steps: Step[]): JevRow[] {
  const interpreted = steps.find((s) => s.stage === "decide:jev" && s.message.startsWith("interpreted"));
  if (!interpreted?.body) return [];
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(interpreted.body);
  } catch {
    return [];
  }
  const rows: JevRow[] = [];
  for (const [question, value] of Object.entries(data)) {
    if (typeof value !== "string") continue;
    const [left, ...rest] = value.split("→").map((p) => p.trim());
    const [answer, rule = ""] = left.split(/\s+vs\s+/);
    rows.push({ question, answer, rule, result: rest.join(" → ") });
  }
  return rows;
}
