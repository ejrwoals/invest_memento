# 수치 수집 — Series Service

> 상위 문서: [`development-plan.md`](../development-plan.md) §13(구현 마스터플랜)·§3.5(수치 수집 & 평가)·§6(아키텍처).
> 테이블 구조의 정본은 [`01-db-schema.md`](./01-db-schema.md)(`series_catalog`·`series_snapshots`·
> `scenarios`의 auto 칼럼·`pnl_snapshots`·`instruments`)이다. 이 문서는 그 테이블을 채우고
> 읽는 **결정론적 파이프라인**을 정의한다.

## 1. 범위와 원칙

Series Service는 AI 오케스트레이션과 별개의 축이다(§6). 리서치는 비결정적·온디맨드이고
비용이 LLM 호출에 비례하지만, 수치 수집은 **결정적·스케줄 기반**이고 비용이 외부 API
쿼터에 걸린다. 실패 시 재시도 성질도 다르다 — 수치는 하루치를 놓쳐도 다음 배치에서
소급 수집하면 되지만, 리서치는 그렇지 않다. 그래서 LLM 코드와 섞지 않고 워커의 독립
모듈로 둔다.

아래 설계 전체를 관통하는 불변 원칙(§3.5·§2.3): ① 실시간 없음 — 일 1회 종가 기준
② 수집 값은 전역 캐시 — 호출량은 사용자 수가 아니라 계열 수에 비례 ③ 미마감 당일은
저장·표시하지 않는다 — 마지막 점은 언제나 확정 종가 ④ 해외 계열은 현지 거래일 기준
⑤ 앱은 판정하지 않는다 — met은 확정이 아니라 제안(리마인드)의 트리거다.

실행 환경: FastAPI와 같은 코드베이스의 **워커 프로세스**에서 APScheduler 크론으로
실행한다. Redis 없음 — 큐·락·캐시는 전부 Postgres(Supabase)가 맡는다. API 프로세스는
여러 개일 수 있으나 **워커는 1개**를 전제한다(스케일 필요 시 재검토).

## 2. Provider 추상화

무료 API의 티어 정책은 예고 없이 바뀐다(§10 리스크 — Alpha Vantage 실례). 특정 제공자에
종속되는 코드를 만들지 않도록 모든 소스를 하나의 인터페이스 뒤에 둔다.

### 2.1 공통 인터페이스

```python
class DailyBar(TypedDict):
    date: date                                  # 현지 거래일
    close: Decimal
    high: Decimal | None; low: Decimal | None   # 거시 계열은 None

class SeriesProvider(Protocol):
    name: str                                   # 'fred' | 'ecos' | 'kis' | 'yfinance'
    def fetch_daily(self, code: str, start: date, end: date) -> list[DailyBar]:
        """확정된 일별 값만. 미마감 당일은 포함하지 않는다."""

class QuoteProvider(Protocol):                  # 온디맨드 현재가·환율(§6) — KIS만 구현
    def fetch_quote(self, code: str) -> Quote   # {price, fetched_at}
```

- 미마감 당일을 걸러낼 책임은 구현체에 있다. 호출자는 받은 것을 그대로 upsert한다.
  휴장일은 응답에 행이 없을 뿐 — 거래일 캘린더를 자체 관리하지 않는다(provider 응답이
  정본). 레이트 리밋·인증·재시도도 구현체 내부의 일이다.

### 2.2 FRED

| 항목 | 내용 |
|---|---|
| 인증 | 무료 API 키 1개 (환경변수) |
| 계열 조회 | `series/observations` — 계열 ID(`DFF`, `CPIAUCSL` 등)와 기간으로 일·월·분기 값 |
| 발표 캘린더 | `release_dates` — **계획 §3.3 "다음 점검 일정"의 원천.** 계열이 속한 release의 다음 발표일을 API가 준다 |
| 쿼터 | 무료. 분당 요청 제한이 있으나(120/분 수준 — 착수 시 확인) 이 앱의 호출량에서는 도달하지 않는다 |

