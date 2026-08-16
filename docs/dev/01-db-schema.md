# DB 스키마 — Supabase PostgreSQL

> 상위 문서: [`development-plan.md`](../development-plan.md) §5(데이터 모델)·§13(구현 마스터플랜).
> 이 문서가 테이블 구조의 정본이다. §5의 개념 모델과 어긋나면 이 문서를 고치기 전에
> §5와의 차이가 의도된 것인지 먼저 확인한다.

## 1. 전제와 규약

- **DB는 Supabase 관리형 PostgreSQL**이다. 마이그레이션은 `supabase/migrations/`의
  SQL 파일로 관리하고, `supabase db push`/CI로 적용한다.
- **인증은 Supabase Auth.** 사용자 정본은 `auth.users`이고, 앱 스키마는 `public`에
  `profiles`로 1:1 확장한다. 모든 사용자 소유 테이블은 `user_id uuid references auth.users`를
  가진다.
- **PK는 `uuid default gen_random_uuid()`**, 시각은 `timestamptz`, 날짜만 의미 있는 값
  (거래일·판단 시점·스냅샷 일자)은 `date`.
- **열거값은 Postgres ENUM이 아니라 `text` + `CHECK`로 둔다.** 값 추가가 마이그레이션
  한 줄로 끝나고, Supabase 타입 생성·클라이언트 코드젠과의 마찰이 없다.
- **파생 값은 저장하지 않는다**가 기본값이다(§3.9 잔고·평단·수익률). 예외는 두 가지뿐:
  - `pnl_snapshots` — 손익 추이의 파생 캐시. 언제든 재계산 가능함을 전제로 적재한다.
  - `scenarios`의 auto 판정 진행 상태(`auto_status`·`met_at`·`progress`) — 야간 배치의
    평가 결과 캐시. 정본은 `series_snapshots`이며 재평가로 언제든 복원된다.
- **불변 테이블**: `conversation_messages`는 INSERT만 허용한다(§P2 원본 대화 불변).
  트리거로 UPDATE/DELETE를 차단한다.
- API 서버(FastAPI)는 service role로 접속하고 권한 검사를 애플리케이션에서 수행한다.
  클라이언트가 PostgREST로 직접 테이블을 읽는 경로는 만들지 않지만, **방어선으로 RLS를
  전 테이블에 켠다**(§7).

## 2. ER 개요

```
auth.users ─ profiles
    │
    ├─ notes ─┬─ conversations ── conversation_messages   (불변)
    │         ├─ content_blocks
    │         ├─ sources
    │         ├─ premises ── premise_verifications
    │         ├─ watches
    │         ├─ research_items
    │         ├─ advisor_opinions
    │         ├─ reminder_rules
    │         └─ galae ─┬─ scenarios ─┬─ probability_entries
    │                   │             └─ auto_condition_edits
    │                   ├─ galae_references
    │                   ├─ galae_deadline_resets
    │                   └─ reviews
    ├─ trades
    ├─ exchanges
    ├─ pnl_snapshots
    ├─ notifications
    └─ jobs               온디맨드 AI 작업 큐 (워커 폴링)

(전역 · user_id 없음)
instruments          매매·노트 대상 티커의 정규화 사전
series_catalog       auto 판정·지켜보는 수치의 계열 사전 (FRED/ECOS/KIS)
series_snapshots     일별 관측값 전역 캐시 — (provider, code, date) 유니크
```

## 3. 테이블 정의

### 3.1 사용자

```sql
create table profiles (
  user_id              uuid primary key references auth.users on delete cascade,
  display_name         text,
  disclaimer_agreed_at timestamptz,          -- 온보딩 면책 동의 시각 (§3.8). null이면 미동의
  reminder_channel     text not null default 'email'
                       check (reminder_channel in ('email')),   -- MVP는 이메일뿐
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
```

### 3.2 카탈로그 (전역)

