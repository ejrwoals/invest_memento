# 인프라·배포·운영

> 상위 문서: [`development-plan.md`](../development-plan.md) §6(시스템 아키텍처)·§13(구현 마스터플랜).
> 이 문서는 환경 구성, 배포 대상, CI/CD, 관측, 백업, 보안, 비용을 다룬다.
> 코드 구조는 [`02-backend.md`](./02-backend.md)·[`03-frontend.md`](./03-frontend.md)·
> [`04-ai-agents.md`](./04-ai-agents.md)·[`05-series-service.md`](./05-series-service.md)가,
> 테이블 구조는 [`01-db-schema.md`](./01-db-schema.md)가 정본이다.
> 가격·무료 티어 정책은 **2026-08 기준**이며 자주 바뀌므로 착수 시 재확인한다.

## 1. 환경 구성

환경은 **local과 production 둘뿐이다.** 별도 스테이징은 두지 않는다.

- **스테이징을 두지 않는 이유**: 개인 프로젝트 규모에서 스테이징은 유지 비용(별도 Supabase
  프로젝트, 별도 백엔드 앱, 시크릿 이중 관리)만 들고, 실사용 트래픽이 없어 production과의
  차이를 검증해주지도 못한다. 그 역할은 세 가지가 나눠 맡는다.
  - 스키마 검증 → 로컬 `supabase db reset` + CI의 마이그레이션 dry-run(§5)
  - 프론트 검증 → Vercel의 PR별 Preview 배포 (기본 제공)
  - 위험한 마이그레이션 리허설 → 필요할 때만 Supabase branching(유료) 또는
    로컬에 production 덤프를 부어 재현
- 사용자가 늘어 마이그레이션 실수가 실제 피해로 이어지는 시점에 스테이징 도입을 재검토한다.

| | local | production |
|---|---|---|
| DB/Auth/Storage | `supabase start` (Docker) | Supabase 관리형 (서울 `ap-northeast-2`) |
| api + worker | `uv run` 2개 프로세스 | Fly.io 2개 프로세스 그룹 (§3) |
| web | `next dev` | Vercel |
| 이메일 | Resend 테스트 키 (샌드박스) | Resend |
| LLM | Claude API (개발용 키) | Claude API (운영용 키) |

**로컬 기동 순서**: `supabase start` → `supabase db reset`(마이그레이션+시드) →
`uv run fastapi dev`(api) / `uv run python -m app.worker`(worker) → `next dev`.

**.env 규약**

- `apps/web/.env.local`, `apps/api/.env` — 둘 다 gitignore. 대신 **`.env.example`을
  각 앱에 커밋**하고, 키 이름과 용도 주석만 담는다(값은 절대 넣지 않는다).
- 로컬 Supabase의 URL·anon key·service role key는 `supabase start` 출력에서 복사한다.
  로컬 키는 공개된 기본값이므로 유출 걱정이 없다.
- 코드가 환경을 분기해야 할 때는 `APP_ENV=local|production` 하나만 본다.
  키 존재 여부로 환경을 추측하는 코드를 만들지 않는다.

## 2. 배포 아키텍처

```
[사용자 브라우저]
      │ HTTPS
      ▼
[Vercel — apps/web (Next.js)]
      │ HTTPS (API 호출, Supabase Auth JWT 첨부)
      ▼
[Fly.io — apps/api (FastAPI)]  ←  [Fly.io — worker (APScheduler)]
      │            같은 Docker 이미지, 프로세스 그룹 2개, NRT(도쿄) 리전
      │ service role
      ▼
[Supabase — Postgres · Auth · Storage]  (서울 리전)
      │
      ▼ (api·worker가 호출)
[외부 API]
   ├─ FRED / ECOS / 한국투자증권  — 수치 배치 수집 (worker, 일 1회)
   ├─ Claude API                 — Thesis Builder·리서치·회고 초안 (api·worker)
   └─ Resend                     — 리마인드 이메일 (worker)
```

- 웹 클라이언트는 Supabase Auth로 로그인하고, **데이터는 FastAPI를 통해서만** 읽고 쓴다
  (01-db-schema §1). PostgREST 직접 접근 경로는 만들지 않는다.
- worker는 APScheduler로 일 1회 수치 수집·평가, 리마인드 발송을 돈다. 온디맨드 비동기
  작업(AI 리서치·회고 초안)은 **Postgres `jobs` 테이블**을 api가 적재하고 worker가
  폴링해 처리한다. Redis는 두지 않는다 — 이 규모에서 폴링 지연(수 초)은 문제가 아니다.