- 거시 계열이므로 `high`·`low`는 항상 `None` (`has_intraday=false`).
- 월·분기 계열도 **매일 조회한다** — 비용이 무시할 수준이고, 발표 지연·수정치 반영을
  공짜로 얻는다.
- `release_dates`는 별도의 **주 1회 동기화 잡**으로 가져와 점검 일정 표시에 공급한다.
  저장은 별도 테이블 `series_release_dates(provider, code, release_date)` — 착수 시 확정.

### 2.3 ECOS (한국은행)

| 항목 | 내용 |
|---|---|
| 인증 | 무료 API 키 (발급 즉시, 환경변수) |
| 코드 체계 | 통계표코드 + 항목코드의 계층 구조. `series_catalog.code`에는 `통계표코드/주기/항목코드1[/항목코드2]`를 하나의 문자열로 합쳐 담는다 |
| 일별 환율 | 원/달러 매매기준율 일별 계열 — **PnlSnapshot(§7)과 환차손익 계산의 원천** |
| 쿼터 | 무료. 일 호출 한도 존재(키 등급별 — 착수 시 확인) |

- 거시·환율 계열이므로 `high`·`low`는 `None` — "발표된 값 하나가 그날의 값"(§2.3).
- FRED 같은 발표 캘린더 API는 없는 것으로 보인다(착수 시 확인). 없으면 ECOS 계열의
  점검 일정은 재확인 주기(계획 §3.3 둘째 줄)로 처리한다 — 화면 문장은 둘을 구분하지 않는다.

### 2.4 KIS (한국투자증권)

| 항목 | 내용 |
|---|---|
| 선행 조건 | 계좌 개설 + 앱키·앱시크릿 발급 |
| 토큰 | **접근토큰 수명 1일.** 재발급 호출에 빈도 제한이 있다(착수 시 확인) |
| 일봉 | 국내·해외 주식/지수 기간별 시세 REST — `close`·`high`·`low`를 모두 준다 |
| 현재가 | 국내·해외 현재가 REST — 온디맨드 경로(§6) 전용 |
| 레이트 리밋 | **초당 20건** (계정 단위) |

- 구체 엔드포인트명·TR ID·해외 지수 커버리지는 문서마다 표기가 갈리므로 단정하지
  않는다 — **착수 시 확인 목록(§10)**에 모아 둔다.
- **토큰은 DB에서 공유한다.** `kis_tokens(token, issued_at, expires_at)` 한 행.
  워커와 여러 API 프로세스가 각자 발급하면 재발급 제한에 걸리거나 서로의 토큰을
  무효화할 수 있다(무효화 여부도 착수 시 확인). 만료 임박 시 갱신은
  `select ... for update skip locked`로 한 프로세스만 수행한다.
- **레이트 리미터는 프로세스 안 토큰버킷.** 한도를 프로세스 수로 나눠 보수적으로
  잡는다(예: 워커 10/s, API 프로세스당 4/s). 분산 리미터는 이 호출량(§9)에서 과잉이다.
  배치는 순차 실행이라 리미터가 실제로 일하는 곳은 온디맨드 현재가(§6)뿐이다.

### 2.5 yfinance — 개발 전용

Yahoo 비공식 엔드포인트라 **유료화 시 약관 위반이고 예고 없이 깨진다**(§6 배제 사유).
파이프라인 검증용으로만 쓰고 출시 전 반드시 제거한다.

- `SeriesProvider` 구현체 하나(`YfinanceProvider`)로 격리하고, import가 어댑터 파일
  밖으로 나가지 않게 한다.
- `series_catalog.provider`에 `yfinance` 값은 없다 — CHECK 제약이 개발용 provider가
  DB로 새는 것을 막는다. 개발 환경에서는 **설정 플래그로 `kis`의 구현체를 yfinance
  어댑터로 바꿔 끼운다**(`005930` → `005930.KS` 코드 매핑은 어댑터 내부 일).
