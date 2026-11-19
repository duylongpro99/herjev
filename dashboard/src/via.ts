import { ago } from "./format";
import { STATUS_LABELS, TERMINAL } from "./tickets";
import type { TicketSummary } from "./types";

export interface SinceAlert {
  tone: "ok" | "warn" | "";
  message: string;
  /** Set when the error kept happening after the alert went out. */
  more: string;
}

/**
 * What changed on a ticket since the alert sent at `sentAt` (epoch seconds), for a reader
 * who opens the ticket from a chat message that may be minutes or days old.
 */
export function sinceAlert(t: TicketSummary, sentAt: number, now = Date.now() / 1000): SinceAlert {
  const changed = t.updated_at != null && t.updated_at > sentAt ? t.updated_at : null;
  const label = STATUS_LABELS[t.status];
  let tone: SinceAlert["tone"] = "";
  let message: string;
  if (changed != null && TERMINAL.has(t.status)) {
    tone = "ok";
    message = `Đã ${label.toLowerCase()} ${ago(changed, now)}, sau thông báo này.` +
      (t.status === "done" ? " Nếu lỗi xảy ra lại, ticket sẽ tự mở lại." : "");
  } else if (t.reopened_at != null && t.reopened_at > sentAt) {
    tone = "warn";
    message = `Ticket từng hoàn thành và đã tự mở lại ${ago(t.reopened_at, now)} vì lỗi xảy ra tiếp.`;
  } else if (changed != null) {
    message = `Đã chuyển sang “${label}” ${ago(changed, now)}, sau thông báo này. Kiểm tra trước khi thao tác để tránh làm trùng.`;
  } else if (t.status === "new") {
    message = "Chưa ai nhận ticket này.";
  } else {
    message = `Trạng thái hiện tại: ${label}.`;
  }
  const more = t.last_seen > sentAt ? `Lỗi vẫn xảy ra sau thông báo · lần cuối ${ago(t.last_seen, now)}.` : "";
  return { tone, message, more };
}
