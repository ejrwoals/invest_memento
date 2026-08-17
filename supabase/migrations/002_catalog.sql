-- 002_catalog.sql — 전역 카탈로그: 티커 정규화 사전, 계열 사전
-- 근거: docs/dev/01-db-schema.md §3.2
-- 쓰기는 service role(API·배치)만, 인증 사용자는 읽기 전용

begin;

create table instruments (
  symbol     text primary key,              -- 정규화된 심볼: '005930', 'AAPL'
  name       text not null,
  market     text not null check (market in ('kr','us')),
  currency   text not null check (currency in ('KRW','USD')),
  kis_code   text,
  created_at timestamptz not null default now()
);

create table series_catalog (
  provider        text not null check (provider in ('fred','ecos','kis')),
  code            text not null,
  label           text not null,
  kind            text not null check (kind in ('equity','index','macro','fx')),
  unit            text,
  has_intraday    boolean not null default false,
  search_keywords text[],
  primary key (provider, code)
);

alter table instruments enable row level security;
alter table series_catalog enable row level security;

create policy instruments_read on instruments
  for select to authenticated using (true);

create policy series_catalog_read on series_catalog
  for select to authenticated using (true);

commit;
