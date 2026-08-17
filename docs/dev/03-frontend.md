# 프론트엔드 — Next.js (apps/web)

> 상위 문서: [`development-plan.md`](../development-plan.md) §13(구현 마스터플랜).
> 화면·인터랙션의 정본은 [`ux-design.md`](../ux-design.md)이고, 비주얼의 정본은
> [`prototype-mvp.html`](../prototype-mvp.html)이다. 이 문서는 그 둘을 Next.js 구현으로
> 옮기는 방법 — 라우트, 컴포넌트, 토큰 이관, 차트, 상태 관리 — 만 다룬다.
> 데이터 모델은 [`01-db-schema.md`](./01-db-schema.md), API 계약은 `02-backend.md` 참조.

## 1. 전제

- **Next.js App Router + TypeScript**, 모노레포 `apps/web`.
- 클라이언트는 **FastAPI(apps/api)만 호출**한다. Supabase를 직접 읽지 않는다.
  예외는 로그인 하나 — Supabase Auth SDK(`@supabase/ssr`)로 세션을 만들고,
  이후 모든 데이터 요청은 그 토큰을 실어 FastAPI로 간다.
- **검증 규칙의 정본은 서버다**(dev-plan §3.1 — 규칙은 결정론적 코드, 서버가 재검사).
  미리보기 화면의 즉시 피드백은 클라이언트 규칙 복제가 아니라 **validate API 호출
  (디바운스)** 로 받는다. 유일한 TS 미러는 확률 재분배 함수 하나이며(§6), 서버 구현과
  어긋나지 않도록 골든 테스트 벡터를 공유한다.
- 차트는 **라이브러리 없이 커스텀 SVG**(§5). ux-design §7의 스펙(기간 고정, 눈금 없음,
  띠·빗금·HTML 오버레이 라벨)은 일반 차트 라이브러리의 전제와 정면으로 충돌하므로,
  프로토타입의 인라인 SVG 접근을 그대로 컴포넌트화한다.
- 데이터 페칭은 **TanStack Query**. 전역 상태 스토어는 두지 않는다 — 서버 상태는
  Query 캐시가, 나머지(서랍 열림, 대화 입력 등)는 컴포넌트 로컬 상태가 감당한다.

## 2. 라우트 맵과 디렉토리

### 2.1 화면 → 경로

전역 내비게이션은 `홈 / 노트 / + 새 노트` 셋으로 고정한다(ux §2).

| 화면 (ux-design) | 경로 | 비고 |
|---|---|---|
| 홈 — 피드 + 타임라인 (§3.1) | `/` | 기본 진입점 |
| 노트 목록 (§3.6) | `/notes` | |
| 보유 종목 (§3.7) | `/notes/holdings` | 노트 탭 안의 전환 뷰. 별도 전역 탭 아님 |
| 노트 상세 (§3.4) | `/notes/[noteId]` | 서랍·접힘 섹션은 경로 없이 로컬 상태 |
| 새 노트 · 1단계 대화 (§3.2) | `/write/[conversationId]` | 진입 시 conversation을 먼저 만들고 이동 — draft 재개와 경로가 같아진다 |
| 노트 초안 확인 (§3.2) | `/write/[conversationId]/preview` | 저장 전이므로 conversation 축 |
| 2단계 확인 방법 설정 (§3.3) | `/notes/[noteId]/setup` | 저장 직후 리다이렉트로 이어짐. 노트 상세에서 재진입 가능 |
| 리마인드 상세 (§3.5) | `/reminders/[notificationId]` | 열람 시각 기록(감쇠 판단, 스키마 §3.8)이 이 진입점에서 발생 |
| 결과 확인 (§4) | `/notes/[noteId]/galae/[galaeId]/judge` | |
| 회고록 (§5) | `/notes/[noteId]/galae/[galaeId]/review` | 회고는 갈래 단위로 열린다(dev-plan §3.4) |
| 온보딩 (§12) | `/onboarding` | 로그인 → 컨셉 3문장 → 면책 동의 → 즉시 `/write/...` |
| 설정 | `/settings` | 우상단 아바타 메뉴로만 진입. 탭 아님 |
| 로그인 | `/login` | 유일한 Supabase SDK 직접 사용 지점 |

