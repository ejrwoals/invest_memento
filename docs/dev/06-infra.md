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
  - 스키마 검증 → 마이그레이션 파일 리뷰 + CI의 마이그레이션 dry-run(§5)
  - 프론트 검증 → Vercel의 PR별 Preview 배포 (기본 제공)
  - 위험한 마이그레이션 리허설 → 필요할 때만 Supabase branching(유료) 또는
    로컬에 production 덤프를 부어 재현
- 사용자가 늘어 마이그레이션 실수가 실제 피해로 이어지는 시점에 스테이징 도입을 재검토한다.

| | local | production |
|---|---|---|
| DB/Auth/Storage | **호스티드 Supabase를 그대로 쓴다** (아래) | Supabase 관리형 (서울 `ap-northeast-2`) |
| api + worker | `uv run` 2개 프로세스 | Cloud Run 서비스 2개 — 같은 이미지 (§3) |
| web | `next dev` | Vercel |
| LLM | Claude API (개발용 키) | Claude API (운영용 키) |

이메일 발송은 없다 — 리마인드는 인앱(홈 피드 + `notifications` 행) 전용이다.
(PWA 푸시는 Phase 2에서 검토)

- **개발 DB도 호스티드 Supabase다.** 대시보드(테이블 브라우저·SQL editor·Auth 설정)를
  보면서 개발하는 워크플로가 기본이고, Docker 로컬 스택(`supabase start`)은 오프라인
  개발이 필요할 때의 선택지로만 남긴다. 초기(사용자 = 개발자 본인)에는 프로젝트 하나로
  충분하고, 실사용자가 생기면 그 시점에 dev용 무료 프로젝트를 분리한다 — 그 전까지는
  분리가 시크릿 이중 관리 비용만 만든다.
- 스키마 적용 경로: `supabase link` 1회 → 마이그레이션 파일 작성 → `supabase db push`.
  SQL editor는 실험·조회용이고, 확정된 스키마 변경은 반드시 마이그레이션 파일로
  남긴다(01-db-schema §8).
- Google 로그인 등 OAuth provider는 호스티드 Auth 대시보드에서 설정한다. redirect URL에
  `http://localhost:3000` 계열을 함께 등록하면 로컬 개발에서도 같은 Auth를 쓴다.

**로컬 기동 순서**: `uv run fastapi dev`(api) / `uv run python -m app.worker`(worker)
→ `next dev`. DB는 호스티드이므로 띄울 것이 없다.

**.env 규약**

- `apps/web/.env.local`, `apps/api/.env` — 둘 다 gitignore. 대신 **`.env.example`을
  각 앱에 커밋**하고, 키 이름과 용도 주석만 담는다(값은 절대 넣지 않는다).
- Supabase URL·anon key·service role key는 호스티드 프로젝트 대시보드
  (Settings → API)에서 복사한다. service role key는 서버(.env)에만 두고 웹에는
  anon key만 노출한다.
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
[Cloud Run — api (FastAPI)]  ←  [Cloud Run — worker (APScheduler)]
      │        같은 Docker 이미지(Artifact Registry), 서비스 2개, asia-northeast3(서울)
      │ service role
      ▼
[Supabase — Postgres · Auth · Storage]  (서울 리전)
      │
      ▼ (api·worker가 호출)
[외부 API]
   ├─ FRED / ECOS / 한국투자증권  — 수치 배치 수집 (worker, 일 1회)
   └─ Claude API                 — Thesis Builder·리서치·회고 초안 (api·worker)
