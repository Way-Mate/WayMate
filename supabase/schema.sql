-- Waymate database.  Supabase dashboard → SQL Editor → New query → paste this whole file → Run.
-- Every table starts with wm_ so it can sit next to the tables you already have (profiles, messages, waymate_* ...).

create table wm_profiles (
  id uuid primary key references auth.users on delete cascade,      -- same id as the Supabase Auth user
  name text not null check (char_length(name) between 2 and 80),
  gender text not null check (gender in ('M','F','O')),
  age int not null check (age between 18 and 120),                  -- adults only
  bio text not null default '' check (char_length(bio) <= 240),
  suspended boolean not null default false,                         -- set true to hide someone (reports do this automatically)
  created_at timestamptz not null default now()
);

-- One active route per person: user_id is the primary key, so saving a new route replaces the old one.
create table wm_posts (
  user_id uuid primary key references wm_profiles on delete cascade,
  from_station int not null,
  to_station int not null,
  path int[] not null,                                              -- station numbers passed, in travel order
  departure_at timestamptz not null,
  pref text not null default 'any' check (pref in ('any','male','female')),
  note text not null default '' check (char_length(note) <= 120),
  created_at timestamptz not null default now()
);
create index wm_posts_path_idx on wm_posts using gin (path);        -- fast "shares a station" lookups
create index wm_posts_departure_idx on wm_posts (departure_at);

create table wm_connections (
  id bigint generated always as identity primary key,
  requester uuid not null references wm_profiles on delete cascade,
  recipient uuid not null references wm_profiles on delete cascade,
  status text not null default 'pending' check (status in ('pending','accepted','declined','cancelled')),
  created_at timestamptz not null default now(),
  unique (requester, recipient),
  check (requester <> recipient)
);

create table wm_messages (
  id bigint generated always as identity primary key,
  sender uuid not null references wm_profiles on delete cascade,
  recipient uuid not null references wm_profiles on delete cascade,
  body text not null check (char_length(body) between 1 and 2000),
  created_at timestamptz not null default now()
);
create index wm_messages_pair_idx on wm_messages (sender, recipient, id);

create table wm_blocks (
  blocker uuid not null references wm_profiles on delete cascade,
  blocked uuid not null references wm_profiles on delete cascade,
  created_at timestamptz not null default now(),
  primary key (blocker, blocked),
  check (blocker <> blocked)
);

create table wm_reports (
  id bigint generated always as identity primary key,
  reporter uuid not null references wm_profiles on delete cascade,
  reported uuid not null references wm_profiles on delete cascade,
  reason text not null check (reason in ('fake_or_spam','harassment','inappropriate_content','unsafe_behaviour','other')),
  details text not null default '' check (char_length(details) <= 500),
  status text not null default 'open' check (status in ('open','reviewed','actioned')),
  created_at timestamptz not null default now(),
  check (reporter <> reported)
);

-- Security: the website never talks to the database directly — only backend.py does, using the service key
-- (which bypasses these rules). RLS ON with no policies means the public anon key can read/write NOTHING here.
alter table wm_profiles    enable row level security;
alter table wm_posts       enable row level security;
alter table wm_connections enable row level security;
alter table wm_messages    enable row level security;
alter table wm_blocks      enable row level security;
alter table wm_reports     enable row level security;

-- Permissions for the server's secret key (role "service_role").
-- New Supabase projects do NOT grant these automatically, so without this you would get "permission denied for table".
-- Only service_role is granted access; anon/authenticated get nothing, so the public key can't touch these tables.
grant usage on schema public to service_role;
grant select, insert, update, delete on
  wm_profiles, wm_posts, wm_connections, wm_messages, wm_blocks, wm_reports to service_role;
grant usage, select on sequence wm_connections_id_seq, wm_messages_id_seq, wm_reports_id_seq to service_role;