티커 정규화(§3.9 "Note.target과 같은 정규화")의 정본. 매매 기록과 노트 target이
같은 심볼 체계를 쓰게 하는 장치이고, 국내/미국 탭 분류(`market`)와 손익 통화
(`currency`)가 여기서 결정된다 — 사용자가 탭을 고르지 않는다는 결정(§3.9)의 구현체다.

```sql
create table instruments (
  symbol     text primary key,              -- 정규화된 심볼: '005930', 'AAPL'
  name       text not null,                 -- '삼성전자'
  market     text not null check (market in ('kr','us')),
  currency   text not null check (currency in ('KRW','USD')),
  kis_code   text,                          -- 한국투자증권 조회용 코드 (없으면 symbol 그대로)
  created_at timestamptz not null default now()
);
```

auto 판정과 지켜보는 수치가 참조하는 계열 사전. "사용자 표현 → 계열 코드 매핑"
(§6 계열 카탈로그 검색)의 검색 대상이다. 주식 계열은 `instruments`와 겹치지만
역할이 다르다 — `instruments`는 장부의 정규화, `series_catalog`는 수치 수집의 대상.

```sql
create table series_catalog (
  provider   text not null check (provider in ('fred','ecos','kis')),
  code       text not null,                 -- 'DFF', '005930', ...
  label      text not null,                 -- '미국 기준금리', '삼성전자'
  kind       text not null check (kind in ('equity','index','macro','fx')),
  unit       text,                          -- '%', '원', '달러' 등 표시용
  has_intraday boolean not null default false,  -- true면 고가·저가 수집 (장중 터치 판정)
  search_keywords text[],                   -- 사용자 표현 매핑 보조 ('기준금리', '연준')
  primary key (provider, code)
);
```

### 3.3 노트 축

```sql
create table notes (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users on delete cascade,
  target_type    text not null check (target_type in ('ticker','asset','theme')),
  target_symbol  text references instruments(symbol),  -- ticker일 때만. asset/theme은 null
  target_name    text not null,             -- 화면 표기명. ticker면 instruments.name 복사
  thesis_summary text not null,             -- 한 문장, 40자 내외 (§3.1 노트 생성 계약)
  thesis_detail  text,
  color          text not null,             -- 홈 타임라인 식별색 (§5)
  archived_at    timestamptz,               -- 모든 갈래 judged 후 사용자가 접은 시점
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
```

- **`is_complete` 컬럼은 두지 않는다.** §5 초안에는 있으나, "판단 시점 있는 갈래가
  하나 이상 존재"에서 완전히 파생되는 값이라 저장하면 어긋날 길만 생긴다. API가
  응답에 계산해 실어준다. (파생 값 비저장 원칙)
- `NO_TARGET`/`NO_THESIS`가 blocking이므로(§3.1) `target_name`·`thesis_summary`는
  not null이다. 나머지 빈 칸 허용 정책은 스키마가 아니라 검증기의 일이다.

대화는 노트보다 먼저 태어난다(1단계 대화 도중 이탈 → draft 재개). 그래서
`conversations`가 `note_id`를 nullable로 갖고, 노트 저장 시점에 연결된다.

```sql
create table conversations (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users on delete cascade,
  note_id     uuid unique references notes on delete restrict,
  status      text not null default 'draft'
              check (status in ('draft','attached','abandoned')),
  draft_note  jsonb,                        -- 작성 중 실시간 패널의 진행 상태 (UX §3.2)
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table conversation_messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations on delete restrict,
  seq             int  not null,            -- 대화 내 순서
  role            text not null check (role in ('user','assistant')),
  content         text not null,
  created_at      timestamptz not null default now(),
  unique (conversation_id, seq)
);
-- 불변: §4.2의 트리거로 UPDATE/DELETE 차단. 노트가 지워져도 대화는 남는다(restrict).
```

노트 본문 블록. `quoted_from`이 있는 블록과 `premises.statement`가 "사용자가 한 말"
주장이며, 저장 시 서버가 원본 대화에서 실제 문자열을 대조한다(§3.1 검증 2층).

