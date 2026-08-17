# 백엔드 — FastAPI 애플리케이션 설계

> 상위 문서: [`development-plan.md`](../development-plan.md) §13(구현 마스터플랜).
> 이 문서는 `apps/api`(FastAPI)의 구조·API·도메인 로직을 다룬다. 테이블 구조의 정본은
> [`01-db-schema.md`](./01-db-schema.md), AI 에이전트 상세는 [`04-ai-agents.md`](./04-ai-agents.md),
> 시세 수집 상세는 [`05-series-service.md`](./05-series-service.md)가 담당한다.
> 이 문서에서 에이전트와 시세는 **인터페이스(경계)만** 언급한다.

## 1. 스택과 전제

| 항목 | 결정 |
|---|---|
| 런타임 | Python 3.12+, 패키지 관리는 uv |
| 프레임워크 | FastAPI + Pydantic v2 |
| DB 접근 | SQLAlchemy. **스키마를 생성하지 않는다** — DDL 정본은 `supabase/migrations/`이고 ORM 모델은 반영만 한다 (01-db-schema §8) |
| 접속 권한 | Supabase service role. RLS를 우회하므로 **권한 검사는 애플리케이션 책임**이다 (01-db-schema §7) |
| 인증 | 클라이언트가 Supabase Auth로 로그인 → 발급된 JWT를 FastAPI가 검증 |
| 배치·비동기 | 같은 코드베이스의 별도 워커 프로세스. 크론은 APScheduler, 온디맨드 AI 작업은 Postgres `jobs` 테이블 + `FOR UPDATE SKIP LOCKED` 폴링. MVP에 Redis 없음 |
| LLM | Claude API. 호출은 전부 워커에서 — HTTP 요청 안에서 LLM을 기다리지 않는다 |

> `jobs` 테이블(id, kind, payload, status, attempts, run_after, locked_at 수준)은
> [`01-db-schema.md`](./01-db-schema.md)에 아직 없다. 사용자 데이터가 아니라 인프라
> 테이블이므로 워커 구현과 함께 마이그레이션을 추가한다 — 스키마 문서에 반영할 것.

**검증 규칙·확률 재분배의 정본은 서버 Python 단일 구현이다.** development-plan.md §3.1의
"규칙 정의를 한 패키지로 공유" 원칙은 TS/Python 혼합 스택에서는 성립하지 않으므로 이렇게 조정한다:

- 노트 검증은 클라이언트가 **미리보기 진입 시 `POST /notes/validate`를 호출**해 받는다.
  즉시 피드백의 실체가 미러 구현이 아니라 API 호출이다. 미리보기 진입은 저장당 한 번
  수준이라 왕복 비용이 문제되지 않는다.
- 확률 슬라이더만은 드래그 중 실시간 반응이 필요하므로 **TS 미러 구현을 허용**하되,
  `fixtures/`의 골든 테스트 벡터를 **양쪽 CI에서 같이 돌려** 어긋남을 기계적으로 잡는다(§6).
  저장 시에는 어차피 서버가 재계산한 값만 기록한다.

## 2. 애플리케이션 구조

```
apps/api/
  pyproject.toml                # uv 관리
  src/app/
    main.py                     # FastAPI 앱 조립
    core/
      config.py                 # 환경 변수 (Supabase URL·키, Claude 키, ...)
      auth.py                   # JWT 검증 의존성 (§3)
      errors.py                 # 에러 모델·핸들러 (§10)
    routers/                    # HTTP 계층 — 얇게. 파싱·인증·응답 변환만
      notes.py conversations.py galae.py judgments.py reviews.py
      ledger.py quotes.py research.py advisor.py series.py
    services/                   # 유스케이스 계층 — 트랜잭션 경계가 여기 있다
      note_saver.py             # 노트 저장 플로우 (§9)
      judgment.py               # 판정·시점 재설정 (§8)
      ledger_service.py         # 장부 조회 조립 (도메인 계산 + 시세)
      job_enqueue.py            # jobs 테이블 INSERT
    domain/                     # ★ 순수 함수만. DB·HTTP·시각(now)에 의존하지 않는다
      validation/               # 노트 검증기 (§5) — 규칙당 함수 하나
      probability.py            # 확률 재분배 (§6)
      ledger/                   # 장부 계산 (§7) — holdings.py, fx.py, xirr.py, pnl.py
    db/
      models.py                 # SQLAlchemy 모델 (마이그레이션 반영)
      repositories/             # 쿼리 모음 — 모든 메서드가 user_id를 받는다 (§3)
    agents/                     # Claude 호출 인터페이스 (상세는 04-ai-agents.md)
    series/                     # 시세·계열 경계 인터페이스 (상세는 05-series-service.md)
  worker/
    scheduler.py                # APScheduler 엔트리 (일 배치·판정 스캔·리마인드)
    poller.py                   # jobs 폴러 (SKIP LOCKED)
  tests/
```

