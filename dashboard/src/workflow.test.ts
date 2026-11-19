import { describe, expect, it } from "vitest";

import type { Outcome, Step } from "./types";
import { buildWorkflow, jevRows, nodeFor, verdictLine } from "./workflow";

let seq = 0;
function s(stage: string, message: string, extra: Partial<Step> = {}): Step {
  seq += 1;
  return { seq, at: 1_700_000_000 + seq, stage, message, body: null, elapsed_ms: null, blob: null, ...extra };
}

function incident(steps: Step[], outcome: Outcome, decider = "jev:typesafe/jev-1.13", reason = "jev: notify p=0.93") {
  return { steps, outcome, decider, decision_reason: reason };
}

const interpreted = JSON.stringify({
  should_notify: "p=0.16 vs notify_threshold=0.5 → no",
  urgency: "score=0.2 (confidence=0.9) → 0 ignore",
  category: "security (confidence=0.7)",
  notify_before_policy: false,
});

describe("buildWorkflow", () => {
  it("lays out a notified incident as 7 ran nodes in pipeline order", () => {
    seq = 0;
    const nodes = buildWorkflow(
      incident(
        [
          s("aggregate", "error x2 from api"),
          s("decide", "trying jev"),
          s("decide:jev", "question definitions", { blob: "k1", body: "{}" }),
          s("decide:jev", "→ POST https://openrouter.ai/api/alpha/decisions"),
          s("decide:jev", "← HTTP 200 in 0.40s", { elapsed_ms: 400 }),
          s("decide:jev", "interpreted answers", { body: interpreted }),
          s("policy", "always_notify=['fatal', 'panic'] min_urgency=1: no change"),
          s("decision", "notify=True urgency=2"),
          s("llm:openai", "system prompt", { blob: "k2", body: "You are" }),
          s("llm:openai", "→ POST https://openrouter.ai/api/v1/chat/completions"),
          s("llm:openai", "← HTTP 200 in 1.20s", { elapsed_ms: 1200 }),
          s("analyze", "result from openai:m (confidence 0.70)"),
          s("route", "level=error → oncall"),
          s("deliver", "1/1 channel(s) ok"),
        ],
        "notified",
      ),
    );
    expect(nodes.map((n) => n.n)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(nodes.map((n) => n.status)).toEqual(Array(7).fill("ran"));
    expect(nodes[1].elapsedMs).toBe(400);
    expect(nodes[4].elapsedMs).toBe(1200);
    expect(nodes[4].steps.map((x) => x.stage)).toEqual(["llm:openai", "llm:openai", "llm:openai", "analyze"]);
    expect(nodes[1].attempts).toHaveLength(1);
    expect(nodes[1].summary).toBe("jev: notify p=0.93");
  });

  it("marks analyze/route/deliver as not run with a reason when not notified", () => {
    seq = 0;
    const nodes = buildWorkflow(
      incident(
        [
          s("aggregate", "error x1"),
          s("decide:jev", "interpreted answers", { body: interpreted }),
          s("policy", "no change"),
          s("decision", "notify=False"),
          s("outcome", "not notified: decider verdict is notify=False"),
        ],
        "not_notified",
      ),
    );
    expect(nodes.slice(0, 4).map((n) => n.status)).toEqual(["ran", "ran", "ran", "ran"]);
    for (const n of nodes.slice(4)) {
      expect(n.status).toBe("skipped");
      expect(n.summary).toMatch(/không chạy: .*không gửi/);
    }
    expect(nodes[3].steps.map((x) => x.stage)).toEqual(["decision", "outcome"]);
  });

  it("shows two attempts in the classify node when Jev fails and rules take over", () => {
    seq = 0;
    const nodes = buildWorkflow(
      incident(
        [
          s("aggregate", "error x1"),
          s("decide", "trying jev"),
          s("decide:jev", "→ POST https://openrouter.ai/api/alpha/decisions"),
          s("decide:jev", "← HTTP 503 in 0.10s", { elapsed_ms: 100, body: "unavailable" }),
          s("decide", "trying rules"),
          s("decide:rules", "error x1 → notify=True urgency=1"),
          s("decision", "notify=True urgency=1"),
          s("analyze", "skipped: urgency 0 below analyze_min_urgency 1"),
          s("route", "→ log"),
          s("deliver", "1/1 channel(s) ok"),
        ],
        "notified",
        "rules",
        "rules: error x1",
      ),
    );
    const classify = nodes[1];
    expect(classify.status).toBe("ran");
    expect(classify.attempts.map((a) => [a.name, a.failed])).toEqual([
      ["jev", true],
      ["rules", false],
    ]);
    expect(classify.attempts[0].steps.some((x) => x.message.includes("503"))).toBe(true);
    expect(nodes[2].status).toBe("skipped");
    expect(nodes[4].status).toBe("skipped");
    expect(nodes[4].summary).toBe("bỏ qua: urgency 0 below analyze_min_urgency 1");
  });

  it("runs only the aggregate node for a cooldown-suppressed incident", () => {
    seq = 0;
    const nodes = buildWorkflow(incident([s("aggregate", "suppressed error x3: cooldown 600s")], "suppressed", "cooldown"));
    expect(nodes[0].status).toBe("ran");
    for (const n of nodes.slice(1)) {
      expect(n.status).toBe("skipped");
      expect(n.summary).toMatch(/cooldown/);
    }
  });

  it("assigns an LLM decider's calls to the classify node", () => {
    expect(nodeFor("llm:gemini", false)).toBe(2);
    expect(nodeFor("llm:gemini", true)).toBe(5);
    expect(nodeFor("source:loki", false)).toBe(0);
  });
});

describe("jevRows / verdictLine", () => {
  it("splits interpreted answers into answer, rule and result", () => {
    seq = 0;
    const rows = jevRows([s("decide:jev", "interpreted answers", { body: interpreted })]);
    expect(rows[0]).toEqual({ question: "should_notify", answer: "p=0.16", rule: "notify_threshold=0.5", result: "no" });
    expect(rows[1].result).toBe("0 ignore");
    expect(rows).toHaveLength(3);
  });

  it("prefixes the reason with the outcome label", () => {
    expect(verdictLine({ outcome: "not_notified", decision_reason: "jev: notify p=0.16" })).toBe(
      "Không gửi: jev: notify p=0.16",
    );
  });
});
