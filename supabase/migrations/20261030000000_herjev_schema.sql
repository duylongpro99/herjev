-- herjev incident store: incidents, their decision trace, mutes and ticket status.
--
-- Everything lives in the `herjev` schema, which is never added to Supabase's exposed
-- API schemas, so the Data API (anon key) cannot reach it. Two login roles share it:
--   herjev_watcher  reads and writes every table (the watcher, the CLI)
--   herjev_portal   reads every table and writes only `tickets` (the portal Worker)
-- Passwords are set by the operator after the first migration, never here.
--
-- Migrations only add: the watcher that is still running while CI migrates must keep
-- working, so never rename or drop a column it reads in the same release.

create schema if not exists herjev;
revoke all on schema herjev from public;

-- Roles are cluster-wide, so a second database on the same server (tests create one per
-- test) must not fail on them.
do $$
begin
  if not exists (select from pg_roles where rolname = 'herjev_watcher') then
    create role herjev_watcher login;
  end if;
  if not exists (select from pg_roles where rolname = 'herjev_portal') then
    create role herjev_portal login;
  end if;
end $$;

alter role herjev_watcher set search_path = herjev;
alter role herjev_portal set search_path = herjev;
alter role herjev_portal set statement_timeout = '3s';

create table herjev.incidents (
    id text primary key,
    fingerprint text not null,
    created_at double precision not null,  -- epoch seconds: API cursors and replies use the number
    level text not null,
    source text,
    host text,
    service text,
    title text,
    count integer,
    notify boolean not null,
    urgency integer,
    category text,
    decision_reason text,
    decider text,
    analysis jsonb,
    channels jsonb,
    delivery_errors jsonb,
    state jsonb,
    project text not null,
    outcome text not null,
    decision_raw jsonb,
    duration_ms double precision
);
create index idx_incidents_created on herjev.incidents (created_at);
create index idx_incidents_fp on herjev.incidents (fingerprint);
create index idx_incidents_project on herjev.incidents (project, service, created_at);

create table herjev.incident_steps (
    incident_id text not null,
    seq integer not null,
    at double precision,
    stage text,
    message text,
    body text,
    elapsed_ms double precision,
    blob text,
    primary key (incident_id, seq)
);
-- Lets prune find orphaned blobs without scanning every step.
create index idx_incident_steps_blob on herjev.incident_steps (blob);

-- Static trace blocks (question sets, system prompts), stored once and referenced by key.
create table herjev.trace_blobs (
    key text primary key,
    body text
);

create table herjev.mutes (
    fingerprint text primary key,
    until double precision not null,
    reason text
);

-- Ticket status per fingerprint: the only table the portal writes.
create table herjev.tickets (
    fingerprint text primary key,
    status text not null check (status in ('new', 'inprogress', 'cancel', 'review', 'done')),
    updated_at double precision not null,
    reopened_at double precision
);
create index idx_tickets_status on herjev.tickets (status);

grant usage on schema herjev to herjev_watcher, herjev_portal;
grant select, insert, update, delete on all tables in schema herjev to herjev_watcher;
grant select on all tables in schema herjev to herjev_portal;
grant insert, update on herjev.tickets to herjev_portal;

-- Tables added by later migrations (run as the same migrating role) get the same split;
-- a later migration that lets the portal write a table grants it explicitly.
alter default privileges in schema herjev
    grant select, insert, update, delete on tables to herjev_watcher;
alter default privileges in schema herjev
    grant select on tables to herjev_portal;

-- anon/authenticated only exist on Supabase; plain Postgres (tests, CI) has neither.
do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select from pg_roles where rolname = r) then
      execute format('revoke all on schema herjev from %I', r);
      execute format('revoke all on all tables in schema herjev from %I', r);
    end if;
  end loop;
end $$;
