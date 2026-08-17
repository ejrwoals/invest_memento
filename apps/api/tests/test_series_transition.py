"""judge_end 도래 전이 (05 §5.5) — pending_judgment 로만 옮긴다.

자동 실패 처리가 아니다 — rejected 로 넘기는 코드는 존재하지 않는다.
"""

import asyncio
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Galae, Note, Notification, Scenario
from app.series.transition import transition_judgment

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date.today()


def _note() -> Note:
    return Note(
        user_id=USER_ID,
        target_type="ticker",
        target_name="삼성전자",
        thesis_summary="테스트",
        color="#2563eb",
    )


def test_due_galae_transitions_active_scenarios_and_notifies(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            due = Galae(question="지났다", judge_end=TODAY - timedelta(days=1))
            due.scenarios = [
                Scenario(name="a", resolution_type="manual"),
                Scenario(name="b", resolution_type="manual"),
                Scenario(name="이미 확정", resolution_type="manual", status="confirmed"),
            ]
            not_due = Galae(question="아직", judge_end=TODAY + timedelta(days=1))
            not_due.scenarios = [Scenario(name="c", resolution_type="manual")]
            today_edge = Galae(question="오늘이 판단일", judge_end=TODAY)
            today_edge.scenarios = [Scenario(name="d", resolution_type="manual")]
            note.galae = [due, not_due, today_edge]
            s.add(note)
            await s.commit()
            due_id, note_id = due.id, note.id

        async with series_db() as s:
            assert await transition_judgment(s) == 1

        async with series_db() as s:
            rows = (await s.scalars(select(Scenario))).all()
            by_name = {r.name: r.status for r in rows}
            assert by_name["a"] == "pending_judgment"
            assert by_name["b"] == "pending_judgment"
            assert by_name["이미 확정"] == "confirmed"  # active 만 옮긴다
            assert by_name["c"] == "active"  # judge_end 미도래
            assert by_name["d"] == "active"  # judge_end < today 엄격 — 당일은 아직
            assert "rejected" not in by_name.values()  # 자동 실패 처리는 없다

            galae = await s.get(Galae, due_id)
            assert galae is not None and galae.status == "open"  # 결론은 사용자가 낸다

            notifications = (await s.scalars(select(Notification))).all()
            assert len(notifications) == 1
            assert notifications[0].kind == "judgment_due"
            assert notifications[0].note_id == note_id
            assert notifications[0].payload["galae_id"] == str(due_id)

    asyncio.run(_run())


def test_transition_is_idempotent_across_runs(
    series_db: async_sessionmaker[AsyncSession],
) -> None:
    async def _run() -> None:
        async with series_db() as s:
            note = _note()
            due = Galae(question="지났다", judge_end=TODAY - timedelta(days=3))
            due.scenarios = [Scenario(name="a", resolution_type="manual")]
            note.galae = [due]
            s.add(note)
            await s.commit()

        async with series_db() as s:
            assert await transition_judgment(s) == 1
        async with series_db() as s:
            assert await transition_judgment(s) == 0  # 두 번째 실행은 아무 일도 없다

        async with series_db() as s:
            assert len((await s.scalars(select(Notification))).all()) == 1  # 알림도 한 번만

    asyncio.run(_run())
