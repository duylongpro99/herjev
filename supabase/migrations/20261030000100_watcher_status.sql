-- The watcher's latest status (the same body it writes to status.json), so the portal,
-- which runs on another machine, can show whether the watcher is alive.
create table herjev.watcher_status (
    id integer primary key check (id = 1),
    body jsonb not null,
    updated_at double precision not null
);

-- The default privileges from the first migration already cover this table; granting
-- explicitly keeps it correct even if this file runs as a different role.
grant select, insert, update, delete on herjev.watcher_status to herjev_watcher;
grant select on herjev.watcher_status to herjev_portal;