```sql
create table content_blocks (
  id          uuid primary key default gen_random_uuid(),
  note_id     uuid not null references notes on delete cascade,
  section     text not null check (section in
              ('thesis','thesis_quote','scenario','premise_intro','free')),
  position    int  not null default 0,
  content     text not null,
  authorship  text not null check (authorship in ('ai','user')),  -- user만 [사용자] 표기 (P3)
  quoted_from uuid references conversation_messages,   -- 사용자 발화 직접 인용 시
  derived     boolean not null default false,           -- AI 해석값 여부 → 미리보기 확인 대상
  created_at  timestamptz not null default now()
);

create table sources (                       -- 사용자가 첨부한 근거 자료 (§5)
  id         uuid primary key default gen_random_uuid(),
  note_id    uuid not null references notes on delete cascade,
  type       text not null check (type in ('link','text','file','image')),
  url        text,
  content    text,
  storage_path text,                         -- file/image → Supabase Storage 경로
  created_at timestamptz not null default now()
);
```

### 3.4 갈래와 시나리오

```sql
create table galae (
  id            uuid primary key default gen_random_uuid(),
  note_id       uuid not null references notes on delete cascade,
  question      text not null,              -- "올해 안에 HBM4 공급사로 진입하는가?"
  judge_kind    text check (judge_kind in ('date','range')),
  judge_start   date,                       -- range일 때만. date면 null
  judge_end     date,                       -- 판정 기준일. null이면 미완성 노트 (리마인드 제외)
  status        text not null default 'open' check (status in ('open','judged')),
  position      int  not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  check (judge_kind is null or judge_end is not null),
  check (judge_kind is distinct from 'range' or judge_start is not null)
);
-- ★ 판단 시점은 갈래에 하나뿐이다(§3.4). 시나리오에는 날짜 필드 자체가 없다.

create table galae_deadline_resets (         -- "시기를 다시 본다" 이력 (회고용, §3.4)
  id         uuid primary key default gen_random_uuid(),
  galae_id   uuid not null references galae on delete cascade,
  from_end   date not null,
  to_end     date not null,
  reason     text,
  reset_at   timestamptz not null default now()
);
```

```sql
create table scenarios (
  id                 uuid primary key default gen_random_uuid(),
  galae_id           uuid not null references galae on delete cascade,
  name               text not null,          -- 답만 쓴다. 경로·이유는 description으로 (§3.4)
  description        text,
  trigger_conditions text,
  position           int  not null default 0,
  is_residual        boolean not null default false,  -- `그 외 예상 못한 전개`. 삭제 불가·최소 5%
  status             text not null default 'active' check (status in
                     ('active','pending_judgment','confirmed','rejected')),
  status_reason      text,                   -- 결과를 그렇게 정한 이유 한 줄 (선택)
  probability        smallint check (probability between 0 and 100
                                     and probability % 5 = 0),
  -- 판정 방법 (§2.3). 시나리오는 자기가 어떻게 판정되는지를 스스로 갖는다.
  resolution_type    text not null check (resolution_type in ('auto','manual','complement')),
  -- auto 전용 칼럼 — 조건은 하나뿐이다. 둘이 필요하면 질문이 둘이다.
  series_provider    text check (series_provider in ('fred','ecos','kis')),
  series_code        text,
  series_label       text,
  comparator         text check (comparator in ('gte','lte','between','change_pct')),
  target_value       numeric(18,4),
  target_low         numeric(18,4),          -- between 전용
  target_high        numeric(18,4),
  baseline_date      date,                   -- change_pct 전용 기준일
  auto_status        text check (auto_status in ('not_met','met')),  -- 배치 평가 캐시
  met_at             date,
  progress           real,                   -- 목표까지의 거리 0~1 (배치 평가 캐시)
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
-- 관측 창은 갈래의 judge_at, 관측 규칙은 시스템 고정 — 그래서 여기에 기한·규칙 필드가 없다(§2.3).
```

확률 이력. **현재값은 `scenarios.probability`, 이력은 여기.** 쓰기는 갈래 단위
원자적 갱신 API 한 곳뿐이고(§3.1), 그 트랜잭션이 두 곳을 함께 쓴다.