- 교체 시나리오: KIS 준비 즉시 플래그만 되돌린다. 스키마·배치·평가 코드는 건드릴 것이
  없어야 하고, 그것이 이 추상화의 합격 기준이다.

## 3. 계열 카탈로그

`series_catalog`는 auto 판정·지켜보는 수치가 참조할 수 있는 계열의 전체 집합이다.
여기 없는 계열은 조건으로 설정할 수 없다 — `watches`에는 FK가 걸려 있고, `scenarios`의
auto 칼럼에도 같은 FK를 거는 것을 착수 시 검토한다.

### 3.1 시드 전략

`supabase/seed.sql`에 초기 항목을 넣는다(01-db-schema §8). 시드의 기준은 "Thesis
Builder가 후보로 올릴 만한 계열"이다.

| kind | 시드 예 | provider |
|---|---|---|
| `index` | 코스피, 코스닥, S&P500, 나스닥 | kis |
| `macro` | 미국 기준금리(DFF), CPI, 실업률, 미 10년물, WTI | fred |
| `macro` | 한국 기준금리, 소비자물가 | ecos |
| `fx` | 원/달러 매매기준율 | ecos |
| `equity` | (시드 없음 — 아래 동적 등록) | kis |

- **개별 주식은 시드하지 않는다.** 노트 대상이 `instruments`에 등록될 때(티커 정규화
  시점) `kind='equity'`로 함께 upsert한다. 주식은 코드 체계가 정형이라 동적 등록이
  안전하지만, 거시 계열은 코드 발굴 자체가 사람 일이라 시드로만 늘린다.

### 3.2 사용자 표현 → 계열 매핑

- 2단계 폼에서 Thesis Builder가 갈래의 질문에서 계열 후보를 뽑아 채울 때(§3.1 2단계)
  이 카탈로그를 검색한다. `search_keywords text[]`가 매핑 보조다 —
  `('기준금리','연준','Fed')` 같은 동의어를 심어 두고, label·keywords에 대한 단순
  텍스트 매칭으로 후보를 추린다.
- 최종 선택은 LLM이 아니라 **사용자가 폼에서 확인**한다. 매핑이 틀려도 조용히 틀린
  조건이 만들어지지 않는다.
- 사전을 어디까지 늘릴지는 오픈 이슈다(§12). 초기에는 시드 수십 개로 시작하고 아래
  미스 로그로 늘린다.

### 3.3 카탈로그에 없는 계열 요청

- Thesis Builder가 매핑 후보를 찾지 못하면 **auto 후보를 제안하지 않는다.** 그 갈래는
  manual로 남는다 — 정상 동작이다(§3.1 "확인 방법이 없는 노트도 정상 동작한다").
- 다만 실패한 표현을 `catalog_misses(expression, note_id, created_at)` 로그로 남긴다.
  시드 확장의 우선순위가 여기서 나온다. 사용자에게는 아무것도 재촉하지 않는다.

## 4. 일일 수집 배치

### 4.1 스케줄 — 시장별로 따로 돈다 (§3.5)

| 잡 | 크론 (타임존 명시) | 대상 |
|---|---|---|
| `collect_kr` | 평일 17:30 Asia/Seoul | kis 국내 주식·지수 |
| `collect_us` | 평일 07:30 Asia/Seoul (미국장 마감 후 — DST 고려해 ET 기준 크론이 안전한지 착수 시 확인) | kis 해외 주식·지수 |
| `collect_macro` | 매일 08:00 Asia/Seoul | fred·ecos 전 계열 |
| `sync_release_dates` | 주 1회 | FRED release_dates → 점검 일정 표시(계획 §3.3) |
| `evaluate_auto` | 각 collect 잡 직후 체이닝 | §5 |
| `pnl_snapshot` | 매일 08:30 Asia/Seoul (collect 이후) | §7 |
| `transition_judgment` | 매일 09:00 Asia/Seoul | judge_end 도래 → pending_judgment (§5.5) |