설계 원칙은 하나다. **도메인 규칙은 순수 함수로 둔다.** 검증기·확률 재분배·장부 계산은
전부 입력 → 출력만 있는 함수여서 DB 없이 단위 테스트가 된다. development-plan.md가
"규칙은 각각 순수 함수 하나로 만든다"(§3.1), "잔고는 파생 값이다"(§3.9)라고 못 박은 것의
코드 구조 번역이다. `services/`는 그 순수 함수들을 트랜잭션으로 감싸는 역할만 하고,
`routers/`는 그 위에서 HTTP만 한다. 의존 방향은 `routers → services → domain/db` 한쪽이다.

## 3. 인증·권한

- 로그인·토큰 갱신은 **클라이언트 ↔ Supabase Auth 직통**이다. FastAPI에 로그인 엔드포인트는 없다.
- 모든 API 요청은 `Authorization: Bearer <supabase JWT>`를 요구한다. `core/auth.py`의
  FastAPI 의존성이 서명(프로젝트 JWT secret 또는 JWKS)·만료·`aud`를 검증하고
  `sub` 클레임을 `user_id: UUID`로 꺼낸다. 실패는 일괄 401.

```python
async def current_user_id(token: str = Depends(bearer)) -> UUID:
    claims = verify_supabase_jwt(token)     # 서명·exp·aud 검증
    return UUID(claims["sub"])
```

- **service role 접속은 RLS를 우회하므로, user_id 스코핑이 유일한 실질 방어선이다.**
  규약: `repositories/`의 모든 사용자 데이터 메서드는 첫 인자로 `user_id`를 받고
  WHERE 절에 반드시 넣는다. 노트 하위 리소스(갈래·시나리오·회고 등)는 소유 노트 경유로
  검사한다 — RLS 정책(01-db-schema §7)과 같은 모양을 애플리케이션에서 반복하는 것이다.
  남의 리소스 접근은 403이 아니라 **404**로 답한다(존재 여부 자체를 흘리지 않는다).
- 전역 테이블(`instruments`·`series_catalog`·`series_snapshots`)은 읽기에 user_id가 없다.

## 4. API 엔드포인트 인벤토리

화면 대응은 [`ux-design.md`](../ux-design.md) §9.4의 개발명↔화면명 표를 따른다.
LLM이 개입하는 엔드포인트(∗ 표시)는 요청을 `jobs`에 넣고 즉시 202를 돌려주며,
클라이언트는 결과 리소스를 폴링한다 — HTTP 타임아웃 안에 LLM을 기다리지 않는다.