```sql
create table probability_entries (
  id          uuid primary key default gen_random_uuid(),
  scenario_id uuid not null references scenarios on delete cascade,
  value       smallint not null check (value between 0 and 100 and value % 5 = 0),
  reason      text,                          -- 무엇을 보고 바꿨는지 (선택)
  created_at  timestamptz not null default now()
);
-- 출처(source) 컬럼이 없다 — AI는 확률 숫자를 내지 않으므로(§3.4) 전부 사용자의 판단이다.

create table auto_condition_edits (          -- 목표값 사후 수정 이력 (§2.3, 회고 화면 노출)
  id          uuid primary key default gen_random_uuid(),
  scenario_id uuid not null references scenarios on delete cascade,
  field       text not null,                 -- 'target_value' 등
  from_value  text,
  to_value    text,
  reason      text,
  edited_at   timestamptz not null default now()
);
```

확률을 매길 때 보여준 자료(§3.4). 어느 답 편인지로 더미를 가른다.

```sql
create table galae_references (
  id          uuid primary key default gen_random_uuid(),
  galae_id    uuid not null references galae on delete cascade,
  scenario_id uuid references scenarios on delete set null,  -- null = 어느 편도 아님 (기저율)
  kind        text not null check (kind in ('base_rate','evidence','market_view')),
  headline    text not null,                 -- 기저율은 분모·분자를 문장에 그대로 담는다
  note        text,
  sources     jsonb not null default '[]',   -- [{title, url, published_at}]
  fetched_at  timestamptz not null default now()
);
-- 확률값을 담는 kind는 없다 — 사용자가 낸 것 하나만 확률이다(§3.4).
```

### 3.5 근거 항목과 지켜보는 수치

```sql
create table watches (                       -- 판정하지 않는다. 추이만 (§2.3)
  id         uuid primary key default gen_random_uuid(),
  note_id    uuid not null references notes on delete cascade,
  provider   text not null,
  code       text not null,
  label      text not null,
  created_at timestamptz not null default now(),
  foreign key (provider, code) references series_catalog (provider, code)
);

create table premises (                      -- 근거 항목 — 노트에 붙는다. 갈래가 아니다 (§5)
  id              uuid primary key default gen_random_uuid(),
  note_id         uuid not null references notes on delete cascade,
  statement       text not null,             -- 사용자가 말한 그대로. 다듬지 않는다
  position        int  not null default 0,   -- 논리 사슬 순서 (A → B → C)
  quoted_from     uuid references conversation_messages,
  linked_watch_id uuid references watches on delete set null,
  created_at      timestamptz not null default now()
);

create table premise_verifications (         -- 회고 시 AI가 채운다. 갈래마다 열리므로 이력으로 쌓인다
  id          uuid primary key default gen_random_uuid(),
  premise_id  uuid not null references premises on delete cascade,
  review_id   uuid not null references reviews on delete cascade,
  verdict     text not null check (verdict in ('happened','did_not_happen','unverifiable')),
  summary     text,
  sources     jsonb not null default '[]',
  verified_at timestamptz not null default now(),
  unique (premise_id, review_id)
);
```

### 3.6 판정과 회고

```sql
create table reviews (                       -- 회고록 — 갈래 단위로 열린다 (§3.6)
  id                   uuid primary key default gen_random_uuid(),
  galae_id             uuid not null unique references galae on delete cascade,
  realized_scenario_id uuid references scenarios,
  outcome              text not null check (outcome in ('confirmed','rejected')),
  logic_verdict        text check (logic_verdict in
                       ('logic_held','outcome_only','logic_only','both_wrong')),
  ai_draft             text,
  benchmark_comparison jsonb,   -- { scenario_return, index_return, sector_return,
                                --   my_return(매매 기록 있을 때), period }
  user_narrative       text,                 -- [사용자] 저작
  status               text not null default 'draft_ready'
                       check (status in ('draft_ready','written')),
  drafted_at           timestamptz,
  written_at           timestamptz,
  created_at           timestamptz not null default now()
);
```

