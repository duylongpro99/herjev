-- Sends a person asked for in the portal: deliver a ticket's alert to a channel by hand.
-- The portal holds no channel secrets, so it only queues the request here; the watcher
-- claims it (pending → sending), delivers it with its own channels and writes the result
-- (sent or failed). A request left open too long becomes expired instead of firing late.
create table herjev.channel_sends (
    id bigint generated always as identity primary key,
    fingerprint text not null,
    incident_id text not null,      -- the ticket's newest occurrence when the send was asked for
    channel text not null,
    -- Set by the database, so the portal cannot choose when its request expires.
    requested_at double precision not null default extract(epoch from now()),
    status text not null default 'pending'
        check (status in ('pending', 'sending', 'sent', 'failed', 'expired')),
    error text,
    finished_at double precision
);
create index idx_channel_sends_fp on herjev.channel_sends (fingerprint, requested_at);
create index idx_channel_sends_open on herjev.channel_sends (requested_at)
    where status in ('pending', 'sending');

grant select, insert, update, delete on herjev.channel_sends to herjev_watcher;
-- The portal may only ask: it inserts the request columns, so every row it adds starts
-- pending at the database's time, and it can never mark a send done or change one.
grant select on herjev.channel_sends to herjev_portal;
grant insert (fingerprint, incident_id, channel) on herjev.channel_sends to herjev_portal;
