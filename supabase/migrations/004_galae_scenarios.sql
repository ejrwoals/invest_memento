-- 004_galae_scenarios.sql — 갈래, 시나리오, 확률 이력, 조건 수정 이력, 확률 자료
-- 근거: docs/dev/01-db-schema.md §3.4, §4.1, §4.3
-- 선행: 003

begin;

-- ── galae ─────────────────────────────────────────────────────────────────
create table galae (
  id          uuid primary key default gen_random_uuid(),
  note_id     uuid not null references notes on delete cascade,
  question    text not null,
  judge_kind  text check (judge_kind in ('date','range')),
  judge_start date,
  judge_end   date,                          -- null이면 미완성 노트 (리마인드 제외)
  status      text not null default 'open' check (status in ('open','judged')),
  position    int  not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check (judge_kind is null or judge_end is not null),
  check (judge_kind is distinct from 'range' or judge_start is not null)
);
-- ★ 판단 시점은 갈래에 하나뿐 — 시나리오에는 날짜 필드 자체가 없다.

create index galae_note_idx on galae (note_id);
create index galae_due_idx on galae (judge_end) where status = 'open';

create trigger galae_updated_at
  before update on galae
  for each row execute procedure extensions.moddatetime(updated_at);

create table galae_deadline_resets (
  id       uuid primary key default gen_random_uuid(),
  galae_id uuid not null references galae on delete cascade,
  from_end date not null,
  to_end   date not null,
  reason   text,
  reset_at timestamptz not null default now()
);

-- ── scenarios ─────────────────────────────────────────────────────────────
create table scenarios (
  id                 uuid primary key default gen_random_uuid(),
  galae_id           uuid not null references galae on delete cascade,
  name               text not null,          -- 답만 쓴다. 경로·이유는 description으로
  description        text,
  trigger_conditions text,
  position           int  not null default 0,
  is_residual        boolean not null default false,
  status             text not null default 'active' check (status in
                     ('active','pending_judgment','confirmed','rejected')),
  status_reason      text,
  probability        smallint check (probability between 0 and 100
                                     and probability % 5 = 0),
  resolution_type    text not null check (resolution_type in ('auto','manual','complement')),
  -- auto 전용 — 조건은 하나뿐이다
  series_provider    text check (series_provider in ('fred','ecos','kis')),
  series_code        text,
  series_label       text,
  comparator         text check (comparator in ('gte','lte','between','change_pct')),
  target_value       numeric(18,4),
  target_low         numeric(18,4),
  target_high        numeric(18,4),
  baseline_date      date,
  auto_status        text check (auto_status in ('not_met','met')),
  met_at             date,
  progress           real,
  -- manual 전용
  marked             text check (marked in ('happened','did_not_happen')),
  marked_at          timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  check (resolution_type <> 'auto'
         or (series_provider is not null and series_code is not null
             and comparator is not null)),
  check (resolution_type = 'auto'
         or (series_provider is null and comparator is null and target_value is null)),
  check (comparator is distinct from 'between'
         or (target_low is not null and target_high is not null)),
  check (comparator is distinct from 'change_pct' or baseline_date is not null),
  check (not is_residual or resolution_type = 'complement')
);

create index scenarios_galae_idx on scenarios (galae_id);
create index scenarios_eval_idx on scenarios (series_provider, series_code)
  where resolution_type = 'auto' and auto_status = 'not_met';
create unique index one_residual_per_galae on scenarios (galae_id) where is_residual;

create trigger scenarios_updated_at
  before update on scenarios
  for each row execute procedure extensions.moddatetime(updated_at);

-- ── 불변식: 갈래별 확률 합 = 100 (deferred constraint trigger) ────────────
-- 허용 상태는 둘뿐: 전부 null(아직 배분 안 함) 또는 전부 채워지고 합 100.
create or replace function check_galae_probability_sum()
returns trigger language plpgsql as $$
declare
  g uuid := coalesce(new.galae_id, old.galae_id);
  cnt int; nulls int; total int; residual_ok boolean;
begin
  select count(*),
         count(*) filter (where probability is null),
         coalesce(sum(probability), 0),
         bool_and(probability >= 5) filter (where is_residual)
    into cnt, nulls, total, residual_ok
    from scenarios where galae_id = g;

  if nulls > 0 and nulls < cnt then
    raise exception 'probabilities must be all-null or all-set per galae (%)', g;
  end if;
  if nulls = 0 and cnt > 0 and total <> 100 then
    raise exception 'probability sum must be 100 for galae % (got %)', g, total;
  end if;
  if nulls = 0 and residual_ok is false then
    raise exception 'residual scenario must keep at least 5%% (galae %)', g;
  end if;
  return null;