### 3.7 AI 산출물

```sql
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

create table advisor_opinions (
  id                     uuid primary key default gen_random_uuid(),
  note_id                uuid not null references notes on delete cascade,
  stance                 text not null check (stance in ('buy','add','hold','trim','sell')),
  rationale              text not null,
  referenced_scenarios   uuid[] not null default '{}',
  referenced_research    uuid[] not null default '{}',
  target_price           numeric(18,4),
  target_timeframe       text,
  invalidation_condition text,               -- 이 의견이 틀렸다고 볼 조건
  user_action            text not null default 'none'
                         check (user_action in ('followed','partially_followed','ignored','none')),
  created_at             timestamptz not null default now()
);
```

### 3.8 리마인드

```sql
create table reminder_rules (
  id                    uuid primary key default gen_random_uuid(),
  note_id               uuid not null references notes on delete cascade,
  type                  text not null check (type in
                        ('interval','galae_deadline','pending_judgment','event_triggered')),
  config                jsonb not null default '{}',
  next_trigger_at       timestamptz,
  consecutive_unopened  int not null default 0,   -- 화면에 절대 노출하지 않음 (§5, P5)
  current_interval_weeks int not null default 2,  -- 미열람 시 2배씩, 최대 12주
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create table notifications (                 -- 발송 로그 — 하루 1통 묶음·미열람 감쇠의 근거
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users on delete cascade,
  note_id       uuid references notes on delete cascade,
  kind          text not null,               -- 'reminder_digest', 'judgment_due', ...
  payload       jsonb not null default '{}',
  channel       text not null default 'email' check (channel in ('email')),
  scheduled_for timestamptz not null,
  sent_at       timestamptz,
  opened_at     timestamptz,                 -- 리마인드 상세 진입 시각 (감쇠 판단용)
  created_at    timestamptz not null default now()
);
```

### 3.9 장부 (매매·환전·손익 캐시)

```sql
create table trades (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users on delete cascade,
  symbol     text not null references instruments(symbol),
  side       text not null check (side in ('buy','sell','dividend')),
  traded_at  date not null,
  price      numeric(18,4),   -- 종목 통화 기준. dividend는 주당 배당액.
                              -- 과거 보유분 등록 시 모르면 null — 지어내지 않는다 (빈 칸 원칙)
  quantity   numeric(18,6) not null check (quantity > 0),
  comment    text,            -- 그때 왜 샀는가/팔았는가 — 한 줄 판단 기록
  created_at timestamptz not null default now()
);
-- ※ Holding 테이블은 없다. 잔고 = Σ매수 − Σ매도, 평단 = 이동평균 — 전부 파생 계산(§5).
--    "매도 수량 > 잔고" 차단은 DB가 아니라 저장 API의 결정론적 검사가 한다.
--    (running balance는 CHECK로 표현할 수 없고, 과거 날짜 삽입·수정도 재검사해야 하므로
--     검사 지점을 애플리케이션 한 곳으로 모은다.)
--    노트와의 연결은 별도 FK 없이 symbol = notes.target_symbol 매칭으로 계산한다.

create table exchanges (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users on delete cascade,
  direction    text not null check (direction in ('krw_to_usd','usd_to_krw')),
  exchanged_at date not null,
  rate         numeric(12,4),  -- 원/달러. 모르면 null — 환차손익 계산에서 제외
  usd_amount   numeric(14,2) not null check (usd_amount > 0),
  comment      text,           -- 왜 지금 환전하는가 — 환율에 대한 판단 한 줄
  created_at   timestamptz not null default now()
);

create table pnl_snapshots (   -- 손익 추이의 파생 캐시. 야간 배치가 적재 (§3.9)
  user_id    uuid not null references auth.users on delete cascade,
  scope      text not null check (scope in ('kr','us','fx')),
  date       date not null,    -- 종가 기준. 미마감 당일은 만들지 않는다
  pnl        numeric(18,2) not null,  -- 누적 손익 (kr·fx 원, us 달러)
  created_at timestamptz not null default now(),
  primary key (user_id, scope, date)
);
-- 과거 날짜의 매매·환전이 추가·수정되면 해당 구간을 소급 재계산한다(삭제 후 재적재).
```