- 거시를 발표일에만 조회하는 최적화는 하지 않는다 — 매일 전 계열을 훑어도 수십
  건이다(§9). release_dates는 조회 최적화가 아니라 화면 표시용이다.
- 모든 잡은 멱등이고(§4.3), APScheduler `max_instances=1`로 같은 잡의 중복 실행을 막는다.

### 4.2 수집 대상 산출

```sql
-- ① 열린 갈래의 auto 시나리오가 참조하는 계열 (met 여부와 무관 — 아래 주의)
select distinct s.series_provider, s.series_code
  from scenarios s join galae g on g.id = s.galae_id
 where s.resolution_type = 'auto' and g.status = 'open'
union  -- ② 지켜보는 수치
select distinct provider, code from watches
union  -- ③ 잔고 > 0 보유 종목 + 원/달러 환율 (PnlSnapshot의 원천, §7)
select 'kis', /* 보유 종목 */ ...  union  select 'ecos', '<원달러 계열 코드>';
```

- **주의: 수집 대상은 `auto_status='not_met'`로 거르지 않는다.** 01-db-schema §6의
  부분 인덱스는 **평가** 대상용이다. met이 된 뒤에도 갈래가 판정되기 전까지 추이
  차트(UX §7)는 계속 자라야 하므로 수집은 갈래 `open` 기준으로 한다.
- 산출된 계열을 4.1의 잡별로 provider·market으로 갈라 순차 수집한다.

### 4.3 멱등 upsert와 백필

```
for (provider, code) in targets:
    last = max(date) in series_snapshots for (provider, code)   # 없으면 백필 시작점(아래)
    bars = provider.fetch_daily(code, last + 1일, 오늘)          # 구현체가 미마감 당일 제외
    upsert bars  on conflict (provider, code, date) do update    # 수정치 발표도 이 경로로 반영
```

- **소급 수집이 재시도 전략의 전부다.** 어제 배치가 죽었어도 오늘 배치가 `last + 1일`
  부터 가져오므로 구멍이 저절로 메워진다(§8).
- **최초 백필 시작점**: 그 계열을 참조하는 가장 이른 기록일(auto 조건·watch·장부 중
  최소 날짜) − 여유 30일. 차트 기간이 `기록 시점 → 판단 시점` 고정이라 그보다 과거는
  필요 없다.
- upsert가 `do update`인 이유: 거시 계열은 수정치(revision)가 발표된다. 최신 값으로 덮는다.

## 5. auto 조건 평가

`evaluate_auto` 잡이 수집 직후 실행한다. 평가 대상은 부분 인덱스 그대로 —
`resolution_type='auto' and auto_status='not_met'` (+ 갈래 open).

### 5.1 관측 규칙의 구현 (§2.3 — 시스템 고정)

> 판단 시점까지 기간 중 **한 번이라도** 목표에 닿으면 달성. **장중 포함.**

- 관측 창: `[조건 설정일, galae.judge_end]` — 시나리오에는 기한 필드가 없다. 장중
  포함 = `high`·`low`를 쓴다. 거시 계열(`has_intraday=false`)은 `close` 하나가 그날의 값.
- **met은 단조다.** 한 번 닿으면 달성이므로 되돌림이 없다. "달성/미달성이 바뀌는
  순간"(§3.5)은 곧 `not_met → met` 전이 한 종류다.
- auto 조건을 나중에 채우면 관측 창 전체가 **소급 판정**된다(§3.1). 평가 로직이
  어차피 기간 전체를 훑으므로 별도 경로가 필요 없다.

### 5.2 comparator 4종 (§2.3)

그날의 도달 범위를 `[lo, hi]`로 둔다 — 주식·지수는 `[low, high]`, 거시는 `[close, close]`.