end $$;

create constraint trigger galae_probability_sum
  after insert or update of probability, galae_id or delete on scenarios
  deferrable initially deferred
  for each row execute function check_galae_probability_sum();

-- ── `그 외 예상 못한 전개` 삭제 보호 ──────────────────────────────────────
create or replace function protect_residual()
returns trigger language plpgsql as $$
begin
  if old.is_residual then
    raise exception 'residual scenario cannot be deleted';
  end if;
  return old;
end $$;

create trigger scenarios_protect_residual
  before delete on scenarios
  for each row execute function protect_residual();

-- ── 확률 이력 ─────────────────────────────────────────────────────────────
-- 출처 컬럼이 없다 — AI는 확률 숫자를 내지 않으므로 전부 사용자의 판단이다.
create table probability_entries (
  id          uuid primary key default gen_random_uuid(),
  scenario_id uuid not null references scenarios on delete cascade,
  value       smallint not null check (value between 0 and 100 and value % 5 = 0),
  reason      text,
  created_at  timestamptz not null default now()
);

-- ── auto 조건 사후 수정 이력 (회고 화면 노출) ─────────────────────────────
create table auto_condition_edits (
  id          uuid primary key default gen_random_uuid(),
  scenario_id uuid not null references scenarios on delete cascade,
  field       text not null,
  from_value  text,
  to_value    text,
  reason      text,
  edited_at   timestamptz not null default now()
);

-- ── 확률을 매길 때 보여준 자료 ────────────────────────────────────────────
create table galae_references (
  id          uuid primary key default gen_random_uuid(),
  galae_id    uuid not null references galae on delete cascade,
  scenario_id uuid references scenarios on delete set null,  -- null = 기저율 (어느 편도 아님)
  kind        text not null check (kind in ('base_rate','evidence','market_view')),
  headline    text not null,
  note        text,
  sources     jsonb not null default '[]',
  fetched_at  timestamptz not null default now()
);

-- ── RLS ───────────────────────────────────────────────────────────────────
alter table galae enable row level security;
alter table galae_deadline_resets enable row level security;
alter table scenarios enable row level security;
alter table probability_entries enable row level security;
alter table auto_condition_edits enable row level security;
alter table galae_references enable row level security;

create policy galae_owner on galae
  for all to authenticated
  using (exists (select 1 from notes n
                 where n.id = note_id and n.user_id = auth.uid()))
  with check (exists (select 1 from notes n
                      where n.id = note_id and n.user_id = auth.uid()));

create policy galae_deadline_resets_owner on galae_deadline_resets
  for all to authenticated
  using (exists (select 1 from galae g join notes n on n.id = g.note_id
                 where g.id = galae_id and n.user_id = auth.uid()))
  with check (exists (select 1 from galae g join notes n on n.id = g.note_id
                      where g.id = galae_id and n.user_id = auth.uid()));

create policy scenarios_owner on scenarios
  for all to authenticated
  using (exists (select 1 from galae g join notes n on n.id = g.note_id
                 where g.id = galae_id and n.user_id = auth.uid()))
  with check (exists (select 1 from galae g join notes n on n.id = g.note_id
                      where g.id = galae_id and n.user_id = auth.uid()));

create policy probability_entries_owner on probability_entries
  for all to authenticated
  using (exists (select 1 from scenarios s
                   join galae g on g.id = s.galae_id
                   join notes n on n.id = g.note_id
                 where s.id = scenario_id and n.user_id = auth.uid()))
  with check (exists (select 1 from scenarios s
                        join galae g on g.id = s.galae_id
                        join notes n on n.id = g.note_id
                      where s.id = scenario_id and n.user_id = auth.uid()));

create policy auto_condition_edits_owner on auto_condition_edits
  for all to authenticated
  using (exists (select 1 from scenarios s
                   join galae g on g.id = s.galae_id
                   join notes n on n.id = g.note_id
                 where s.id = scenario_id and n.user_id = auth.uid()))
  with check (exists (select 1 from scenarios s
                        join galae g on g.id = s.galae_id
                        join notes n on n.id = g.note_id
                      where s.id = scenario_id and n.user_id = auth.uid()));

create policy galae_references_owner on galae_references
  for all to authenticated
  using (exists (select 1 from galae g join notes n on n.id = g.note_id
                 where g.id = galae_id and n.user_id = auth.uid()))
  with check (exists (select 1 from galae g join notes n on n.id = g.note_id
                      where g.id = galae_id and n.user_id = auth.uid()));

commit;