### 3.10 비동기 작업 큐

온디맨드 AI 작업(리서치·회고 초안·어드바이저)의 실행 큐. MVP에서 Redis를 두지 않는
대신 Postgres 테이블을 워커가 `FOR UPDATE SKIP LOCKED`로 폴링한다(§13.1).

```sql
create table jobs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references auth.users on delete cascade,  -- 사용자별 실행 정책·쿼터용
  kind        text not null,       -- 'research', 'review_draft', 'advisor', 'reference', ...
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
create index on jobs (status, run_after);
```

API는 작업을 넣고 202를 반환하며, 클라이언트는 결과 리소스(research_items 등)를
폴링한다. 상세는 [`02-backend.md`](./02-backend.md).

### 3.11 수치 스냅샷 (전역)

```sql
create table series_snapshots (
  provider   text not null,
  code       text not null,
  date       date not null,    -- 현지 거래일 기준. 한국 시간으로 환산하지 않는다 (§3.5)
  close      numeric(18,4) not null,  -- 판정과 차트의 기준
  high       numeric(18,4),    -- 장중 터치 판정용. 거시 계열은 null
  low        numeric(18,4),
  fetched_at timestamptz not null default now(),
  primary key (provider, code, date),
  foreign key (provider, code) references series_catalog (provider, code)
);
-- 사용자별로 저장하지 않는다. 외부 API 호출량은 사용자 수가 아니라 계열 수에 비례한다(§3.5).
```

### 3.12 운영 보조 테이블

Series Service([`05-series-service.md`](./05-series-service.md))가 요구하는 작은
테이블들이다. 전부 캐시·마크·로그 성격이라 지워져도 기능 손실 없이 복원된다.

```sql
create table quote_cache (           -- 온디맨드 현재가의 전역 TTL 캐시 (05 §6)
  provider   text not null,
  code       text not null,
  price      numeric(18,4) not null,
  fetched_at timestamptz not null,
  primary key (provider, code)
);
-- 인프로세스 캐시가 아닌 이유: API 프로세스가 여러 개면 프로세스 수만큼 KIS를 때린다.

create table kis_tokens (            -- KIS 접근토큰 공유 — 수명 1일, 프로세스 간 공유 (05 §2.4)
  id         boolean primary key default true check (id),   -- 한 행 강제
  token      text not null,
  issued_at  timestamptz not null,
  expires_at timestamptz not null
);

create table pnl_recalc_marks (      -- PnlSnapshot 소급 재계산 마크 (05 §7)
  user_id   uuid primary key references auth.users on delete cascade,
  from_date date not null            -- 장부 쓰기 트랜잭션이 영향 시작일을 least() upsert
);

create table series_release_dates (  -- FRED 발표 캘린더 — "다음 점검 일정" 표시용 (05 §2.2)
  provider     text not null,
  code         text not null,
  release_date date not null,
  primary key (provider, code, release_date)
);

create table catalog_misses (        -- 계열 매핑 실패 로그 — 시드 확장 우선순위 (05 §3.3)
  id         uuid primary key default gen_random_uuid(),
  expression text not null,
  note_id    uuid references notes on delete set null,
  created_at timestamptz not null default now()
);
```

## 4. 불변식 — 검사하지 않고 어긋날 수 없게

### 4.1 갈래별 확률 합 = 100

개별 시나리오 확률을 고치는 API를 만들지 않는 것(§3.1)이 1차 방어선이고, DB 제약은
코드가 틀려도 잘못된 데이터가 남지 않게 하는 마지막 방어선이다. 행 단위 CHECK로는
합계를 볼 수 없으므로 **deferred constraint trigger**로 건다.

