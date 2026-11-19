#!/bin/sh
# First start of the local Postgres (docker compose): apply herjev's migrations, then give
# the two herjev roles the local passwords. Production passwords are set by the operator.
set -eu

for f in /herjev-migrations/*.sql; do
    psql -X -q -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
done

psql -X -q -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v watcher="$HERJEV_WATCHER_PASSWORD" -v portal="$HERJEV_PORTAL_PASSWORD" <<'SQL'
alter role herjev_watcher password :'watcher';
alter role herjev_portal password :'portal';
SQL
