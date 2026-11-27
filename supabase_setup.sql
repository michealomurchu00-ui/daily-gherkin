-- ============================================================
-- Daily Gherkin — Supabase schema update
-- Run this in Supabase SQL Editor (paste all, run once)
-- ============================================================

-- 1. Add user_id column (links to Supabase Auth)
alter table leaderboard
  add column if not exists user_id uuid references auth.users(id);

-- 2. Unique constraint: one score per user per day
alter table leaderboard
  drop constraint if exists unique_user_day;
alter table leaderboard
  add constraint unique_user_day unique (user_id, day);

-- 3. Drop the open insert policy, replace with auth-only
drop policy if exists "Anyone can insert scores" on leaderboard;

create policy "Authenticated users insert own score"
  on leaderboard for insert
  to authenticated
  with check (auth.uid() = user_id);

-- Read stays open (anyone can see the leaderboard)
-- "Anyone can read scores" policy already exists — no change needed.

-- 4. All-time leaderboard RPC
-- Groups scores by user, uses their most recent display name.
create or replace function alltime_leaderboard()
returns table(player_name text, avg_score numeric, games_played bigint)
language sql security definer
as $$
  with latest_names as (
    select distinct on (user_id) user_id, player_name
    from leaderboard
    order by user_id, created_at desc
  ),
  aggregated as (
    select
      user_id,
      round(avg(score)::numeric, 0) as avg_score,
      count(*)                       as games_played
    from leaderboard
    group by user_id
  )
  select ln.player_name, a.avg_score, a.games_played
  from aggregated a
  join latest_names ln on ln.user_id = a.user_id
  order by a.avg_score desc
  limit 50;
$$;