```sql
create or replace function check_galae_probability_sum() returns trigger as $$
declare
  g uuid := coalesce(new.galae_id, old.galae_id);
  total int; cnt int; nulls int; residual_ok boolean;
begin
  select count(*), count(*) filter (where probability is null),
         coalesce(sum(probability), 0),
         bool_and(probability >= 5) filter (where is_residual)
    into cnt, nulls, total, residual_ok
    from scenarios where galae_id = g;
  -- 허용 상태는 둘뿐: 전부 null(아직 배분 안 함) 또는 전부 채워지고 합 100
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
end $$ language plpgsql;

create constraint trigger galae_probability_sum
  after insert or update of probability, galae_id or delete on scenarios
  deferrable initially deferred
  for each row execute function check_galae_probability_sum();
```

- **시나리오가 하나뿐인 갈래는 확률을 전부 null로 둔다**(§3.1 — 혼자인 답에 100%를
  넣으면 사용자가 표현한 적 없는 확신을 만든다). 이 규칙은 재분배 함수(서버)가 지킨다.
- 재분배(스냅·최대 잔여법·잔여 슬롯 5%)는 서버의 순수 함수 하나가 정본이다.
  → [`02-backend.md`](./02-backend.md), 골든 테스트 벡터는 §13(마스터플랜) 참조.

### 4.2 원본 대화 불변 (P2)

```sql
create or replace function forbid_mutation() returns trigger as $$
begin
  raise exception '% is immutable', tg_table_name;
end $$ language plpgsql;

create trigger conversation_messages_immutable
  before update or delete on conversation_messages
  for each row execute function forbid_mutation();
```

### 4.3 `그 외 예상 못한 전개` 보호

```sql
create or replace function protect_residual() returns trigger as $$
begin
  if old.is_residual then
    raise exception 'residual scenario cannot be deleted';
  end if;
  return old;
end $$ language plpgsql;

create trigger scenarios_protect_residual
  before delete on scenarios
  for each row execute function protect_residual();
```

갈래 생성 API가 residual 시나리오를 반드시 함께 만든다(서버 책임). "갈래당 residual
정확히 1개"는 partial unique index로 보강한다:

```sql
create unique index one_residual_per_galae on scenarios (galae_id) where is_residual;
```

### 4.4 `updated_at` 자동 갱신

`moddatetime` 확장(Supabase 기본 제공)으로 `updated_at`이 있는 모든 테이블에 트리거를 건다.

## 5. 파생 계산 정의 (요약)

정본 수식과 구현은 [`02-backend.md`](./02-backend.md)에 있다. 여기서는 스키마가
그 계산을 지탱할 수 있는지만 확인한다. 전부 `trades`·`exchanges`·`series_snapshots`
세 테이블에서 나온다.

| 파생 값 | 정의 | 원천 |
|---|---|---|
| 잔고 수량 | Σ매수 − Σ매도 (종목별) | trades |
| 평균 매수 단가 | 이동평균 (매수 시 가중평균, 매도 시 유지). price null 거래가 섞이면 `단가 없음` | trades |
| 실현 손익 | (매도가 − 매도 시점 평단) × 매도 수량, 종목별 누적 | trades |
| 배당 누적 | Σ(price × quantity) where side='dividend' | trades |
| 달러 예수금 | Σ환전(₩→$) + Σ매도 대금 + Σ배당 − Σ매수 대금 − Σ재환전($→₩). 음수면 차단이 아니라 누락 안내 | trades + exchanges |
| 평균 취득 환율 / 환전 원금 잔량 | 주식 평단·잔고와 같은 문법 | exchanges |
| 실현·미실현 환차손익 | 재환전 시 확정 / (현재 환율 − 평균 취득 환율) × 환전 원금 잔량 | exchanges |
| XIRR (원화 전체 / 달러 축) | 장부의 현금 흐름 + 현재 평가금액. price null 종목은 제외하고 제외 사실 표기 | trades + exchanges + 시세 |
| 손익 추이 | 장부 × 일별 종가 × 일별 환율로 전 구간 재구성 → `pnl_snapshots` 적재 | trades + exchanges + series_snapshots |

## 6. 인덱스

