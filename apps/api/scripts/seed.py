"""개발 시드 — 첫 사용자에게 샘플 노트 하나를 심는다.

실행: apps/api 에서 `uv run python scripts/seed.py` (.env 의 DATABASE_URL 사용).
멱등: 같은 target(005930)의 노트가 이미 있으면 지우고 다시 만든다.
확률은 손으로 박지 않고 domain.redistribute 를 두 번 돌려 실제 API와 같은
경로(5의 배수·합 100·이력 적재)로 만든다.
"""

import asyncio
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # apps/api 를 import 루트로

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_sessionmaker
from app.db.models import Galae, Instrument, Note, Premise, ProbabilityEntry, Scenario
from app.domain.probability import ScenarioProb, redistribute

SYMBOL = "005930"
JUDGE_END = date(2026, 12, 31)


async def _first_user_id(session: AsyncSession) -> UUID | None:
    row = await session.execute(
        text("select user_id from profiles order by created_at limit 1")
    )
    got = row.scalar_one_or_none()
    return got if got is None or isinstance(got, UUID) else UUID(str(got))


async def _wipe_previous(session: AsyncSession, user_id: UUID) -> int:
    """같은 target 의 기존 시드 노트를 지운다. residual 삭제 보호 트리거는
    cascade 삭제에도 발화하므로 is_residual 을 접어 무장해제한 뒤 지운다."""
    note_ids = (
        await session.scalars(
            select(Note.id).where(Note.user_id == user_id, Note.target_symbol == SYMBOL)
        )
    ).all()
    if not note_ids:
        return 0
    await session.execute(
        update(Scenario)
        .values(is_residual=False)
        .where(Scenario.galae_id.in_(select(Galae.id).where(Galae.note_id.in_(note_ids))))
    )
    await session.execute(delete(Note).where(Note.id.in_(note_ids)))
    return len(note_ids)


def _apply(
    session: AsyncSession, scenarios: list[Scenario], changed: Scenario, value: int, reason: str
) -> None:
    """갈래 확률 갱신 API 와 같은 경로 — redistribute → UPDATE + 바뀐 값만 이력 INSERT."""
    current = [
        ScenarioProb(id=str(s.id), probability=s.probability, is_residual=s.is_residual)
        for s in scenarios
    ]
    result = redistribute(current, str(changed.id), value)
    assert result is not None
    for s in scenarios:
        new_value = result[str(s.id)]
        if s.probability != new_value:
            session.add(
                ProbabilityEntry(
                    scenario_id=s.id,
                    value=new_value,
                    reason=reason if s.id == changed.id else None,
                )
            )
        s.probability = new_value


async def main() -> None:
    async with get_sessionmaker()() as session:
        user_id = await _first_user_id(session)
        if user_id is None:
            sys.exit("profiles 가 비어 있다 — 먼저 앱에 로그인해 사용자를 만들어야 한다.")

        await session.execute(
            pg_insert(Instrument)
            .values(symbol=SYMBOL, name="삼성전자", market="kr", currency="KRW")
            .on_conflict_do_nothing(index_elements=["symbol"])
        )
        wiped = await _wipe_previous(session, user_id)

        note = Note(
            user_id=user_id,
            target_type="ticker",
            target_symbol=SYMBOL,
            target_name="삼성전자",
            thesis_summary="HBM4 진입이 삼성전자의 리레이팅을 만든다",
            thesis_detail=(
                "HBM 공급 부족이 이어지는 동안 삼성전자가 HBM4 공급사로 진입하면 "
                "메모리 사이클과 무관한 재평가가 가능하다."
            ),
            color="#2563eb",
        )

        # 갈래 1 — manual 질문: 판정은 사용자가 직접 표시한다
        g1 = Galae(
            question="올해 안에 HBM4 공급사로 진입하는가?",
            judge_kind="date",
            judge_end=JUDGE_END,
            position=0,
        )
        g1.scenarios = [
            Scenario(name="연내 HBM4 공급사로 진입한다", resolution_type="manual", position=0),
            Scenario(name="진입이 내년 이후로 밀린다", resolution_type="manual", position=1),
            Scenario(
                name="그 외 예상 못한 전개",
                resolution_type="complement",
                is_residual=True,
                position=2,
            ),
        ]

        # 갈래 2 — auto 질문: 주가 조건은 배치가 판정 제안까지만 한다
        g2 = Galae(
            question="연말까지 주가가 95,000원을 넘는가?",
            judge_kind="date",
            judge_end=JUDGE_END,
            position=1,
        )
        g2.scenarios = [
            Scenario(
                name="95,000원을 넘는다",
                resolution_type="auto",
                series_provider="kis",
                series_code=SYMBOL,
                series_label="삼성전자",
                comparator="gte",
                target_value=Decimal("95000"),
                position=0,
            ),
            Scenario(name="95,000원에 못 미친다", resolution_type="manual", position=1),
            Scenario(
                name="그 외 예상 못한 전개",
                resolution_type="complement",
                is_residual=True,
                position=2,
            ),
        ]
        note.galae = [g1, g2]

        note.premises = [
            Premise(statement="HBM 공급 부족이 내년까지 이어져야 하고", position=0),
            Premise(statement="삼성이 HBM4 퀄 테스트를 연내 통과해야 하고", position=1),
            Premise(statement="파운드리 적자가 메모리 이익을 갉아먹지 않아야 한다", position=2),
        ]
        session.add(note)
        await session.flush()  # id 확정 — 이후 확률 이력이 scenario_id 를 쓴다

        # 확률은 갈래 1만 배분한다 — 빈 배분에서 60을 주고, 두 번째 답을 30으로 조정.
        # 결과 55/30/15: 전부 5의 배수, 합 100, 65/30/5 가 아니다.
        _apply(session, g1.scenarios, g1.scenarios[0], 60, "1차 배분 — HBM4 진입 우세로 본다")
        _apply(session, g1.scenarios, g1.scenarios[1], 30, "퀄 일정 지연 보도를 보고 상향")

        await session.commit()

        print(f"seeded for user {user_id} (기존 시드 노트 {wiped}개 삭제)")
        checks = {
            "notes": "select count(*) from notes where target_symbol = '005930'",
            "galae": (
                "select count(*) from galae g join notes n on n.id = g.note_id "
                "where n.target_symbol = '005930'"
            ),
            "scenarios": (
                "select count(*) from scenarios s join galae g on g.id = s.galae_id "
                "join notes n on n.id = g.note_id where n.target_symbol = '005930'"
            ),
            "residuals": (
                "select count(*) from scenarios s join galae g on g.id = s.galae_id "
                "join notes n on n.id = g.note_id "
                "where n.target_symbol = '005930' and s.is_residual"
            ),
            "galae_sum_100": (
                "select count(*) from (select s.galae_id from scenarios s "
                "join galae g on g.id = s.galae_id join notes n on n.id = g.note_id "
                "where n.target_symbol = '005930' "
                "group by s.galae_id having sum(s.probability) = 100) t"
            ),
            "prob_entries": (
                "select count(*) from probability_entries e "
                "join scenarios s on s.id = e.scenario_id "
                "join galae g on g.id = s.galae_id join notes n on n.id = g.note_id "
                "where n.target_symbol = '005930'"
            ),
        }
        for label, sql in checks.items():
            print(f"  {label}: {(await session.execute(text(sql))).scalar_one()}")


if __name__ == "__main__":
    asyncio.run(main())
