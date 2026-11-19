-- Services a person chose in the portal: which of a source's services the watcher
-- follows, and the name each one is shown under. One row per service someone touched;
-- `label` is the service as the source reports it (a Loki `service_name` value).
-- The portal writes these rows; the watcher reads them and applies them without a restart.
create table herjev.source_services (
    source text not null check (length(source) between 1 and 200),
    label text not null check (length(label) between 1 and 300),
    watched boolean not null default false,
    display_name text check (display_name is null or length(display_name) between 1 and 100),
    updated_at double precision not null default extract(epoch from now()),
    primary key (source, label)
);

grant select, insert, update, delete on herjev.source_services to herjev_watcher;
grant select on herjev.source_services to herjev_portal;
grant insert (source, label, watched, display_name),
      update (watched, display_name, updated_at) on herjev.source_services to herjev_portal;