## 3. 백엔드 호스팅 — Fly.io vs Railway

| 기준 | Fly.io | Railway |
|---|---|---|
| 서울 리전 | 없음. 최인접 **NRT(도쿄)** — 서울 Supabase까지 ~30ms | 없음. 최인접 **싱가포르** — 서울까지 ~70ms 이상 |
| 멀티 프로세스 | `fly.toml`의 `[processes]`로 web·worker를 **같은 이미지에서 그룹 분리**, 그룹별 머신·스케일 독립 | 서비스 2개를 만들고 시작 명령만 달리함. 가능하나 서비스 단위 과금 |
| 상시 실행 워커 | 머신 auto-stop을 워커 그룹만 꺼서 상시 실행 지정 가능 | 기본이 상시 실행 |
| Docker 배포 | `fly deploy`가 Dockerfile 빌드→배포. GitHub Actions 연동 단순 | GitHub 연동 자동 배포가 기본. Dockerfile 지원 |
| 가격 | 종량제. shared-cpu-1x 256MB 머신 2대 ≈ **월 $4~7** | Hobby $5/월 + 사용량. 상시 서비스 2개면 **월 $10 안팎** |
| 무료 티어 | 없음 (2024년 폐지) | 체험 크레딧만 |

**권고: Fly.io.** (2026-08 기준, 착수 시 재확인)

근거는 두 가지다. 첫째, **DB와의 거리.** api는 요청마다 Supabase를 여러 번 왕복하므로
도쿄~서울 ~30ms와 싱가포르~서울 ~70ms의 차이가 응답 시간에 곱해져 들어간다. 둘째,
**프로세스 그룹 모델.** 같은 이미지·같은 배포 단위에서 web/worker를 가르는 구조가
"api 웹 프로세스 + APScheduler 워커" 요구와 정확히 맞고, 배포 원자성(둘이 항상 같은
코드 버전)이 공짜로 따라온다. Railway의 강점(무설정 DX)은 CI에서 `fly deploy` 한 줄로
상쇄된다. 플랫폼 크론은 어느 쪽도 쓰지 않는다 — 스케줄은 APScheduler가 코드로 갖는다.

## 4. 시크릿·환경변수

| 변수 | 쓰는 곳 | 저장 위치 |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web | Vercel 환경변수. anon key는 공개 전제(RLS가 방어) |
| `NEXT_PUBLIC_API_BASE_URL` | web | Vercel 환경변수 |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | api·worker | Fly secrets. **service role은 서버 전용, 절대 web에 넣지 않는다** |
| `DATABASE_URL` (SQLAlchemy, Supavisor 풀러 경유) | api·worker | Fly secrets |
| `ANTHROPIC_API_KEY` | api·worker | Fly secrets |
| `FRED_API_KEY` / `ECOS_API_KEY` | worker | Fly secrets |
| `KIS_APP_KEY` / `KIS_APP_SECRET` | api·worker | Fly secrets. 접근 토큰은 만료가 있어 DB나 메모리에 캐시 |
| `RESEND_API_KEY` | worker | Fly secrets |
| `SENTRY_DSN` (web용 / api용 각각) | web·api·worker | Vercel / Fly |
| `SUPABASE_ACCESS_TOKEN` / `SUPABASE_DB_PASSWORD` / `FLY_API_TOKEN` / Vercel 토큰 | CI 전용 | GitHub Actions Secrets |

- 주입 경로는 세 곳뿐이다: **Vercel 환경변수, `fly secrets set`, GitHub Actions Secrets.**
  이 밖의 어디에도(코드, 문서, 시드 파일) 시크릿을 두지 않는다.
- 로컬 `.env`와 production 시크릿은 이름을 동일하게 유지해 코드 분기를 없앤다.
- service role 키가 유출되면 RLS가 무력화되므로(01-db-schema §7) 유출 의심 시
  Supabase 대시보드에서 즉시 로테이션한다. 로테이션 절차를 아는 것도 운영의 일부다.

## 5. CI/CD — GitHub Actions

### PR 검사 (경로 필터로 해당 부분만 실행)