| comparator | 그날 닿음의 정의 | 비고 |
|---|---|---|
| `gte` | `hi >= target_value` | |
| `lte` | `lo <= target_value` | |
| `between` | `[lo, hi] ∩ [target_low, target_high] ≠ ∅` | 스쳐 지나가도 닿은 것이다 |
| `change_pct` | 기준값 `base = baseline_date의 close`. `target_value > 0`이면 `(hi−base)/base×100 >= target_value`, 음수면 `(lo−base)/base×100 <= target_value` | 부호가 방향이다 |

```
evaluate(scenario):
    window = snapshots(series, 설정일 .. min(오늘, judge_end))
    for day in window:                      # 날짜 오름차순
        if touched(day, scenario):
            scenario.auto_status = 'met'; scenario.met_at = day.date
            emit AutoConditionMet(scenario_id)      # 5.4
            return
    scenario.progress = progress(window, scenario)  # 5.3
```

### 5.3 progress — 목표까지의 거리 0~1

시나리오 카드의 `95,000원까지 · 지금 92,800`(§3.4)와 축약 차트의 `목표의 95%`(UX §7)에
쓰는 캐시 값이다.

- 시작값 `start` = 조건 설정일 이전의 마지막 `close` (`series_snapshots`에서 파생 —
  저장하지 않는다).
- `gte`: `clamp((최고 도달값 − start) / (target − start))` · `lte`: 대칭.
  `between`: start에서 가까운 경계까지의 거리 비율. `change_pct`: 달성 변화율 ÷ 목표
  변화율. 분모가 0(설정 시점에 이미 목표)이면 1.0. met이면 항상 1.0.
- 정본은 `series_snapshots`이고 `progress`·`auto_status`·`met_at`은 재평가로 언제든
  복원되는 캐시다(01-db-schema §1).

### 5.4 met 전이 → 리마인드 트리거 발행

전이 순간에 이벤트 기반 리마인드(§3.2)를 **발행만** 한다. Redis가 없으므로 큐는
Postgres다 — `notifications`에 `kind='auto_condition_met'` 행을 insert하고, 문구
구성·발송·하루 1통 묶음·감쇠는 리마인드 파이프라인(Reminder Curation)의 일이다.

- 이벤트는 "달성 사실"만 담는다. 판정이 아니라 제안(§2.3)이므로 시나리오 상태를
  확정하는 필드는 페이로드에 없다.
- 지켜보는 수치는 트리거를 만들지 않는다(§3.5 — 목표가 없으므로 "바뀌는 순간"이 없다).
- 즉시 알림이냐 일일 배칭이냐는 오픈 이슈(§12)다. 배치가 하루 1회라 현 구조에서는
  자연히 일일 배칭이고, 나중에 즉시화해도 이벤트 행 구조는 안 바뀐다.

### 5.5 judge_end 도래 → pending_judgment 전이

`transition_judgment` 잡: `galae.status='open' and judge_end < today`인 갈래의 active
시나리오를 `pending_judgment`로 전이하고 `kind='judgment_due'` 알림 행을 만든다.

- **이 전이는 검증기의 일이 아니다**(§3.1 — 시간이 지나 생기는 상태 변화는 시나리오
  상태 전이가 담당한다). 검증기는 저장 시점의 순수 함수, 이 잡은 시간의 경과를 옮기는
  유일한 자리다. 같은 규칙을 두 곳에 두지 않는다.
- 자동 실패 처리가 아니다 — `rejected`로 넘기는 코드는 존재하지 않는다(§3.4). 결론은
  결과 확인 화면에서 사용자가 낸다.

## 6. 온디맨드 현재가·환율

보유 종목 화면·원화 환산 표기의 경로다(§3.9). 배치와 무관하게 **화면을 열 때** 돈다.

```
GET /quotes?symbols=...
  ① quote_cache에서 조회 → fetched_at이 TTL(기본 5분) 이내면 그대로 반환
  ② 묵었으면 KIS fetch_quote → upsert → 반환
  ③ 응답에는 항상 fetched_at 포함 → 화면이 "조회 시점"을 표기한다 (§3.9)
```