### 2.2 디렉토리 레이아웃

```
apps/web/
  app/
    (auth)/login/  (auth)/onboarding/       # 전역 내비 없는 레이아웃
    (app)/                                  # 인증 + 전역 내비 + 면책 푸터 레이아웃
      page.tsx                              # 홈
      notes/  notes/holdings/  notes/[noteId]/(setup|galae/[galaeId]/(judge|review))/
      write/[conversationId]/(preview)/
      reminders/[notificationId]/
      settings/
    globals.css                             # 디자인 토큰 (§3)
  components/
    ui/          # AppBar, Tabs, Modal/BottomSheet, RightSheet, EmptyState, Disclaimer …
    charts/      # TrendChart, Spark, Donut, PnlChart + scale.ts (공유 좌표 계산)
    note/ home/ write/ review/ holdings/    # 화면 단위 조립 컴포넌트
  lib/
    api/         # FastAPI 클라이언트 (fetch 래퍼 + 타입)
    queries/     # TanStack Query 키·훅 정의 (§7)
    probability/ # redistribute.ts — 서버 미러 (§6)
    terms.ts     # 내부 식별자 → 화면 표기 대응표 (§9)
    supabase.ts  # @supabase/ssr 클라이언트 (로그인 전용)
  fixtures/
    probability.json                       # 골든 테스트 벡터 — 서버와 동일 파일
```

- 서버 컴포넌트는 셸(레이아웃·정적 안내문)까지만 쓴다. 데이터가 붙는 부분은 전부
  클라이언트 컴포넌트 + TanStack Query다 — 조회 시점 표기·낙관적 갱신·디바운스 검증이
  모두 클라이언트 상호작용이라 서버 컴포넌트로 얻을 것이 적다.

## 3. 디자인 토큰과 테마

### 3.1 이관 전략 — 토큰은 다시 쓰지 않고 옮긴다

프로토타입 `:root`의 CSS 커스텀 프로퍼티 체계를 **그대로 `globals.css`로 이관**한다.
Tailwind 등으로 재작성하지 않는다. 색(`--paper-*` `--ink-*` `--line-*` 강조 5쌍),
타이포(`--text-*` `--lh-*`), 간격(`--s1~s8`), 라운드(`--r*`), 모션(`--dur-*`
`--ease-calm`), 그림자까지 이름과 값을 바꾸지 않는다 — 프로토타입이 곧 비주얼 정본이므로
이름이 같아야 대조가 된다. `color-mix()` 기반 도넛 조각색(`--w1~w3`)도 함께 온다.

### 3.2 3중 테마 정의

프로토타입과 같은 3중 구조를 유지한다. 순서가 규칙이다:

1. `:root` — 라이트 팔레트 전체 정의 (기본)
2. `@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]) }` — 시스템 다크
3. `:root[data-theme="dark"]` — 사용자 명시 다크 (시스템 설정을 이긴다)

사용자 선택(`system/light/dark`)은 설정 화면에서 받아 `<html data-theme>`에 스탬프하고
localStorage에 저장한다. FOUC 방지를 위해 root layout의 인라인 스크립트로 페인트 전에
적용한다.

### 3.3 색 의미 규칙 — 코드 리뷰에서 걸러야 할 것

- **`--rise`는 가설 강화, `--fall`은 가설 약화다. 가격 상승/하락이 아니다**(ux §7).
  `금리 3.5% 이하` 조건에서는 내려가는 것이 강화다. 따라서 추이선에 rise/fall을 칠하는
  코드는 전부 버그다 — 선은 무채색, 색은 점 하나뿐(§5).
- 예외적으로 보유 종목 화면의 수익률 **숫자**에만 rise/fall을 쓴다(ux §3.7). 행 전체
  배경을 물들이지 않는다.
- `--countdown`은 임박, `--ai`는 AI 산출물 표시(점, 새 정보 카드 테두리) 전용이다.
- 한 화면의 강조는 하나 — 도넛·큰 숫자·오늘 점이 강조 예산을 나눠 쓰지 않는지 확인한다.

## 4. 컴포넌트 인벤토리

프로토타입의 클래스 구조에서 도출한 재사용 컴포넌트 목록. 괄호는 프로토타입의 대응 클래스.