| 영역 (화면) | 엔드포인트 | 설명 |
|---|---|---|
| 프로필 | `GET/PATCH /me` | 표시명·면책 동의 시각·리마인드 채널 |
| 1단계 대화 | `POST /conversations` | 대화 시작 (보유 종목 넛지에서 오면 symbol을 시드로) |
| | `POST /conversations/{id}/messages` ∗ | 사용자 발화 추가 + Thesis Builder 턴 실행 |
| | `GET /conversations/{id}` | 메시지·draft_note 조회 (이탈 후 재개) |
| 노트 저장 | `POST /conversations/{id}/build` ∗ | 대화 → 노트 초안 생성 (미리보기 데이터). `다시 만들기`도 이 경로 — 사용자가 손댄 블록은 보존 |
| | `POST /notes/validate` | **검증기 실행 (§5).** 미리보기 진입 시 클라이언트가 호출. 저장하지 않고 `Issue[]`만 반환 |
| | `POST /notes` | 트랜잭션 저장 (§9). conversation attach 포함 |
| 노트 | `GET /notes` · `GET /notes/{id}` | 목록·상세. `is_complete`는 저장 안 하고 여기서 계산해 싣는다 (01-db-schema §3.3) |
| | `PATCH /notes/{id}` | 본문 수정 (검증기 재실행). 대화는 건드릴 수 없다 |
| | `POST /notes/{id}/archive` | 모든 갈래 judged 후 접기 |
| 2단계 폼 | `PATCH /scenarios/{id}/resolution` | auto 조건 설정·수정. 사후 수정은 `auto_condition_edits`에 이력 (§2.3) |
| | `PATCH /galae/{id}/deadline` | 판단 시점 입력·재설정 → `galae_deadline_resets` 이력 |
| | `POST /notes/{id}/watches` · `DELETE /watches/{id}` | 지켜보는 수치 |
| **확률** | **`PATCH /galae/{id}/probabilities`** | **확률 쓰기 경로는 이것 하나뿐이다 (§6).** `{changed: {scenario_id, value}, locked_ids: []}` → 서버가 재분배해 합 100인 상태만 기록 |
| 홈·리마인드 | `GET /home` | 리마인드 피드(우선순위 순) + 세로 타임라인(판단 시점 순) 데이터 |
| | `GET /reminders/{notification_id}` | 리마인드 상세 3단 콘텐츠. **첫 조회 시 `notifications.opened_at` 기록** — 미열람 감쇠(§3.2)의 근거. 프론트 라우트 `/reminders/[notificationId]`와 1:1 |
| | `POST /reminders/{notification_id}/keep` | `그대로 봅니다` — 확률 이력을 만들지 않고 검토일만 갱신 (UX §3.5) |
| 판정 | `GET /judgments/pending` | `pending_judgment` 갈래 목록 (홈 리마인드 피드용) |
| | `POST /galae/{id}/judgment` | 3선택지 확정 (§8). `그대로 됨`/`틀렸음`이면 회고 초안 잡 생성 |
| 회고 | `GET /reviews/{id}` · `PATCH /reviews/{id}` | AI 초안 열람 / `logic_verdict`·`user_narrative` 기록 |
| 장부 | `GET/POST/PATCH/DELETE /trades` | 매매·배당 기록. 쓰기 시 매도 수량 결정론적 검사 (§7) |
| | `GET/POST/PATCH/DELETE /exchanges` | 환전 기록 |
| | `GET /portfolio?tab=kr\|us\|fx` | 탭별 보유 목록·요약(XIRR·손익 합) — 전부 그 자리 파생 계산 |
| | `GET /portfolio/pnl-history?tab=` | 손익 추이 (`pnl_snapshots` 캐시 조회) |
| 현재가 | `GET /quotes?symbols=` | 온디맨드 조회. Series Service의 TTL 전역 캐시 통과, 조회 시점 동봉 (§3.9) |
| 차트 | `GET /series/{provider}/{code}?from=&to=` | `series_snapshots` 구간 조회 (추이 차트 원천) |
| | `GET /series/search?q=` | 계열 카탈로그 검색 (2단계 폼 후보용) |
| 리서치 | `POST /notes/{id}/research` ∗ | 온디맨드 리서치 트리거 |
| | `GET /notes/{id}/research-items` · `PATCH /research-items/{id}` | 새 정보 서랍 / adopted·dismissed 피드백 |
| 어드바이저 | `POST /notes/{id}/advisor` ∗ | "지금 어떻게 해야 할까?" 트리거 |
| | `GET /notes/{id}/advisor-opinions` · `PATCH /advisor-opinions/{id}` | 의견 열람 / user_action 사후 기록 |

**만들지 않는 엔드포인트** — 없음이 곧 설계다.

- `PATCH /scenarios/{id}` 로 **확률을 고치는 경로는 없다** (development-plan.md §3.1).
  개별 시나리오 확률 수정 API가 존재하지 않으므로 합≠100 상태를 만들 방법이 없다.
- 대화 메시지의 수정·삭제 엔드포인트는 없다 (P2 원본 불변 — DB 트리거가 2차 방어).
- 잔고·평단을 쓰는 엔드포인트는 없다 — 전부 파생 계산이다.
- 저장된 노트의 재생성(`build`) 엔드포인트는 없다 — 재생성은 미리보기 단계까지만이다.

