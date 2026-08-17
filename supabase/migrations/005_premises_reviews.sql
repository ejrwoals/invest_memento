-- 005_premises_reviews.sql — 지켜보는 수치, 근거 항목, 회고록, 근거 사후 검증
-- 근거: docs/dev/01-db-schema.md §3.5, §3.6
-- 선행: 004

begin;

-- ── watches (지켜보는 수치 — 판정하지 않는다) ─────────────────────────────
create table watches (
  id         uuid primary key default gen_random_uuid(),
  note_id    uuid not null references notes on delete cascade,
  provider   text not null,
  code       text not null,
  label      text not null,
  created_at timestamptz not null default now(),
  foreign key (provider, code) references series_catalog (provider, code)
);

-- ── premises (근거 항목 — 노트에 붙는다) ──────────────────────────────────
create table premises (
  id              uuid primary key default gen_random_uuid(),
  note_id         uuid not null references notes on delete cascade,
  statement       text not null,             -- 사용자가 말한 그대로. 다듬지 않는다
  position        int  not null default 0,   -- 논리 사슬 순서
  quoted_from     uuid references conversation_messages,
  linked_watch_id uuid references watches on delete set null,
  created_at      timestamptz not null default now()
);

create index premises_note_idx on premises (note_id, position);

-- ── reviews (회고록 — 갈래 단위) ──────────────────────────────────────────
create table reviews (
  id                   uuid primary key default gen_random_uuid(),
  galae_id             uuid not null unique references galae on delete cascade,
  realized_scenario_id uuid references scenarios,
  outcome              text not null check (outcome in ('confirmed','rejected')),
  logic_verdict        text check (logic_verdict in
                       ('logic_held','outcome_only','logic_only','both_wrong')),
  ai_draft             text,
  benchmark_comparison jsonb,
  user_narrative       text,                 -- [사용자] 저작
  status               text not null default 'draft_ready'
                       check (status in ('draft_ready','written')),
  drafted_at           timestamptz,
  written_at           timestamptz,
  created_at           timestamptz not null default now()
);

-- ── premise_verifications (회고 시 AI가 채운다 — 이력으로 쌓인다) ─────────
create table premise_verifications (
  id          uuid primary key default gen_random_uuid(),
  premise_id  uuid not null references premises on delete cascade,
  review_id   uuid not null references reviews on delete cascade,
  verdict     text not null check (verdict in ('happened','did_not_happen','unverifiable')),
  summary     text,
  sources     jsonb not null default '[]',
  verified_at timestamptz not null default now(),
  unique (premise_id, review_id)
);

-- ── RLS ───────────────────────────────────────────────────────────────────
alter table watches enable row level security;
alter table premises enable row level security;
alter table reviews enable row level security;
alter table premise_verifications enable row level security;

create policy watches_owner on watches
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy premises_owner on premises
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy reviews_owner on reviews
  for all to authenticated
  using (exists (select 1 from galae g join notes n on n.id = g.note_id
                 where g.id = galae_id and n.user_id = auth.uid()))
  with check (exists (select 1 from galae g join notes n on n.id = g.note_id
                      where g.id = galae_id and n.user_id = auth.uid()));

create policy premise_verifications_owner on premise_verifications
  for all to authenticated
  using (exists (select 1 from premises p join notes n on n.id = p.note_id
                 where p.id = premise_id and n.user_id = auth.uid()))
  with check (exists (select 1 from premises p join notes n on n.id = p.note_id
                      where p.id = premise_id and n.user_id = auth.uid()));

commit;