| 컴포넌트 | 프로토타입 | 등장 화면 |
|---|---|---|
| `AppBar` (`.appbar`) | 뒤로가기 + 제목 + 부제 | 전 화면 |
| `Tabs` (`.htabs`) | 국내/미국/환전 등 | 보유 종목 |
| `BookmarkDrawer` (`.bookmark` + `.drawer` + `.split`) | 책갈피 + 새 정보 서랍. 개수만 표시, 닫기 버튼 없음, 열려도 본문 스크롤 유지 | 노트 상세 |
| `RightSheet` (`.chart-scrim` + `.chart-sheet`) | 오른쪽에서 열리는 시트 | 손익 추이 차트 |
| `Modal` / `BottomSheet` (`.src-modal*`) | 넓은 화면 가운데 카드 ↔ 좁은 화면 하단 시트를 컨테이너 쿼리로 전환하는 한 컴포넌트 | 출처 상세, 매매 기록 입력 |
| `Timeline` (`.tline` `.titem` `.tperiod` `.tnow`) | 세로축 + 점/구간 막대 + 침전(`--far`) + 구간 라벨 | 홈 |
| `FeedCard` (`.fcard`) | 아이콘 + 이유 한 줄 + 제목 + 액션. X 버튼 없음 | 홈 |
| `GalaeBoard` (`.galae`) | 질문 + 판단 시점 + 도넛 + 시나리오 카드 묶음 | 노트 상세 |
| `Donut` + `Legend` (`.pie` `.legend` `.wedge`) | conic-gradient 도넛, 경계 그라데이션 = 확률 범위, 번호 병기 | 노트 상세, 2단계, 리마인드 |
| `ScenarioCard` (`.branch`) + `HowBadge` (`.how--auto/manual/comp`) | 큰 확률 숫자 + 변화 문장 + 판정 방법 배지 | 노트 상세 |
| `IndicatorRow` (`.ind`) + `Spark` (`.spark`) | 조건/수치 한 줄 + 축약 추이 | 노트 상세 |
| `TrendChart` (`.ichart` + `.tip` + `.ilab`) | 전체형 추이 차트 (§5) | 결과 확인, 회고 |
| `SourcePile` (`.pile`) | 답별 자료 더미 + 기저율 묶음 + `찾은 자료가 없습니다` | 2단계, 확률 조정 |
| `ProbabilitySplitter` (`.split-row` + `.slider` + `.scale`) | 재분배 입력 (§6) | 2단계, 다시 판단하기 |
| `SlotList` (`.slot` `.derived` `.empty`) | 칸 단위 초안 + AI 해석값 `고치기` + 빈 칸 표시 | 노트 초안 확인 |
| `PremiseList` (`.premise`) | `이 판단이 성립하려면` 번호 목록 | 노트 상세, 초안, 회고 |
| `Quote` (`.quote`) + `UserTag` (`.tag-user`) | 사용자 원문 인용 + `[사용자]` 표기 | 노트 상세, 리마인드, 회고 |
| `Chat` (`.chat` `.msg` `.composer`) + `BuildPanel` (`.build` `.wsplit`) | 대화 + 만들어지는 노트 패널(넓은 화면) / 접히는 상단 바(좁은 화면) | 새 노트 대화 |
| `InfoCard` (`.info` + `.rel`) | 새 정보 항목 + 뒷받침/어긋남 태그 | 서랍, 리마인드 |
| `WatchList` (`.watchlist`) | `다음에 살펴볼 것` | 노트 상세 |
| `Pick` (`.pick`) | 결과 선택지·2×2 논리 선택지 버튼 | 결과 확인, 회고 |
| `Matrix` (`.matrix`) | 결과×논리 2×2 표 (`결과만 맞았다` 강조) | 회고 |
| `HoldingsTable` (`.hold` `.fx`) + `TradeList` (`.tr`) | 종목 행(고정 폭 열) + 시간순 장부 | 보유 종목 |
| `EmptyState` (`.empty` + ux §3.1 빈 상태 문구) | 성취 문구 없는 빈 상태 | 홈, 서랍, 목록 |
| `Disclaimer` (`.disclaimer`) | 면책 고지 한 줄 | 노트 상세·리마인드 고정 푸터, AI 의견 블록 |
| `Hint` (`.hint`) | 점선 테두리 안내 (관측 규칙 등) | 2단계, 노트 상세 |