- **캐시는 인프로세스가 아니라 DB 테이블이다.** API 프로세스가 여러 개라 프로세스
  메모리 캐시는 프로세스 수만큼 KIS를 때린다. 쓰기 한 번의 비용 < KIS 호출 절약이고,
  Redis 없이 전역 TTL을 얻는 가장 단순한 자리가 Postgres다.

```sql
create table quote_cache (
  provider text not null,  code text not null,
  price numeric(18,4) not null,  fetched_at timestamptz not null,
  primary key (provider, code)
);
```

- 동시 만료 경쟁은 막지 않는다 — 같은 종목을 두 요청이 동시에 갱신해도 KIS 호출이
  한 번 더 나갈 뿐이고, 락으로 직렬화하는 비용이 더 크다. 리미터(§2.4)가 최후 방어선이다.
- **부분 실패 응답**(§3.9): 종목별 독립 조회이므로 실패한 종목만
  `{symbol, error: 'fetch_failed'}`로 내려보낸다. 화면은 그 칸만 `조회 실패 · 다시
  시도`로 두고 목록·수량·넛지는 정상 동작한다. 묵은 캐시값을 대신 주지 않는다 —
  시점 표기가 거짓이 되는 것보다 빈 칸이 낫다.
- 환율(원/달러)도 같은 경로다. KIS 환율 조회 가부는 착수 시 확인 — 안 되면 이 경로만
  대체 소스를 어댑터로 끼운다(일별 확정값은 어차피 ECOS가 정본이다).
- 장중/폐장 분기 로직은 두지 않는다(§3.9 확정).

## 7. PnlSnapshot 야간 배치

손익 추이 차트(§3.9, UX §3.7)의 원천. **장부 × 일별 종가 × 일별 환율**로 사용자·scope별
하루 한 점을 적재한다.

| scope | 값 | 통화 | 필요한 계열 |
|---|---|---|---|
| `kr` | 국내 종목 누적 손익(실현+평가) | 원 | 국내 종가 |
| `us` | 미국 종목 누적 손익 | 달러 | 미국 종가 (환산하지 않는다 — §3.9) |
| `fx` | 환차손익(실현+평가) | 원 | 원/달러 환율 |

```
pnl_snapshot 잡:
    for user in 장부가 있는 사용자:
        dirty_from = pnl_recalc_marks[user] or 마지막 스냅샷 다음 날
        delete pnl_snapshots where user, date >= dirty_from
        for d in dirty_from .. 마지막 확정 거래일:      # 미마감 당일 제외
            insert (user, scope, d, 누적손익(장부, 종가[d], 환율[d]))  # scope별 3행
        clear mark
```

- **소급 재계산은 변경 감지 → 구간 삭제 → 재적재다.** 매매·환전의 쓰기 API가 저장
  트랜잭션 안에서 `pnl_recalc_marks(user_id, from_date)`에 영향 시작일(수정된 거래의
  가장 이른 `traded_at`)을 `least()` upsert로 남기고, 배치는 그 날짜부터 다시 쓴다.
  변경 스캔이 아니라 쓰기 시점 마킹이라 놓칠 길이 없다.
- price가 null인 거래(과거 보유분)가 섞인 종목은 계산에서 제외하고 제외 사실을
  표기한다(§3.9와 같은 규칙).
- **계산식은 이 문서의 것이 아니다.** 잔고·평단·실현손익·환차손익의 정본 수식은
  02-backend.md의 장부 모듈이고, 이 배치는 그 순수 함수에 (장부, 그날의 종가·환율)을
  넣어 돌리는 **오케스트레이션만** 한다. 수식이 두 곳에 있으면 화면의 현재 손익과
  추이 차트가 어긋난다.
- 휴장일은 그 시장의 점을 만들지 않는다. scope별로 거래일이 달라도 축이 탭별로
  분리돼 있어 문제가 없다.

## 8. 장애·재시도

