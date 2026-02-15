-- Saturday v7.1 schema

create table if not exists public.saturday_profiles (
  user_id text primary key,
  city text null,
  zodiac text null,
  morning_time text null default '07:00', -- HH:MM
  last_morning_date date null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.saturday_contacts (
  owner_id text not null,
  alias text not null,
  target_user_id text not null,
  updated_at timestamptz not null default now(),
  primary key (owner_id, alias)
);

create table if not exists public.saturday_memories (
  id bigserial primary key,
  owner_id text not null,
  visibility text not null default 'private', -- private/shared
  content text not null,
  embedding vector null,
  created_at timestamptz not null default now()
);

create table if not exists public.saturday_tasks (
  id bigserial primary key,
  owner_id text not null,
  run_at timestamptz not null,
  message text not null,
  status text not null default 'pending', -- pending/done
  created_at timestamptz not null default now(),
  done_at timestamptz null
);

create table if not exists public.saturday_events (
  event_id text primary key,
  created_at timestamptz not null default now()
);

-- 建議：伺服器用 sb_secret_ key，可不開 RLS，最穩。
-- 如果你要開 RLS，請再另外做 policy（這版先以「穩定」為主）。