`카운트다운 점`(`.kdot`), `단계 표시`(`.step`) 등 잔 요소는 `ui/`의 소형 프리미티브로 둔다.

## 5. 차트 — 커스텀 SVG

### 5.1 왜 라이브러리를 쓰지 않는가

ux §7의 요구는 일반 차트 라이브러리의 기본값과 반대다: 기간 변경·줌 금지, 눈금선 금지,
축 라벨 최소, 비균등 스케일 + **라벨은 SVG 밖 HTML 오버레이**, 고가~저가 띠를 종가선
뒤에 상시, 오늘 이후를 빗금 `<pattern>`으로. 라이브러리를 이 스펙에 맞게 굽는 비용이
직접 그리는 비용보다 크다. 프로토타입이 이미 이 방식으로 그려져 있으므로 계승한다.

### 5.2 TrendChart — 한 렌더러, 두 밀도

축약형(`Spark`)과 전체형(`TrendChart`)은 **같은 좌표 계산기(`charts/scale.ts`)를 쓰는
두 밀도**다. 도메인은 언제나 `기록 시점 → 판단 시점`으로 고정이고, 시리즈는
`series_snapshots`의 종가 + 고가·저가다(마지막 점은 항상 확정 종가 — 미마감 당일 없음).

| 레이어 | 전체형 | 축약형 |
|---|---|---|
| 기록선 (점선) · 목표선 (실선) · 그 사이 띠 | ○ | ○ |
| 고가~저가 띠 (장중 터치 판정 설명용 — 필수) | ○ | ○ |
| 추이선 (2px 무채색) · 오늘 점 | ○ | ○ (달성=채운 점, 미달성=빈 점) |
| 오늘 이후 빗금 (`<pattern>`) | ○ | ○ |
| 목표 최초 터치 점 (유일한 색) + 종가 연결 세로선 + 사유 한 줄 | ○ | — |
| 크로스헤어 툴팁 (`.tip`) + `값으로 보기` 표 (`<details>`) | ○ | — |
| 직접 라벨 (목표·기록·오늘·달성 — HTML 오버레이 `.ilab`) | ○ | — |

- 지켜보는 수치(`watch`)는 목표선·띠 없이 추이만 그린다 — 같은 렌더러에 목표 레이어를
  끄는 형태다. 거시 계열은 고저 띠가 없다(값이 하루 하나).
- SVG는 `preserveAspectRatio="none"`으로 늘어나므로 텍스트를 SVG 안에 넣지 않는다.
  라벨 좌표는 scale.ts가 %로 내주고 HTML absolute 오버레이가 받는다.

### 5.3 도넛과 손익 라인차트

- **도넛은 SVG가 아니라 프로토타입의 `conic-gradient` 방식을 유지한다.** 조각 경계를
  그라데이션 구간으로 그리는데, 그 흐린 폭이 곧 확률의 범위다(ux §6). 확률값 배열
  → gradient stop 문자열 계산 함수 하나로 충분하다. 범례에 번호(`01`)를 병기해
  색맹 대응한다.
- **손익 라인차트**(보유 종목 RightSheet)는 `pnl_snapshots`를 그린다. 추이선 무채색,
  0선 점선, 기간 선택 없음(기록 시작일부터 전부), 벤치마크·색 강조 없음(ux §3.7).
- 회고의 `같은 기간 비교`는 차트가 아니라 가로 막대 3줄이다 — 값 라벨을 붙인 단순
  `<div>` 바로 충분하며 SVG를 쓰지 않는다.

## 6. 확률 재분배 — 유일한 클라이언트 미러

쓰기 경로는 갈래 단위 원자적 갱신 API 하나뿐이고 정본은 서버다(dev-plan §3.1).
그러나 슬라이더는 **드래그 중 매 프레임** 나머지 값이 따라 움직여야 하므로 왕복으로는
만들 수 없다. 그래서 이 함수 하나만 TS로 미러한다.