## 5. 노트 검증기 — `domain/validation/`

development-plan.md §3.1의 구현 번역이다. **LLM에게 묻지 않는다. 결정론적 코드가 검사한다.**

세 층은 실행 위치가 다르다.

| 층 | 구현 | 실행 지점 |
|---|---|---|
| 1. 구조 | Pydantic 모델 파싱 (Thesis Builder 출력 스키마) | `build` 잡 내부. 실패 시 최대 2회 LLM 재시도 → 그래도 실패면 대화로 되돌리고 **부분 저장하지 않는다** |
| 2. 출처 대조 | `quoted_from` 블록·`premise.statement`의 문자열을 원본 대화에서 실제 검색 (공백 정규화 후 부분 문자열 일치) | `build` 결과 조립 시 + 저장 시 재검. 못 찾으면 인용 표시를 떼고 `authorship: ai`로 강등 |
| 3. 규칙 | 규칙당 순수 함수 하나: `(note_draft) -> Issue \| None` | `POST /notes/validate`(미리보기)와 `POST /notes`·`PATCH /notes/{id}`(저장) — **서버가 정본** |

```python
@dataclass(frozen=True)
class Issue:
    code: str          # 'NO_DEADLINE', 'SINGLE_SCENARIO', ...
    severity: Literal['blocking', 'ask', 'incomplete', 'notice']
    field: str
    message: str       # UI가 그대로 쓸 완성된 문장. 코드 번역이 아니다
    fix: Fix | None    # {label, action} — "지금 추가하기" 바로가기

def validate_note(draft: NoteDraft) -> list[Issue]:
    return [i for rule in RULES if (i := rule(draft))]
```

규칙 표(development-plan.md §3.1)를 그대로 옮긴다. **blocking은 `NO_TARGET`·`NO_THESIS`
둘뿐이고, 나머지는 전부 저장된다.** `ask`(NO_DEADLINE·SINGLE_SCENARIO·NO_PREMISE·
NO_GALAE_QUESTION)는 저장 요청에 `acknowledged: true`가 없을 때 409로 Issue 목록을
돌려주고, 클라이언트가 되묻기 화면(저장당 1회, 한 화면)을 보여준 뒤 같은 요청을
`acknowledged: true`로 재전송하면 저장한다. 서버는 되묻기 횟수를 관리하지 않는다 —
"1회만 묻는다"는 UI 규약이고, 서버는 ack 유무만 본다.

검사 시점은 저장·수정 시(서버)와 미리보기 진입 시(클라이언트가 validate 호출)뿐이다.
**주기적 재검사는 하지 않는다** — 시간 경과에 따른 상태 변화는 §8의 상태 전이가 담당한다.
확률 합도 검사 대상이 아니다 — §6이 어긋날 수 없게 만든다.

## 6. 확률 재분배 — `domain/probability.py`

쓰기 경로가 `PATCH /galae/{id}/probabilities` 하나뿐이고, 그 안에서 이 순수 함수가 돈다.

```python
def redistribute(
    current: dict[ScenarioId, int],      # 현재 배분 (전부 null이면 빈 dict)
    residual_id: ScenarioId,             # `그 외 예상 못한 전개`
    changed: tuple[ScenarioId, int],     # 사용자가 움직인 값
    locked: set[ScenarioId],
) -> dict[ScenarioId, int]:              # 합 100, 전부 5의 배수
```

development-plan.md §3.1의 4단계 그대로:

1. 새 값을 5단위로 스냅하고, 잠긴 값들과 잔여 슬롯 최소치(5)의 자리를 뺀 상한으로 자른다
2. 남은 몫을 나머지 시나리오들의 기존 비율대로 나눈다
3. 각각 5단위로 내린 뒤 부족분을 **최대 잔여법**으로 채워 합계를 정확히 100으로
4. 잔여 슬롯이 5 미만이면 가장 큰 항목에서 빌려와 채운다

