import type { Sql } from "./db";
import { HttpError, badRequest, canTransition, isStatus, nowSeconds, ticketSummary } from "./queries";

/** Move a ticket from ``src`` to ``dst``: the portal's only write.
 *
 * ``src`` is the status the client saw; a different stored status means someone else
 * moved the ticket meanwhile, and the move is refused rather than applied. */
export async function setTicketStatus(sql: Sql, fp: string, src: string, dst: string) {
  for (const value of [src, dst]) if (!isStatus(value)) throw badRequest(`unknown status '${value}'`);
  return sql.begin(async (tx) => {
    // Two moves of one ticket run one after the other, so the second sees the first's status.
    await tx`select pg_advisory_xact_lock(hashtext(${fp}))`;
    const [found] = await tx`select 1 from herjev.incidents where fingerprint = ${fp} limit 1`;
    if (!found) throw new HttpError(404, "ticket not found");
    const [row] = await tx`select status from herjev.tickets where fingerprint = ${fp}`;
    const current = row ? row.status : "new";
    if (current !== src) throw new HttpError(409, `ticket is now '${current}', not '${src}'; reload and try again`);
    if (!canTransition(src, dst)) throw new HttpError(409, `cannot move a ticket from '${src}' to '${dst}'`);
    await tx`
      insert into herjev.tickets (fingerprint, status, updated_at) values (${fp}, ${dst}, ${nowSeconds()})
      on conflict (fingerprint) do update set status = excluded.status, updated_at = excluded.updated_at`;
    // Read the reply before committing: a failure here rolls the move back, so the
    // client never sees an error for a move that was saved.
    const summary = await ticketSummary(tx as unknown as Sql, fp);
    if (!summary) throw new HttpError(500, `ticket ${fp} vanished during its move`);
    return summary;
  });
}