| 잡 | 내용 |
|---|---|
| `web` | `apps/web` — lint(eslint) / typecheck(tsc) / test(vitest) |
| `api` | `apps/api` — ruff(lint+format) / mypy / pytest (uv로 의존성 고정 설치) |
| `golden` | `fixtures/*.json` 골든 벡터를 **pytest와 vitest 양쪽에서 검증** — 확률 재분배·노트 검증 규칙이 Python(서버 정본)과 TS(클라이언트 즉시 피드백)에서 같은 답을 내는지 확인한다 |
| `db` | `supabase db start` 후 마이그레이션 전체 적용(dry-run) + 스키마 덤프를 SQLAlchemy 모델 선언과 대조(01-db-schema §8 — ORM은 스키마를 생성하지 않는다) |

### main 머지 시 배포 — 스키마 먼저, 코드 나중

```
1. supabase db push        (새 마이그레이션을 production DB에 적용)
2. fly deploy              (api + worker — 같은 이미지, 두 그룹 롤링 재시작)
3. Vercel 자동 배포        (main 푸시 감지 — 1·2와 병렬로 돌지만 아래 원칙으로 안전)
```

- **마이그레이션은 항상 코드보다 먼저 적용한다.** 따라서 모든 마이그레이션은 **직전 버전
  코드와 호환**되어야 한다(expand-contract): 컬럼 추가·nullable 완화는 자유롭고,
  컬럼 삭제·rename·제약 강화는 "코드에서 사용 제거 배포 → 다음 릴리스에서 스키마 정리"
  두 단계로 나눈다. 이 원칙을 지키면 web 배포가 병렬로 돌아도 깨지지 않는다.
- 1단계 실패 시 2·3단계를 실행하지 않는다(파이프라인 중단). 마이그레이션 파일은 수정하지
  않고 항상 새 파일로 고친다(01-db-schema §8).
- 롤백은 코드만 이전 릴리스로 되돌린다(`fly deploy` 이전 이미지). 스키마 롤백 마이그레이션은
  만들지 않는다 — expand-contract를 지키면 이전 코드가 새 스키마 위에서 그대로 돈다.

## 6. 관측·운영

- **구조화 로그**: api·worker 모두 JSON 라인 로그(structlog). 모든 요청·잡 로그에
  `request_id`(또는 `job_id`)와 `user_id`를 싣되, **노트 본문·대화 내용은 로그에 싣지
  않는다**(§8 프라이버시). Fly 로그는 휘발성이므로 보존이 필요해지면 Logtail 등 연동을
  그때 검토한다.
- **Sentry**: web(브라우저)·api·worker 세 곳에 무료 티어로 붙인다. 배치 실패 알림은
  **Sentry Cron Monitoring**으로 처리한다 — 일 1회 수치 수집, 리마인드 발송 잡이 체크인을
  보내고, 제시간에 안 오면 Sentry가 이메일로 알린다. 별도 알림 인프라를 만들지 않는다.
- **LLM 토큰 사용량 로깅**: Claude API 호출마다 응답의 `usage`(입출력 토큰)를
  에이전트 종류(thesis_builder / research / review / advisor)·`user_id`·`note_id`와 함께
  DB 테이블(`llm_usage_log`)에 적재한다. §10의 "AI 비용 → 사용자별/노트별 실행 정책"은
  이 데이터가 있어야 설계할 수 있으므로 **첫날부터 기록한다.** 월 합계를 보는 SQL 한 줄이
  초기 대시보드다.
- 수치 배치는 실패 시 다음 배치에서 소급 수집되는 구조(§6 아키텍처)이므로, 알림은
  "하루 놓침"이 아니라 "잡 자체가 안 돌았음"에만 울리면 된다.

## 7. 백업·복구

- Supabase **Pro 플랜의 일일 자동 백업(7일 보관)** 을 기본으로 한다. Free 플랜에는 자동
  백업이 없으므로, 실사용자(본인 외 1명이라도)가 생기는 시점부터는 Pro를 전제한다.
  이 앱의 데이터(원본 대화·근거 항목)는 **유실되면 재생성이 불가능한** 종류다.
- 추가로 **주 1회 `pg_dump` 오프사이트 백업**을 GitHub Actions 스케줄 워크플로로 돈다.
  덤프를 암호화(age)해 private 저장소가 아닌 별도 오브젝트 스토리지(예: Cloudflare R2
  무료 티어)에 두고 최근 8주분만 보관한다. 플랫폼 밖 사본 하나가 계정 잠김·과금 사고
  같은 "Supabase 자체의 장애가 아닌 사고"에 대한 보험이다.
- 복구 리허설: 분기 1회, 최신 덤프를 로컬 `supabase start` 인스턴스에 부어 앱이 뜨는지
  확인한다. 복원해본 적 없는 백업은 백업이 아니다.