```ts
// lib/probability/redistribute.ts — 서버 순수 함수의 미러
redistribute(
  scenarios: { id: string; value: number | null; locked: boolean }[],
  changedId: string,
  newValue: number,
): { id: string; value: number }[]
// 불변식: 합 100 · 전부 5의 배수 · residual ≥ 5
// 단계: 5스냅 → 상한 절단 → 기존 비율 배분 → 최대 잔여법 보정 → residual 5 확보
```

- **골든 테스트 벡터 `fixtures/probability.json`을 서버와 같은 파일로 공유**하고,
  CI가 양쪽 구현을 같은 벡터로 돌린다. dev-plan §3.1의 검증 케이스(A→80, A→95,
  A→100 절단, A→0 비율 유지, A→63 스냅, 잠금)가 초기 벡터다. 미러가 어긋나면
  드래그 중 보이던 숫자와 저장된 숫자가 달라진다 — 이 앱에서 가장 나쁜 종류의 버그다.
- 저장 시에는 미러의 결과가 아니라 **`changed + lockedIds`만 서버로 보내고**, 서버가
  재분배한 응답으로 캐시를 덮는다. 미러는 미리보기이지 제출값이 아니다.
- 시나리오가 하나뿐이면 확률 UI 자체를 비활성이 아니라 **없앤다** — `확률은 반대
  시나리오가 생긴 뒤에 나눕니다`(ux §3.4).
- 입력 장치: 데스크톱은 `<input type="range" step="5">` 슬라이더, 좁은 화면은
  `[−] 65% [＋]` 스테퍼로 교체한다(ux §11.4 — 5% 한 칸 17px는 터치 불가, 가로 드래그는
  세로 스크롤과 충돌). 같은 `ProbabilitySplitter`가 컨테이너 폭으로 갈라 그린다.
- 변경 시 이유 한 줄 입력을 권한다(선택). `probability_entries.reason`으로 간다.

## 7. 데이터 페칭과 상태

### 7.1 Query 키 설계

키는 리소스 계층을 그대로 딴다. 무효화가 계층 prefix 매칭이 되도록 한다.

```ts
['home']                                  // 피드 + 타임라인 + draft 재개 링크
['notes']  ['notes', noteId]              // 목록 / 상세 (갈래·시나리오·premise 포함)
['notes', noteId, 'research']             // 새 정보 서랍
['notes', noteId, 'series', code]         // 추이 차트 데이터
['conversations', id]                     // 대화 + draft_note
['reminders', notificationId]
['holdings']  ['holdings', 'pnl', scope]  // 잔고 파생 + 손익 추이
['validate', draftHash]                   // 미리보기 검증 결과 (§7.3)
```

- 뮤테이션 후 무효화 원칙: 확률 갱신 → `['notes', noteId]` + `['home']`.
  판정·시점 재설정 → 같은 조합. 매매 기록 → `['holdings']` 전체.
- 현재가·조회 시점(ux §3.7)은 `staleTime: 0`으로 화면 진입마다 조회하되, 응답의
  조회 시각을 그대로 표기한다. 실패해도 목록 쿼리와 분리되어 있어 목록은 산다.

### 7.2 대화 화면 — 스트리밍과 낙관적 갱신

- AI 응답은 FastAPI의 **SSE 스트리밍**으로 받는다. 스트림은 Query 캐시가 아니라 대화
  컴포넌트의 로컬 상태로 흘리고, 턴이 끝나면 `['conversations', id]`를 무효화해 정본과
  동기화한다.
- 사용자 메시지는 **낙관적으로 즉시 append**한다. 전송 실패 시 메시지를 지우지 않고
  `다시 보내기`를 붙인다 — 사용자가 쓴 문장을 앱이 버리면 안 된다.
- 턴 종료 응답에는 `draft_note`(만들어지는 노트의 진행 상태, 스키마 §3.3)가 실려 온다.
  BuildPanel(데스크톱 오른쪽 패널 / 모바일 접히는 상단 바)은 이것만 그린다 —
  클라이언트가 대화에서 구조를 추출하지 않는다.
- **draft 재개**: 홈 쿼리가 `status='draft'`인 conversation을 내려주면 상단에 조용히
  재개 링크를 놓는다(ux §3.2). 경로가 `/write/[conversationId]`라 재개도 새 대화도
  같은 화면이다.

