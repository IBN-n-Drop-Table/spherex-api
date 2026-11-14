-- Leaderboard of the game "Encuentra lo que se mueve" (POST /api/v1/game/finish).
--
-- HOW TO APPLY: Supabase Dashboard -> SQL Editor -> paste this file -> Run.
-- Safe to re-run (idempotent). The API needs no restart to start using it
-- only if it could already read the table at startup; otherwise restart it
-- (it checks the table once and falls back to memory if missing).
--
-- Security model (same as citizen_reports: open demo, no accounts):
--   * anyone (anon key) can READ the leaderboard;
--   * INSERT is allowed for anon, limited by column grants and CHECKs that
--     mirror app/schemas/game.py. The score itself is computed by the API,
--     but the anon key could insert rows directly: keep SUPABASE_KEY only on
--     the server (never in the frontend). For a stricter setup, run the
--     optional block at the end and give the API the service_role key.
--   * nobody can UPDATE or DELETE through the API.

create table if not exists public.game_scores (
    id            uuid        primary key default gen_random_uuid(),
    created_at    timestamptz not null default now(),
    nickname      text        not null check (nickname ~ '^[A-Za-z0-9ÁÉÍÓÚÜÑáéíóúüñ_-]{3,16}$'),
    score         integer     not null check (score between 0 and 5000),  -- 5 rounds x 1000
    hits          integer     not null check (hits between 0 and 5),
    rounds_played integer     not null check (rounds_played between 1 and 5),
    rounds_total  integer     not null check (rounds_total between 3 and 5),
    constraint game_scores_played_le_total check (rounds_played <= rounds_total),
    constraint game_scores_hits_le_played  check (hits <= rounds_played),
    constraint game_scores_score_le_played check (score <= 1000 * rounds_played)
);

comment on table public.game_scores is 'Leaderboard of the blink game; scores computed by the API.';

create index if not exists game_scores_rank_idx on public.game_scores (score desc, created_at asc);

alter table public.game_scores enable row level security;

revoke all on public.game_scores from anon, authenticated;
grant select on public.game_scores to anon, authenticated;
grant insert (nickname, score, hits, rounds_played, rounds_total)
    on public.game_scores to anon, authenticated;

drop policy if exists "game_scores: public read" on public.game_scores;
create policy "game_scores: public read"
    on public.game_scores for select
    to anon, authenticated
    using (true);

drop policy if exists "game_scores: public insert" on public.game_scores;
create policy "game_scores: public insert"
    on public.game_scores for insert
    to anon, authenticated
    with check (true);

-- Optional, stricter: only the API (service_role key in SUPABASE_KEY) can insert.
-- revoke insert on public.game_scores from anon, authenticated;
-- drop policy if exists "game_scores: public insert" on public.game_scores;