```

- 웹 클라이언트는 Supabase Auth로 로그인하고, **데이터는 FastAPI를 통해서만** 읽고 쓴다
  (01-db-schema §1). PostgREST 직접 접근 경로는 만들지 않는다.
- worker는 APScheduler로 일 1회 수치 수집·평가, 리마인드 다이제스트 적재(인앱)를 돈다. 온디맨드 비동기
  작업(AI 리서치·회고 초안)은 **Postgres `jobs` 테이블**을 api가 적재하고 worker가
  폴링해 처리한다. Redis는 두지 않는다 — 이 규모에서 폴링 지연(수 초)은 문제가 아니다.

## 3. 백엔드 호스팅 — Google Cloud Run (확정)

Docker 이미지를 Cloud Run에 올려 배포한다(사용자 결정). 리전은 **asia-northeast3(서울)**
— Supabase 서울과 같은 리전이라 DB 왕복 지연이 최소다. 이미지는 Artifact Registry
(같은 리전)에 둔다.

**같은 이미지에서 서비스 2개를 만든다** — 배포 원자성(둘이 항상 같은 코드 버전)을 위해
이미지는 하나이고, Cloud Run의 command 오버라이드로 역할을 가른다.

| 서비스 | 시작 명령 | 스케일 설정 | 역할 |
|---|---|---|---|
| `api` | uvicorn (Dockerfile 기본 CMD) | min 0 — 요청 없으면 0으로 (콜드스타트 수 초는 이 앱 성격상 허용) | FastAPI |
| `worker` | `python -m app.worker` | **min-instances=1 + CPU always-allocated** | APScheduler 크론 + jobs 폴링 |

- **worker에 min=1이 필수인 이유**: Cloud Run은 기본이 요청 기반 스케일-투-제로라,
  인스턴스가 0이 되면 APScheduler도 jobs 폴링도 함께 죽는다. 상시 1대 + CPU 상시 할당
  (요청 처리 중이 아닐 때도 CPU를 주는 설정)이어야 백그라운드 루프가 돈다.
  worker도 `/health` 리스너는 열어 둔다(Cloud Run 서비스는 포트 리슨이 필요하다).
  Cloud Run **Worker Pools**(pull 기반 워크로드용, HTTP 불필요)가 GA면 그쪽이 더
  정확한 모델이므로 착수 시 확인해 대체한다.
- **비용 절감 대안(도입 유보)**: Cloud Scheduler → Cloud Run Jobs로 배치를 돌리고
  온디맨드 작업은 Cloud Tasks로 밀어넣으면 상시 인스턴스가 사라진다. 다만 배치
  진입점을 잡별 엔드포인트로 재구성해야 하고 jobs 폴링 설계(02-backend)와 어긋나므로,
  worker 상시 비용이 실제로 부담될 때 재검토한다. 스케줄은 어느 쪽이든 코드가 갖는다 —
  플랫폼 크론에 비즈니스 로직을 심지 않는다.
- Dockerfile은 `apps/api/Dockerfile` 하나다. uv 공식 베이스 이미지로 `uv sync --frozen`
  후 uvicorn을 `$PORT`(Cloud Run 주입, 기본 8080)에 바인딩한다.

## 4. 시크릿·환경변수

| 변수 | 쓰는 곳 | 저장 위치 |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | web | Vercel 환경변수. anon key는 공개 전제(RLS가 방어) |
| `NEXT_PUBLIC_API_BASE_URL` | web | Vercel 환경변수 |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | api·worker | GCP Secret Manager → Cloud Run 환경변수 매핑. **service role은 서버 전용, 절대 web에 넣지 않는다** |
| `DATABASE_URL` (SQLAlchemy, Supavisor 풀러 경유) | api·worker | GCP Secret Manager |
| `ANTHROPIC_API_KEY` | api·worker | GCP Secret Manager |
| `FRED_API_KEY` / `ECOS_API_KEY` | worker | GCP Secret Manager |
| `KIS_APP_KEY` / `KIS_APP_SECRET` | api·worker | GCP Secret Manager. 접근 토큰은 만료가 있어 DB에 캐시(01-db-schema §3.12 `kis_tokens`) |
| `SENTRY_DSN` (web용 / api용 각각) | web·api·worker | Vercel / Secret Manager |
| `SUPABASE_ACCESS_TOKEN` / `SUPABASE_DB_PASSWORD` | CI 전용 (db push) | GitHub Actions Secrets |

- 주입 경로는 세 곳뿐이다: **Vercel 환경변수, GCP Secret Manager(Cloud Run 매핑),
  GitHub Actions Secrets.** 이 밖의 어디에도(코드, 문서, 시드 파일) 시크릿을 두지 않는다.
- CI의 GCP 인증은 **Workload Identity Federation**으로 한다 — 서비스 계정 JSON 키를
  만들지 않는다(키 파일은 유출 사고의 단골이다).
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
1. supabase db push          (새 마이그레이션을 production DB에 적용)
2. docker build → Artifact Registry push
   → gcloud run deploy api / worker   (같은 이미지 digest로 두 서비스 갱신)
3. Vercel 자동 배포          (main 푸시 감지 — 1·2와 병렬로 돌지만 아래 원칙으로 안전)
```

- **마이그레이션은 항상 코드보다 먼저 적용한다.** 따라서 모든 마이그레이션은 **직전 버전
  코드와 호환**되어야 한다(expand-contract): 컬럼 추가·nullable 완화는 자유롭고,
  컬럼 삭제·rename·제약 강화는 "코드에서 사용 제거 배포 → 다음 릴리스에서 스키마 정리"
  두 단계로 나눈다. 이 원칙을 지키면 web 배포가 병렬로 돌아도 깨지지 않는다.
- 1단계 실패 시 2·3단계를 실행하지 않는다(파이프라인 중단). 마이그레이션 파일은 수정하지
  않고 항상 새 파일로 고친다(01-db-schema §8).
- 롤백은 코드만 이전 리비전으로 되돌린다 — Cloud Run은 리비전이 남으므로
  `gcloud run services update-traffic --to-revisions=<이전>=100` 한 줄이다. 스키마 롤백
  마이그레이션은 만들지 않는다 — expand-contract를 지키면 이전 코드가 새 스키마 위에서
  그대로 돈다.

## 6. 관측·운영

- **구조화 로그**: api·worker 모두 JSON 라인 로그(structlog). 모든 요청·잡 로그에
  `request_id`(또는 `job_id`)와 `user_id`를 싣되, **노트 본문·대화 내용은 로그에 싣지
  않는다**(§8 프라이버시). stdout JSON은 Cloud Logging이 자동 수집한다(기본 30일 보존
  — 무료 한도 내).
- **Sentry**: web(브라우저)·api·worker 세 곳에 무료 티어로 붙인다. 배치 실패 알림은
  **Sentry Cron Monitoring**으로 처리한다 — 일 1회 수치 수집, 리마인드 다이제스트 잡이 체크인을
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
- **service role 키는 GCP Secret Manager에만** 존재한다. 브라우저 번들·로그·CI 아티팩트
  어디에도 나타나지 않는지 배포 전 점검한다.
- **전 구간 HTTPS**: Vercel·Cloud Run·Supabase 모두 기본 제공. HTTP 리스너를 열지 않는다.
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
| Cloud Run | 종량제 | **$5~25** | api는 스케일-투-제로라 무료 한도 내(≈$0). 비용의 대부분은 worker 상시 1대(min-instances=1, CPU always-allocated, 0.5vCPU/512MB 추정) — 착수 시 가격 계산기로 재확인. 부담되면 Scheduler+Jobs 대안(§3) |
| Claude API | 종량제 | **$5~30** | 노트 작성 대화 5~8턴 + 온디맨드 리서치·회고. 사용자 수에 비례 — `llm_usage_log`(§6)로 실측 후 상한 정책 결정 |
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
