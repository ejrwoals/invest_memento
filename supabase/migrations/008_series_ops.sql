-- 008_series_ops.sql — 수치 스냅샷(전역), 운영 보조 테이블, 비동기 작업 큐
-- 근거: docs/dev/01-db-schema.md §3.10~§3.12
-- 선행: 002 (series_catalog)

begin;

-- ── series_snapshots (전역 — 사용자별로 저장하지 않는다) ──────────────────
create table series_snapshots (
  provider   text not null,
  code       text not null,
  date       date not null,    -- 현지 거래일 기준
  close      numeric(18,4) not null,
  high       numeric(18,4),    -- 장중 터치 판정용. 거시 계열은 null
  low        numeric(18,4),
  fetched_at timestamptz not null default now(),
  primary key (provider, code, date),
  foreign key (provider, code) references series_catalog (provider, code)
);

-- ── jobs (온디맨드 AI 작업 큐 — 워커가 FOR UPDATE SKIP LOCKED 폴링) ───────
create table jobs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references auth.users on delete cascade,
  kind        text not null,
  payload     jsonb not null default '{}',
  status      text not null default 'queued'
              check (status in ('queued','running','done','failed')),
  attempts    int not null default 0,
  run_after   timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz,
  error       text,
  created_at  timestamptz not null default now()
);

create index jobs_poll_idx on jobs (status, run_after);

-- ── 운영 보조 테이블 (캐시·마크·로그 — 지워져도 복원 가능) ────────────────
create table quote_cache (
  provider   text not null,
  code       text not null,
  price      numeric(18,4) not null,
  fetched_at timestamptz not null,
  primary key (provider, code)
);

create table kis_tokens (
  id         boolean primary key default true check (id),   -- 한 행 강제
  token      text not null,
  issued_at  timestamptz not null,
  expires_at timestamptz not null
);

create table pnl_recalc_marks (
  user_id   uuid primary key references auth.users on delete cascade,
  from_date date not null
);

create table series_release_dates (
  provider     text not null,
  code         text not null,
  release_date date not null,
  primary key (provider, code, release_date)
);

create table catalog_misses (
  id         uuid primary key default gen_random_uuid(),
  expression text not null,
  note_id    uuid references notes on delete set null,
  created_at timestamptz not null default now()
);

-- ── RLS ───────────────────────────────────────────────────────────────────
-- 전역 읽기 테이블: 인증 사용자 읽기 전용. 쓰기는 service role(배치)만.
alter table series_snapshots enable row level security;
create policy series_snapshots_read on series_snapshots
  for select to authenticated using (true);

alter table series_release_dates enable row level security;
create policy series_release_dates_read on series_release_dates
  for select to authenticated using (true);

alter table quote_cache enable row level security;
create policy quote_cache_read on quote_cache
  for select to authenticated using (true);

-- 서버 전용 테이블: 정책 없음 = authenticated 접근 전면 차단 (service role만)
alter table kis_tokens enable row level security;
alter table pnl_recalc_marks enable row level security;
alter table catalog_misses enable row level security;

-- jobs: 본인 것 조회만 허용 (적재·갱신은 service role)
alter table jobs enable row level security;
create policy jobs_owner_read on jobs
  for select to authenticated using (user_id = auth.uid());

commit;
