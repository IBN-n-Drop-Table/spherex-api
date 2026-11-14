-- Citizen Science discovery reports + community feed.
--
-- Safe to re-run (idempotent). Paste it into Supabase Dashboard -> SQL Editor
-- -> Run, or apply it with `supabase db push`.
--
-- Security model (open hackathon demo, no user accounts):
--   * anyone (anon key) can READ the feed and INSERT a report;
--   * clients cannot set id / created_at / verifications_count (column grants);
--   * nobody can UPDATE or DELETE rows through the API;
--   * peer verification goes only through verify_citizen_report(), which does
--     an atomic `verifications_count + 1` (no read-modify-write race).
--
-- Limits mirror app/schemas/reports.py. Keep both in sync.

create table if not exists public.citizen_reports (
    id                  uuid        primary key default gen_random_uuid(),
    created_at          timestamptz not null default now(),

    discoverer          text        not null check (char_length(btrim(discoverer)) between 1 and 50),
    notes               text        check (char_length(notes) <= 1000),

    ra_deg              double precision not null check (ra_deg >= 0 and ra_deg < 360),
    dec_deg             double precision not null check (dec_deg between -90 and 90),
    target_name         text        check (char_length(target_name) between 1 and 100),
    pixel_x             integer     check (pixel_x >= 0),
    pixel_y             integer     check (pixel_y >= 0),

    classification      text        not null check (classification in (
                            'asteroid',
                            'brown_dwarf',
                            'high_proper_motion_star',
                            'variable_star',
                            'transient_anomaly',
                            'other'
                        )),
    anomaly_id          text        check (anomaly_id ~ '^[A-Za-z0-9_-]{1,64}$'),
    snapshot_url        text        check (snapshot_url ~* '^https?://' and char_length(snapshot_url) <= 2083),

    verifications_count integer     not null default 1 check (verifications_count >= 1),

    constraint citizen_reports_pixel_pair check ((pixel_x is null) = (pixel_y is null))
);

comment on table  public.citizen_reports is 'Citizen-science discovery reports (community feed).';
comment on column public.citizen_reports.anomaly_id is 'Optional link to an automated detection, e.g. anom_09.';
comment on column public.citizen_reports.verifications_count is 'Peer confirmations, including the discoverer (starts at 1).';

-- Feed: newest first, optionally filtered by target or classification.
create index if not exists citizen_reports_created_at_idx
    on public.citizen_reports (created_at desc);
create index if not exists citizen_reports_target_created_idx
    on public.citizen_reports (target_name, created_at desc);
create index if not exists citizen_reports_class_created_idx
    on public.citizen_reports (classification, created_at desc);
create index if not exists citizen_reports_anomaly_idx
    on public.citizen_reports (anomaly_id) where anomaly_id is not null;

-- --------------------------------------------------------------------------
-- Privileges + Row Level Security
-- --------------------------------------------------------------------------
alter table public.citizen_reports enable row level security;

revoke all on public.citizen_reports from anon, authenticated;
grant select on public.citizen_reports to anon, authenticated;
grant insert (discoverer, notes, ra_deg, dec_deg, target_name, pixel_x, pixel_y,
              classification, anomaly_id, snapshot_url)
    on public.citizen_reports to anon, authenticated;

drop policy if exists "citizen_reports: public read" on public.citizen_reports;
create policy "citizen_reports: public read"
    on public.citizen_reports for select
    to anon, authenticated
    using (true);

drop policy if exists "citizen_reports: public insert" on public.citizen_reports;
create policy "citizen_reports: public insert"
    on public.citizen_reports for insert
    to anon, authenticated
    with check (verifications_count = 1);

-- No UPDATE / DELETE policies on purpose.

-- --------------------------------------------------------------------------
-- Peer verification (atomic +1)
-- --------------------------------------------------------------------------
-- SECURITY DEFINER lets anon bump the counter without UPDATE rights on the
-- table. Returns the updated row, or no rows if the id does not exist.
create or replace function public.verify_citizen_report(report_id uuid)
returns setof public.citizen_reports
language sql
volatile
security definer
set search_path = ''
as $$
    update public.citizen_reports
       set verifications_count = verifications_count + 1
     where id = report_id
    returning *;
$$;

revoke all on function public.verify_citizen_report(uuid) from public;
grant execute on function public.verify_citizen_report(uuid) to anon, authenticated, service_role;

-- Optional: live feed updates in the frontend via Supabase Realtime.
-- alter publication supabase_realtime add table public.citizen_reports;
