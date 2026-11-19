import { useDraggable } from "@dnd-kit/core";
import { Link, type To } from "react-router-dom";

import { serviceLabel } from "../api";
import { ago, agoShort, fullTime, message } from "../format";
import { TERMINAL } from "../tickets";
import type { TicketStatus, TicketSummary, Transitions } from "../types";
import { LevelBadge } from "./LevelBadge";
import { MoveMenu } from "./MoveMenu";

/** Lanes by project or level mix services, so their cards tag the service up top;
 * service lanes already name it. */
function CardTop({ ticket, byService }: { ticket: TicketSummary; byService: boolean }) {
  return (
    <div className="card-top">
      <LevelBadge level={ticket.max_level} />
      {!byService && (
        <span className={`svc-tag${ticket.service ? "" : " none"}`} title={`Service: ${serviceLabel(ticket.service)}`}>
          {serviceLabel(ticket.service)}
        </span>
      )}
      {ticket.reopened_at != null && (
        <span className="reopen" title={`Mở lại lúc ${fullTime(ticket.reopened_at)}`}>
          Mở lại
        </span>
      )}
      <time
        className="age num"
        dateTime={new Date(ticket.last_seen * 1000).toISOString()}
        title={`Lần cuối: ${ago(ticket.last_seen)} · ${fullTime(ticket.last_seen)}`}
      >
        {agoShort(ticket.last_seen)}
      </time>
    </div>
  );
}

/** Service lanes name the service, so their cards name the project in the foot instead. */
const Where = ({ ticket, byService }: { ticket: TicketSummary; byService: boolean }) =>
  byService ? <span className="svc">{ticket.project}</span> : null;

/** The card as drawn under the pointer while it is dragged. */
export function CardOverlay({ ticket, byService }: { ticket: TicketSummary; byService: boolean }) {
  return (
    <article className={`card lv-${ticket.max_level} is-overlay`}>
      <CardTop ticket={ticket} byService={byService} />
      <span className="msg">{message(ticket.title)}</span>
      <div className="card-foot">
        <Where ticket={ticket} byService={byService} />
        <span className="occ num">×{ticket.occurrences}</span>
      </div>
    </article>
  );
}

interface Props {
  ticket: TicketSummary;
  transitions: Transitions | undefined;
  to: To;
  nav: { replace?: boolean; state: unknown };
  byService: boolean;
  selected: boolean;
  cursor: boolean;
  pending: boolean;
  /** New activity since the previous poll. */
  fresh: boolean;
  onMove: (ticket: TicketSummary, to: TicketStatus) => Promise<void>;
}

export function TicketCard({ ticket, transitions, to, nav, byService, selected, cursor, pending, fresh, onMove }: Props) {
  const fp = ticket.fingerprint;
  const locked = TERMINAL.has(ticket.status);
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: fp,
    disabled: locked || pending,
    data: { ticket },
  });
  const cls = ["card", `lv-${ticket.max_level}`, locked && "terminal", selected && "selected", cursor && "cursor",
    isDragging && "is-dragging", pending && "pending", fresh && "fresh"].filter(Boolean).join(" ");
  return (
    <article ref={setNodeRef} id={`tk-${fp}`} data-fp={fp} className={cls} {...listeners}>
      <CardTop ticket={ticket} byService={byService} />
      {/* The link's ::after covers the card; drag listeners sit on the card, so Space on the focused link picks it up. */}
      <Link
        className="open-btn"
        to={to}
        replace={nav.replace}
        state={nav.state}
        draggable={false}
        title={ticket.title}
        aria-current={selected ? "page" : undefined}
        aria-describedby={locked ? undefined : attributes["aria-describedby"]}
        aria-roledescription={locked ? undefined : "ticket kéo được"}
        aria-busy={pending || undefined}
      >
        <span className="msg">
          <span className="ul">{message(ticket.title)}</span>
        </span>
      </Link>
      <div className="card-foot">
        <Where ticket={ticket} byService={byService} />
        <span className="occ num" title={`${ticket.occurrences} lần xuất hiện · ${ticket.incidents} sự cố`}>
          ×{ticket.occurrences}
        </span>
        <MoveMenu
          status={ticket.status}
          transitions={transitions}
          disabled={pending}
          title={ticket.title}
          onMove={(st) => onMove(ticket, st)}
        />
      </div>
    </article>
  );
}