### 7.3 미리보기 검증 — 서버에 물어본다

미리보기 화면의 `⚠ 근거 항목 1개 비었음` 류 즉시 피드백은 **validate API를 디바운스
(약 500ms) 호출**해 받는다. `Issue[]`(code·severity·field·message — dev-plan §3.1)를
그대로 렌더링한다. `message`는 서버가 완성 문장으로 주므로 클라이언트는 문구를 만들지
않는다. 저장 시 서버가 어차피 재검사하므로, 디바운스 사이의 낡은 표시가 저장을 오염시킬
경로는 없다. `ask` 되묻기는 저장 요청의 응답으로 한 화면에 한 번만 뜬다 — 클라이언트가
따로 세지 않는다.

## 8. 반응형

### 8.1 구간과 도구

ux §11.5의 3구간을 따르되, MVP는 Wide·Narrow만 다듬고 Medium은 Narrow 규칙으로
근사한다(ux §13 이연).

| 구간 | 폭 | 처리 |
|---|---|---|
| Wide | ≥ 1280px | 2단 구성 — 리마인드 ①② 나란히(7:5), 작성 화면 BuildPanel, 서랍 340px |
| Medium | 768–1279px | MVP에서는 Narrow 규칙 적용 (Phase 2에서 정리) |
| Narrow | < 768px | 세로 타임라인 여백 축소, 스테퍼 입력, 하단 고정 입력창, 전체 폭 시트 |

- **컨테이너 쿼리 기반**이다. 프로토타입의 `@container (max-width: 767px)` 블록이
  변환 규칙의 정본이므로, 레이아웃 루트에 `container-type: inline-size`를 주고 그대로
  가져온다. 미디어 쿼리가 아니라 컨테이너 쿼리를 쓰는 이유: 프로토타입과 규칙을 1:1로
  이관할 수 있고, 화면 일부(서랍이 열린 본문 등)가 좁아졌을 때도 같은 규칙이 작동한다.
- 컴포넌트는 구간별 분기 렌더링이 아니라 **같은 DOM에 CSS 변환**을 기본으로 한다.
  DOM 자체가 달라야 하는 곳(슬라이더↔스테퍼, 타임라인 날짜 위치)만 `.desk-only` /
  `.mob-only` 패턴 또는 컨테이너 쿼리 감지 훅으로 가른다.

### 8.2 금지 패턴 — 리뷰 체크리스트 (ux §11.6)

| 금지 | 어긋나기 쉬운 곳 |
|---|---|
| 분리된 두 영역의 세로 스택 변환 | 노트 상세 본문+서랍, 리마인드 ①② — 서랍은 좁은 화면에서 전체 폭 시트, 리마인드는 스크롤 순차 |
| 우선순위 목록의 다열 그리드화 | 홈 피드 — 넓어지면 카드를 눕혀 높이를 줄인다(§11.2) |
| 타임라인의 축 없는 목록화 | 축·점·연결선은 어느 폭에서도 유지, 여백만 변경 |
| 새 정보의 하단 접힘 밀어내기 | 책갈피는 좁은 화면에서도 제자리 |
| 데스크톱 세로 스크롤 전제 배치 | 세로 예산 750px — 가로 폭으로 높이를 줄인다 |
| 모바일에서 정보 삭제 | 배치 변경으로만 해결 |

- 터치 타깃 최소 44×44px(`.btn` 기본 `min-height:44px` 유지). 주요 액션은 화면 아래
  1/3, hover 전용 정보 금지(툴팁은 탭 시트로 대체), 가로 드래그 제스처 전면 회피.

## 9. UX 원칙이 프론트에 거는 제약