| 상황 | 처리 |
|---|---|
| 배치 실행 누락(워커 다운·배포) | 다음 배치가 `last + 1`부터 소급 수집(§4.3). 별도 복구 절차 없음 |
| provider 한 곳 장애 | **계열 단위 격리.** 실패한 계열만 건너뛰고 나머지는 진행한다. FRED가 죽어도 KIS 수집·평가는 돈다 |
| 일시 오류(429·5xx) | 구현체 내부에서 지수 백오프 2회 재시도 후 포기 — 어차피 내일 소급된다 |
| KIS 토큰 만료·발급 실패 | 공유 토큰 갱신 재시도. 실패 시 kis 계열만 스킵 |
| 평가·전이 잡 | 수집과 독립적으로 항상 실행한다. 데이터가 어제까지뿐이면 어제까지로 평가한다 — met은 단조라 늦게 잡혀도 판정이 틀어지지 않고 `met_at`은 실제 닿은 날짜로 기록된다 |
| PnlSnapshot | 종가·환율이 없는 날짜는 그 날짜에서 멈추고 mark를 유지 — 다음 날 이어서 적재 |

- 리서치의 재시도와 성질이 다르다(§6) — 수치는 소급이 무손실이므로 "실패한 그 시각에
  다시 실행"할 이유가 없다. 재시도 큐를 만들지 않는 것이 설계다.
- 로깅: 잡 실행마다 (잡 이름, 대상 계열 수, 성공/실패 수, 소요 시간)을 구조화 로그로
  남긴다. 같은 계열이 N일 연속 실패하면 운영자 이메일 하나만 보낸다 — 사용자 화면에는
  아무것도 띄우지 않는다. 어차피 소급으로 메워진다.

## 9. 쿼터·비용 추정

호출량은 사용자 수와 무관하고 **서로 다른 계열의 개수**에 비례한다(§3.5).

| 경로 | 하루 호출량 (계열 수 S, 보유 종목 종류 H 기준) | 초기 추정치 |
|---|---|---|
| 일일 수집 | S회 (계열당 1회 — 증분 범위 조회) | S ≈ 50~200 → 수십~수백 건 |
| release_dates 동기화 | 주 1회 × FRED release 수 | 주 수십 건 |
| 온디맨드 현재가 | 화면 열람 × H ÷ TTL 히트율 | 사용자 수백 명이어도 시간당 수백 건 이하 |
| PnlSnapshot | 외부 호출 0 — 전부 DB 안에서 계산 | 0 |

- FRED·ECOS는 무료 한도 대비 두 자릿수 이상 여유. KIS는 20/s 대비 배치가 순차라 무관.
- 계열 수가 만 단위가 되기 전에는 어떤 유료 플랜도 필요 없다. 이것이 §6이 말하는
  "무료 소스만으로 상용 운영이 가능한 비용 구조"의 구체 수치다.

## 10. 착수 시 확인 목록

- [ ] KIS: 국내/해외 일봉·현재가의 정확한 엔드포인트와 TR ID, 해외 **지수** 커버리지
- [ ] KIS: 접근토큰 재발급 빈도 제한, 재발급 시 기존 토큰 무효화 여부
- [ ] KIS: 환율 현재가 조회 가부 (안 되면 온디맨드 환율만 대체 어댑터)
- [ ] KIS: 모의투자 계정으로 개발 가능한 범위
- [ ] FRED: 분당 요청 한도 현재값, release_dates ↔ 계열 매핑 방법
- [ ] ECOS: 일 호출 한도, 발표 캘린더 부재 확인, 원/달러 일별 계열의 정확한 통계코드
- [ ] scenarios auto 칼럼에 series_catalog FK를 걸지 (01-db-schema 보강)
- [ ] 운영 보조 테이블 5종(`series_release_dates`·`quote_cache`·`pnl_recalc_marks`·
      `kis_tokens`·`catalog_misses`)의 마이그레이션 작성 — 정의는 01-db-schema §3.12에 반영됨
- [ ] 미국장 마감 크론의 DST 처리 방식 (ET 타임존 크론 vs 고정 KST + 여유 시각)
