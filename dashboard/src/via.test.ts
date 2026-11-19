import { describe, expect, it } from "vitest";

import type { TicketStatus, TicketSummary } from "./types";
import { sinceAlert } from "./via";

const NOW = 100_000;
const SENT = NOW - 3600;

function ticket(status: TicketStatus, over: Partial<TicketSummary> = {}): TicketSummary {
  return {
    fingerprint: "fp", status, updated_at: null, reopened_at: null, title: "t", project: "p", service: "s",
    latest_id: "1", incidents: 1, occurrences: 1, first_seen: SENT - 60, last_seen: SENT,
    latest_outcome: "notified", max_level: "error", ...over,
  };
}

describe("sinceAlert", () => {
  it("says nobody has picked up a new ticket", () => {
    expect(sinceAlert(ticket("new"), SENT, NOW)).toEqual({ tone: "", message: "Chưa ai nhận ticket này.", more: "" });
  });

  it("reports a ticket finished after the alert", () => {
    const r = sinceAlert(ticket("done", { updated_at: NOW - 600 }), SENT, NOW);
    expect(r.tone).toBe("ok");
    expect(r.message).toBe("Đã hoàn thành 10 phút trước, sau thông báo này. Nếu lỗi xảy ra lại, ticket sẽ tự mở lại.");
  });

  it("warns that a finished ticket reopened after the alert", () => {
    const r = sinceAlert(ticket("new", { updated_at: NOW - 120, reopened_at: NOW - 120 }), SENT, NOW);
    expect(r.tone).toBe("warn");
    expect(r.message).toBe("Ticket từng hoàn thành và đã tự mở lại 2 phút trước vì lỗi xảy ra tiếp.");
  });

  it("warns against duplicate work when someone moved the ticket after the alert", () => {
    const r = sinceAlert(ticket("inprogress", { updated_at: NOW - 1800 }), SENT, NOW);
    expect(r.tone).toBe("");
    expect(r.message).toBe(
      "Đã chuyển sang “Đang xử lý” 30 phút trước, sau thông báo này. Kiểm tra trước khi thao tác để tránh làm trùng.");
  });

  it("states the status when nothing changed since the alert", () => {
    const r = sinceAlert(ticket("review", { updated_at: SENT - 60 }), SENT, NOW);
    expect(r.message).toBe("Trạng thái hiện tại: Chờ review.");
  });

  it("notes that the error kept happening after the alert", () => {
    const r = sinceAlert(ticket("new", { last_seen: NOW - 300 }), SENT, NOW);
    expect(r.more).toBe("Lỗi vẫn xảy ra sau thông báo · lần cuối 5 phút trước.");
  });
});