| 원칙 | 구현 제약 |
|---|---|
| P1 지금 볼 것 먼저 | `/`는 피드가 위. 노트 목록 정렬 기본값은 다음 판단 시점 순(최근 수정순 아님). 모바일 홈은 피드 기본 + 타임라인 한 줄 요약 상시 |
| P2 원본 대화 불변 | 대화 열람 화면에 수정·삭제 UI를 만들지 않는다. 리마인드 ①은 재요약 없이 저장된 블록 그대로 렌더링 |
| P3 `[사용자]`만 표기 | `authorship==='user'`인 블록에만 `UserTag`. AI 블록은 무표기. AI의 주관 블록만 `AI의 견해` 제목 |
| P4 사고와 정보의 분리 | 새 정보는 `BookmarkDrawer` 안에만. 기본 닫힘. 나란히 대비는 리마인드 화면 하나뿐 |
| P5 죄책감 금지 | FeedCard에 닫기(X) 없음. 미룬 횟수·스트릭·빨간 배지·`N일째` 문구 없음. 책갈피는 개수만(0이면 숫자 없음). 미설정 노트 개수 카운트 금지. 빈 상태에 성취 문구 금지 |
| P6 진입장벽 최소화 | 작성은 대화만. 폼은 2단계 하나이고 `나중에 하기`가 1급 버튼. blocking 차단은 대상·가설 둘뿐 — 나머지는 저장된다 |
| P7 단순한 내비게이션 | 전역 탭 3개 고정. 폴더·태그·필터 UI 없음. 설정은 아바타 메뉴 |
| P8 내부 용어 비노출 | 아래 `terms.ts` 참조 |

**`lib/terms.ts`** — ux §9.4의 대응표를 상수 모듈로 옮긴다. 내부 식별자(`active`,
`pending_judgment`, `supports`, `resolution.manual`, `residual_scenario`…)가 화면
문자열로 나가는 유일한 통로다. 컴포넌트가 식별자를 직접 한국어로 번역하는 것을 금지하고,
`relevance_score`·`series_snapshot`처럼 **표시하지 않는 값은 이 모듈에 키 자체를 두지
않아** 실수로도 새지 않게 한다. 문구 작성 규칙(§9.3 — 할 일로 말하기, D-day 표기 금지,
설계 근거 비노출)은 이 모듈과 화면 문안 리뷰에서 함께 지킨다.

## 10. 구현 순서

ux §11.7의 순서를 그대로 따른다 — **작성은 데스크톱, 확인은 모바일**이라는 실사용
패턴 전제.

1. **데스크톱 Wide 전체** — 로그인·온보딩 → 홈 → 작성 1단계(대화+BuildPanel) →
   초안 확인 → 2단계 → 노트 상세(서랍·도넛·Spark) → 리마인드 → 결과 확인 → 회고 →
   노트 목록·보유 종목
2. **모바일 리마인드 확인 흐름** — 홈(피드 세그먼트) → 리마인드 상세(순차 스크롤) →
   다시 판단하기(스테퍼)
3. **모바일 전체** — 작성 대화(접히는 상단 바, 하단 고정 입력창) 포함
4. **Medium 구간 정리** (Phase 2)

토큰·`ui/` 프리미티브·`charts/scale.ts`·`terms.ts`·`redistribute.ts`는 1단계 첫 화면
전에 깔린다 — 이후 화면이 전부 이 위에 선다.

## 11. 계측 — 최소 이벤트

dev-plan §10이 "이 기능에서 가장 큰 위험"으로 지목한 2단계 이탈률을 잴 수 있어야 한다.
MVP는 자체 이벤트 테이블(FastAPI 수집)로 최소만 심는다.

| 이벤트 | 목적 |
|---|---|
| `setup_entered` / `setup_saved` / `setup_skipped` | **2단계 진입률·완료율** — 낮으면 1단계 말미 절충안 검토(ux §14) |
| `note_saved` (turn 수 포함) | 대화 적정 턴 수 실측 (목표 5~8턴, ux §14) |
| `reminder_opened` / `revise` / `keep` / `later` | 리마인드 루프 작동 여부. `keep`(그대로 봅니다)과 `later`는 반드시 구분 — 감쇠 로직의 입력(ux §3.5) |
| `judge_completed` / `review_written` / `review_deferred` | 판정→회고 전환율 |
| `drawer_opened` | 새 정보 서랍이 실제로 열리는가 |

세지 않는 것: 미룬 횟수·미열람의 사용자별 노출, 완료율 게이미피케이션 — 계측은
서버로만 가고 화면에는 어떤 형태로도 되돌아오지 않는다(P5).
