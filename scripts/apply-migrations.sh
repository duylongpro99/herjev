#!/usr/bin/env bash
# Apply every supabase/migrations/*.sql file, in name order, to one database.
#
#   scripts/apply-migrations.sh postgresql://postgres:postgres@127.0.0.1:5432/postgres
#
# For a fresh database (tests, local dev). Production is migrated by CI with
# `supabase db push`, which also records which migrations already ran.
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $0 <database-url>" >&2
    exit 2
fi

dir="$(cd "$(dirname "$0")/../supabase/migrations" && pwd)"
for f in "$dir"/*.sql; do
    psql "$1" -X -q -v ON_ERROR_STOP=1 -f "$f"
done