불변식 셋 — **합 = 100, 모든 값 5의 배수, residual ≥ 5.** 함수가 1차 방어,
DB deferred constraint trigger(01-db-schema §4.1)가 최후 방어다.
**시나리오가 residual 포함 하나뿐이면(=답이 혼자면) 재분배하지 않고 전부 null로 둔다** —
혼자인 답에 100%를 넣으면 사용자가 표현한 적 없는 확신을 앱이 만들어내는 셈이다.
갱신 트랜잭션은 `scenarios.probability` UPDATE와 `probability_entries` INSERT를 함께 쓴다.

**골든 테스트 벡터**: §3.1에서 참조 구현으로 검증한 경우들을 `fixtures/probability.json`에
담는다. 서버(pytest)와 웹의 TS 슬라이더 미러(vitest)가 **같은 파일을 읽어** 각자 CI에서 돌린다.

```json
{ "cases": [
  { "name": "raise A to 80",
    "start":  {"A": 65, "B": 25, "residual": 10},
    "changed": ["A", 80], "locked": [],
    "expect": {"A": 80, "B": 15, "residual": 5} },
  { "name": "cap at 95",      "changed": ["A", 100], "expect": {"A": 95, "B": 0,  "residual": 5} },
  { "name": "drop A to 0",    "changed": ["A", 0],   "expect": {"A": 0,  "B": 70, "residual": 30} },
  { "name": "snap 63 to 65",  "changed": ["A", 63],  "expect": {"A": 65, "B": 25, "residual": 10} },
  { "name": "locked B",       "changed": ["A", 80],  "locked": ["B"],
    "expect": {"A": 70, "B": 25, "residual": 5} }
] }
```

## 7. 장부 계산 — `domain/ledger/`

development-plan.md §3.9의 수식을 함수로 옮긴다. 원천은 `trades`·`exchanges`·
`series_snapshots` 셋뿐이고(01-db-schema §5), 출력은 저장하지 않는다(`pnl_snapshots` 캐시 제외).
전 함수가 순수 — 장부 리스트를 받아 값을 돌려준다. 금액은 전부 `Decimal`.

```python
# holdings.py — 종목별 잔고·평단·손익. 이동평균법(매수 시 가중평균, 매도 시 평단 유지)
def compute_position(trades: list[Trade]) -> Position:
    # Position: qty, avg_price | None, realized_pnl, dividend_sum, has_null_price
    # price null 거래가 섞인 종목은 avg_price = None → 화면 표기 `단가 없음`
def unrealized(pos: Position, last_price: Decimal) -> Decimal   # (현재가 − 평단) × 잔고

# 매도 수량 > 잔고 — 결정론적 검사. 쓰기 API(생성·수정·삭제)마다 실행한다.
# 과거 날짜 삽입·수정은 그 이후 전 구간의 잔고를 바꾸므로, 해당 종목의 전체 이력을
# (traded_at, created_at) 순으로 다시 걸어 어느 시점에서도 잔고가 음수가 되지 않는지 본다.
def check_sell_feasible(trades_after_change: list[Trade]) -> LedgerViolation | None

# fx.py — 환전은 달러의 매매. 주식과 같은 이동평균 코드가 돈다 (§3.9 "계산 코드도 같은 것")
def fx_position(exchanges: list[Exchange]) -> FxPosition
    # 환전 원금 잔량 = Σ(₩→$) − Σ($→₩), 평균 취득 환율(이동평균), 실현 환차손익 누적.
    # 재환전이 원금 잔량을 넘는 부분은 환차손익을 주장하지 않는다 — rate null 건은 계산 제외
def unrealized_fx(fx: FxPosition, current_rate: Decimal) -> Decimal
def usd_cash(trades: list[Trade], exchanges: list[Exchange]) -> Decimal
    # Σ환전(₩→$) + Σ매도대금 + Σ배당 − Σ매수대금 − Σ재환전. 음수여도 차단하지 않는다 —
    # API가 `기록이 맞지 않습니다` 안내 플래그만 얹는다 (§3.9)

# xirr.py — 포트폴리오 수익률 (금액가중)
def build_cashflows_krw(trades, exchanges, valuation: Valuation) -> list[Cashflow]
    # 원화 전체: 환전의 원화 금액(rate × usd_amount)만 흐름으로. 달러 세계 내부 거래는 제외
def build_cashflows_usd(trades, valuation) -> list[Cashflow]
    # 달러 축: 달러 흐름만 — 환율을 배제한 종목 판단의 성적
def xirr(flows: list[Cashflow]) -> float | None
    # 뉴턴법 + 이분법 폴백. 수렴 실패·흐름 1개 이하면 None → 화면은 빈 값
    # price null 종목은 흐름에서 제외하고, 응답에 excluded_symbols로 제외 사실을 싣는다

# pnl.py — 손익 추이 재구성 (야간 배치·소급 재계산 공용)
def reconstruct_pnl(trades, exchanges, closes: SeriesLookup, fx_rates: SeriesLookup,
                    scope: Literal['kr','us','fx'], dates: list[date]) -> list[PnlPoint]
    # 날짜마다 장부를 그 시점까지 잘라 잔고·평단을 재구성하고 그날 종가로 평가.
    # 미마감 당일 제외. 과거 매매 추가·수정 시 워커가 해당 구간을 삭제 후 재적재
```