## 8. 보안·프라이버시

투자 관점·포지션은 민감 정보다(§10). 원칙은 다음과 같다.

- **RLS 이중 방어**: 접근 제어의 1차선은 FastAPI의 권한 검사지만, 전 테이블에 RLS를 켜서
  api 버그·anon key 경로의 실수가 데이터 노출로 이어지지 않게 한다(01-db-schema §7).
- **service role 키는 Fly secrets에만** 존재한다. 브라우저 번들·로그·CI 아티팩트 어디에도
  나타나지 않는지 배포 전 점검한다.
- **전 구간 HTTPS**: Vercel·Fly·Supabase 모두 기본 제공. HTTP 리스너를 열지 않는다.
- **PII 최소화**: 계정 식별에 필요한 것은 이메일뿐이다. 이름·전화번호·계좌번호를 받지
  않고, 증권사 계좌 연동도 하지 않는다(매매 기록은 수동 입력 — §3.9).
- **LLM 전송 데이터**: 대화 전문·노트 내용이 Claude API로 나간다. Anthropic API는 기본적으로
  입력을 학습에 쓰지 않지만(2026-08 기준, 착수 시 약관 재확인), 이 사실과 전송 범위를
  개인정보 고지에 명시한다. 로그·Sentry 이벤트에는 노트 본문을 싣지 않는다(§6).
- 백업 덤프는 반드시 암호화 후 저장한다(§7).

## 9. 월 비용 추정 (초기 사용자 소수 전제, 2026-08 기준 — 착수 시 재확인)

| 항목 | 플랜 | 월 비용 | 비고 |
|---|---|---|---|
| Supabase | Free → Pro | $0 → **$25** | 실사용자 발생 시점에 Pro 전환(자동 백업 — §7) |
| Vercel | Hobby | $0 | Hobby는 비상업 용도 한정. 유료화 시 Pro $20 |
| Fly.io | 종량제 | **$4~7** | shared-cpu-1x 256MB × 2 (api·worker) |
| Claude API | 종량제 | **$5~30** | 노트 작성 대화 5~8턴 + 온디맨드 리서치·회고. 사용자 수에 비례 — `llm_usage_log`(§6)로 실측 후 상한 정책 결정 |
| Resend | Free | $0 | 월 3,000통 — 리마인드 이메일 규모로 충분 |
| FRED / ECOS / 한국투자증권 | 무료 | $0 | 일 1회 배치·전역 캐시로 쿼터 내 (§6 아키텍처) |
| Cloudflare R2 (백업) | Free | $0 | 10GB까지 무료 |
| **합계** | | **약 $10~35** | Supabase Pro 전환 후 $35~60 |

지배적 변수는 Claude API뿐이고 나머지는 사실상 고정비다. 따라서 비용 관리는 곧
LLM 호출 정책 관리이며, 그 근거 데이터가 `llm_usage_log`다.

## 10. 계측 — 자체 이벤트 테이블로 시작한다

§10·§12가 요구하는 초기 계측값은 셋이다: **2단계 진입률·완료율**, **노트 작성 대화 턴 수**,
그리고 기저율 자료가 실제로 나오는 갈래 비율.

- **외부 분석 도구(GA·PostHog 등)를 붙이지 않고 `product_events` 테이블 하나로 시작한다.**
  - 필요한 이벤트가 열 개 미만이고, 질문이 명확하며(위 셋), 답은 SQL 한 줄이면 나온다.
  - 외부 도구는 스크립트 삽입·쿠키 고지·데이터 반출(민감 정보 — §8)을 데려온다.
    민감한 투자 기록 옆에 서드파티 트래커를 두지 않는 것 자체가 제품 신뢰의 일부다.
- 스키마는 최소로: `id, user_id, name, properties jsonb, created_at`. 이벤트는
  서버(api)에서 기록한다 — 클라이언트 계측은 유실·중복이 많고, 위 세 질문은 전부
  서버가 아는 사건이다.
- 초기 이벤트 목록: `note_saved`(properties: 대화 턴 수, 빈 칸 목록), `step2_entered`,
  `step2_completed`, `step2_skipped`, `research_requested`, `review_opened`,
  `reference_search_done`(properties: 기저율 발견 여부).
- 이벤트가 늘고 퍼널·리텐션 같은 질문이 생기면 그때 PostHog self-host 등을 재검토한다.
  테이블은 그대로 내보내면 되므로 지금 결정이 미래를 막지 않는다.
