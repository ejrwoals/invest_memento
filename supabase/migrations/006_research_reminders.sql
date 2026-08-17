-- 006_research_reminders.sql — AI 산출물(리서치·어드바이저), 리마인드
-- 근거: docs/dev/01-db-schema.md §3.7, §3.8
-- 선행: 005

begin;

-- ── research_items ────────────────────────────────────────────────────────
create table research_items (
  id              uuid primary key default gen_random_uuid(),
  note_id         uuid not null references notes on delete cascade,
  scenario_id     uuid references scenarios on delete set null,
  title           text not null,
  summary         text,
  url             text,
  published_at    date,
  relation        text not null check (relation in ('supports','contradicts','neutral')),
  relevance_score real,
  user_feedback   text not null default 'none'
                  check (user_feedback in ('adopted','dismissed','none')),
  created_at      timestamptz not null default now()
);

create index research_items_note_idx on research_items (note_id, created_at desc);

-- ── advisor_opinions ──────────────────────────────────────────────────────
create table advisor_opinions (
  id                     uuid primary key default gen_random_uuid(),
  note_id                uuid not null references notes on delete cascade,
  stance                 text not null check (stance in ('buy','add','hold','trim','sell')),
  rationale              text not null,
  referenced_scenarios   uuid[] not null default '{}',
  referenced_research    uuid[] not null default '{}',
  target_price           numeric(18,4),
  target_timeframe       text,
  invalidation_condition text,
  user_action            text not null default 'none'
                         check (user_action in
                         ('followed','partially_followed','ignored','none')),
  created_at             timestamptz not null default now()
);

-- ── reminder_rules ────────────────────────────────────────────────────────
create table reminder_rules (
  id                     uuid primary key default gen_random_uuid(),
  note_id                uuid not null references notes on delete cascade,
  type                   text not null check (type in
                         ('interval','galae_deadline','pending_judgment','event_triggered')),
  config                 jsonb not null default '{}',
  next_trigger_at        timestamptz,
  consecutive_unopened   int not null default 0,   -- 화면에 절대 노출하지 않음 (P5)
  current_interval_weeks int not null default 2,   -- 미열람 시 2배씩, 최대 12주
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create index reminder_rules_trigger_idx on reminder_rules (next_trigger_at);

create trigger reminder_rules_updated_at
  before update on reminder_rules
  for each row execute procedure extensions.moddatetime(updated_at);

-- ── notifications (발송 로그 — 하루 1통 묶음·미열람 감쇠의 근거) ──────────
create table notifications (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users on delete cascade,
  note_id       uuid references notes on delete cascade,
  kind          text not null,               -- 'reminder_digest', 'judgment_due', ...
  payload       jsonb not null default '{}',
  channel       text not null default 'email' check (channel in ('email')),
  scheduled_for timestamptz not null,
  sent_at       timestamptz,
  opened_at     timestamptz,                 -- 리마인드 상세 진입 시각
  created_at    timestamptz not null default now()
);

create index notifications_user_idx on notifications (user_id, scheduled_for);

-- ── RLS ───────────────────────────────────────────────────────────────────
alter table research_items enable row level security;
alter table advisor_opinions enable row level security;
alter table reminder_rules enable row level security;
alter table notifications enable row level security;

create policy research_items_owner on research_items
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy advisor_opinions_owner on advisor_opinions
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy reminder_rules_owner on reminder_rules
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy notifications_owner on notifications
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

commit;