```sql
-- 사용자 소유 목록 조회
create index on notes (user_id, archived_at);
create index on trades (user_id, symbol, traded_at);
create index on trades (user_id, traded_at);
create index on exchanges (user_id, exchanged_at);

-- 노트 하위 로딩
create index on galae (note_id);
create index on scenarios (galae_id);
create index on content_blocks (note_id, position);
create index on premises (note_id, position);
create index on research_items (note_id, created_at desc);

-- 배치·스케줄러
create index on galae (judge_end) where status = 'open';       -- 판단 시점 도래 스캔
create index on reminder_rules (next_trigger_at);
create index on notifications (user_id, scheduled_for);
create index on scenarios (series_provider, series_code)
  where resolution_type = 'auto' and auto_status = 'not_met';  -- 평가 대상 계열 추출
```

`series_snapshots`는 PK (provider, code, date)가 곧 조회 패턴(계열별 기간 범위)이라
추가 인덱스가 필요 없다.

## 7. RLS 정책

클라이언트는 FastAPI를 통해서만 데이터에 접근하지만, 방어선으로 전 테이블에 RLS를 켠다.

- **사용자 소유 테이블** (`notes`, `trades`, `exchanges`, `conversations`,
  `pnl_snapshots`, `notifications`, `profiles`): `user_id = auth.uid()`인 행만
  select/insert/update/delete 허용.
- **노트 하위 테이블** (`galae`, `scenarios`, `content_blocks`, `premises`, `watches`,
  `sources`, `research_items`, `advisor_opinions`, `reminder_rules`,
  `galae_references`, `reviews`, …): 소유 노트 경유 존재 검사
  (`exists (select 1 from notes where notes.id = note_id and notes.user_id = auth.uid())`,
  갈래 하위는 galae→notes 2단 경유).
- **전역 테이블** (`instruments`, `series_catalog`, `series_snapshots`):
  인증 사용자 read-only. 쓰기는 service role만.
- service role(FastAPI·배치)은 RLS를 우회한다 — 권한 검사는 API 레이어가 수행한다.

## 8. 마이그레이션 운영

- `supabase/migrations/<timestamp>_<name>.sql` 순차 적용. 로컬은 `supabase start`
  (Docker) + `supabase db reset`으로 재현하고, 원격은 CI에서 `supabase db push`.
- 마이그레이션 파일은 **수정하지 않고 항상 새 파일을 추가**한다(적용된 이력 불변).
- 시드: `supabase/seed.sql`에 `series_catalog` 초기 항목(주요 지수·FRED/ECOS 대표
  계열)과 개발용 픽스처를 둔다. 운영 시드와 개발 픽스처를 파일로 분리한다.
- FastAPI 쪽 ORM(SQLAlchemy)은 **스키마를 생성하지 않는다.** DDL의 정본은 Supabase
  마이그레이션이고, ORM 모델은 그것을 반영만 한다(불일치는 CI의 스키마 덤프 대조로 검출).

## 9. 의도적으로 저장하지 않는 것

| 없는 것 | 이유 |
|---|---|
| `Holding` 테이블, 잔고·평단·수익률 컬럼 | 전부 trades에서 파생. 저장하면 어긋날 길이 생긴다 (§3.9) |
| `notes.is_complete` | 갈래 판단 시점 유무에서 파생. API가 계산해 응답에 싣는다 |
| 원화 예수금 | 열린 계라 추적하지 않는다 (§3.9 확정) |
| 시나리오의 판단 시점·관측 규칙 필드 | 시점은 갈래에 하나, 규칙은 시스템 고정 — 구조로 막는다 (§2.3) |
| 확률 비교 열 (AI 추정치·시장 환산치) | 사용자가 낸 것 하나만 확률이다 (§3.4). Reference에 확률 kind가 없는 것과 같은 이유 |
| 당일(미마감) 시세 행 | 마지막 점은 언제나 확정 종가 (§3.5) |
| 거래 수수료·세금 컬럼 | 반영하지 않기로 확정 (§3.9) |