`services/ledger_service.py`가 이 순수 함수들에 시세(`GET /quotes` 경로와 같은 캐시)를
붙여 `GET /portfolio` 응답을 조립한다. 탭 분류는 사용자가 고르지 않고
`instruments.market`에서 나온다(01-db-schema §3.2).

## 8. 상태 전이 — 판정과 시점 재설정

상태는 검증기가 아니라 전이 로직이 바꾼다(§3.1 "두 곳에서 같은 일을 하면 어긋난다").

```
active ──(judge_end 도래: 야간 스케줄러 스캔)──▶ pending_judgment ──(사용자 확인)──▶ confirmed / rejected
   ▲                                                   │
   └────────────(`시기를 다시 본다`: judge_end 재설정)───┘
```

- **도래 스캔**: APScheduler 일 배치가 `galae where status='open' and judge_end <= today`
  (부분 인덱스, 01-db-schema §6)를 훑어 소속 시나리오를 `pending_judgment`로 바꾸고
  리마인드(`judgment_due`)를 만든다. **앱은 여기까지만 한다 — 자동으로 confirmed/rejected를
  만드는 코드 경로는 존재하지 않는다** (§2.3 "제안까지만").
- **auto 제안**: 판정 화면에 싣는 제안은 §3.5의 표를 그대로 구현한다 — 달성 답이 정확히
  하나면 그 답, 하나도 없고 complement가 있으면 complement, 둘 이상이면 제안하지 않고
  양쪽 현황을 나란히 보여준다. 달성 여부(`auto_status`)는 배치 평가 캐시를 읽는다.
- **`POST /galae/{id}/judgment`** 는 세 선택지를 받는다.

| choice | 트랜잭션 내용 |
|---|---|
| `confirmed` / `rejected` | 실현 시나리오 확정, 각 시나리오 status 갱신(+`status_reason` 선택), `galae.status = 'judged'`, `reviews` 행 생성, 회고 초안 잡(`review_draft`) enqueue |
| `reset_deadline` | `judge_end` 갱신 + `galae_deadline_resets` 이력 INSERT, 시나리오 전부 `active` 복귀. 회고는 열리지 않는다 |

- 회고 초안이 완성되면(`reviews.ai_draft`·`drafted_at` 채움) 사용자는 `PATCH /reviews/{id}`로
  논리 축(`logic_verdict`)과 서술을 얹는다. 안 써도 재촉하지 않는다(P5) — 서버는 마감·독촉
  스케줄을 갖지 않는다.

## 9. 노트 저장 플로우 — `services/note_saver.py`

```
1단계 대화 (conversations, note_id = null, draft_note에 진행 상태)
  → POST /conversations/{id}/build   : Thesis Builder 출력 → 1층(스키마)·2층(출처 대조)
  → 미리보기: POST /notes/validate   : 3층 규칙 → Issue[] (즉시 피드백)
  → 사용자 수정 (derived 블록 확인 포함) · 필요 시 build 재실행(손댄 블록 보존)
  → POST /notes                      : 서버 재검증 → ask 미확인이면 409 → 트랜잭션 저장
```

저장 트랜잭션 하나에서 전부 일어난다. 부분 저장은 없다.

