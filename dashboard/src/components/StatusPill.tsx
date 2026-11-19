import { STATUS_LABELS } from "../tickets";
import type { TicketStatus } from "../types";

export function StatusPill({ status }: { status: TicketStatus }) {
  return <span className={`status-pill st-${status}`}>{STATUS_LABELS[status]}</span>;
}
