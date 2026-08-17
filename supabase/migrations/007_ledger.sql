-- 007_ledger.sql — 장부: 매매·환전 기록, 손익 추이 캐시
-- 근거: docs/dev/01-db-schema.md §3.9
-- 선행: 002 (instruments)

begin;

create table trades (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users on delete cascade,
  symbol     text not null references instruments(symbol),
  side       text not null check (side in ('buy','sell','dividend')),
  traded_at  date not null,
  price      numeric(18,4),   -- 모르면 null — 지어내지 않는다 (빈 칸 원칙)
  quantity   numeric(18,6) not null check (quantity > 0),
  comment    text,
  created_at timestamptz not null default now()
);
-- Holding 테이블은 없다. 잔고·평단·실현손익은 전부 파생 계산.
-- "매도 수량 > 잔고" 차단은 저장 API의 결정론적 검사가 담당한다.

create index trades_user_symbol_idx on trades (user_id, symbol, traded_at);
create index trades_user_date_idx on trades (user_id, traded_at);

create table exchanges (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users on delete cascade,
  direction    text not null check (direction in ('krw_to_usd','usd_to_krw')),
  exchanged_at date not null,
  rate         numeric(12,4),  -- 모르면 null — 환차손익 계산에서 제외
  usd_amount   numeric(14,2) not null check (usd_amount > 0),
  comment      text,
  created_at   timestamptz not null default now()
);

create index exchanges_user_idx on exchanges (user_id, exchanged_at);

create table pnl_snapshots (
  user_id    uuid not null references auth.users on delete cascade,
  scope      text not null check (scope in ('kr','us','fx')),
  date       date not null,    -- 종가 기준. 미마감 당일은 만들지 않는다
  pnl        numeric(18,2) not null,
  created_at timestamptz not null default now(),
  primary key (user_id, scope, date)
);

-- ── RLS ───────────────────────────────────────────────────────────────────
alter table trades enable row level security;
alter table exchanges enable row level security;
alter table pnl_snapshots enable row level security;

create policy trades_owner on trades
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy exchanges_owner on exchanges
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy pnl_snapshots_owner on pnl_snapshots
  for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

commit;