```python
def save_note(user_id, conversation_id, draft, acknowledged: bool):
    with tx:
        issues = validate_note(draft) + verify_quotes(draft, conversation)   # 서버가 정본
        if has_blocking(issues): raise DomainError(...)
        if has_ask(issues) and not acknowledged: raise AskRequired(issues)   # → 409
        note = insert_note(user_id, draft)
        attach_conversation(conversation_id, note.id)     # status: draft → attached (1:1)
        insert_content_blocks(note.id, draft.blocks)      # authorship·quoted_from·derived
        insert_premises(note.id, draft.premises)          # statement는 사용자 말 그대로
        for g in draft.galae:
            galae = insert_galae(note.id, g)              # question·judge_end (없으면 null)
            insert_scenarios(galae.id, g.scenarios)       # + residual 시나리오 반드시 생성
            if len(answers) >= 2 and g.probabilities:     # 혼자인 답이면 확률 전부 null
                apply_probabilities(galae.id, redistribute(...))
    return note   # 응답에 is_complete(파생)와 남은 Issue(incomplete·notice) 동봉
```

- residual(`그 외 예상 못한 전개`) 생성은 갈래 생성 코드의 책임이다 — DB의
  partial unique index(01-db-schema §4.3)가 "정확히 1개"를 보강한다.
- 저장 후 같은 세션에서 2단계 폼(확률·auto 조건)으로 이어지지만, 그건 §4의 별도
  엔드포인트들이다 — 건너뛰어도 노트는 정상이다(§3.1 2단계).
- 저장된 노트의 `build` 재실행은 없다. 수정은 `PATCH /notes/{id}`의 블록 편집뿐이고
  원본 대화는 어떤 경로로도 변하지 않는다.

## 10. 에러 모델

```json
{ "error": { "code": "SELL_EXCEEDS_HOLDINGS", "message": "2026-03-02 기준 잔고가 12주라 20주 매도를 저장할 수 없습니다.", "details": {...} } }
```

| HTTP | 쓰임 |
|---|---|
| 401 | JWT 없음·만료·서명 불일치 |
| 404 | 리소스 없음 + **남의 리소스** (소유 확인 실패를 구분하지 않는다) |
| 409 | `ASK_REQUIRED`(되묻기 미확인, details에 Issue[]), `SELL_EXCEEDS_HOLDINGS`, 확률 갱신 경합 |
| 422 | 요청 스키마 위반 (FastAPI 기본) |
| 502 | 외부 API(시세·LLM) 실패 — 가격 칸만 `조회 실패`로 두는 건 클라이언트 책임(§3.9) |

`message`는 검증기 Issue와 같은 원칙이다 — UI가 그대로 띄울 수 있는 완성된 문장으로 쓴다.
도메인 예외(`domain/`은 예외 대신 값 반환이 기본이지만, 서비스 계층이 값→예외로 변환)는
`core/errors.py`의 핸들러 하나가 이 모양으로 직렬화한다.

## 11. 테스트 전략

| 층 | 대상 | 방법 |
|---|---|---|
| 도메인 단위 테스트 | `domain/` 전부 — 검증 규칙 각각, 재분배, 이동평균·환차·XIRR·손익 재구성, 매도 수량 검사 | 순수 함수라 픽스처 입력→출력 비교만. DB·목 없음. 커버리지 요구는 이 계층에만 건다 |
| **골든 벡터** | 확률 재분배(§6) + 장부 계산의 대표 시나리오(이동평균·환차손익·XIRR) | `fixtures/*.json`을 pytest와 웹 vitest가 **같이** 읽는다. 벡터 수정은 리뷰 필수 — 정본 수정과 같다 |
| API 통합 테스트 | 저장 플로우(§9)·확률 갱신·판정 전이·장부 CRUD | 로컬 Supabase(`supabase start`) 위에서 httpx로 실제 호출. DB 트리거(합 100·불변 대화·residual 보호)가 진짜로 막는지 여기서 확인한다 |
| 스키마 정합 | SQLAlchemy 모델 ↔ 마이그레이션 | CI에서 `supabase db reset` 후 스키마 덤프와 모델 메타데이터 대조 (01-db-schema §8) |

우선순위도 이 순서다. 도메인 순수 함수가 두텁고, 통합 테스트는 트랜잭션 경계와 DB
방어선이 실제로 작동하는지만 얇게 확인한다.
