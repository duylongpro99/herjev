import postgres from "postgres";

export type Sql = postgres.Sql;

/** One client per request: Hyperdrive pools the real connections, so this one is cheap
 * and must be ended (``ctx.waitUntil(sql.end())``) once the reply is out. */
export function connect(url: string): Sql {
  return postgres(url, {
    max: 5,
    // The schema has no custom types; skipping the lookup saves a round trip per request.
    fetch_types: false,
    connect_timeout: 10,
    // count(*) and sum(integer) are bigint; every count here fits a JS number.
    types: {
      bigint: { to: 20, from: [20], serialize: (x: number) => String(x), parse: (x: string) => Number(x) },
    },
  });
}

/** Postgres error codes the API maps to a reply instead of a 500. */
export const UNDEFINED_TABLE = "42P01";
export const UNDEFINED_SCHEMA = "3F000";
export const UNDEFINED_COLUMN = "42703";
export const QUERY_CANCELED = "57014";
