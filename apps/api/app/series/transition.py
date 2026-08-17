"""judge_end 도래 → pending_judgment 전이 (05 §5.5).

시간이 지나 생기는 상태 변화를 옮기는 유일한 자리다 — 검증기의 일이 아니다.
자동 실패 처리가 아니다: rejected 로 넘기는 코드는 존재하지 않는다. 결론은
결과 확인 화면에서 사용자가 낸다.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Galae, Note, Notification, Scenario

logger = logging.getLogger(__name__)


async def transition_judgment(session: AsyncSession) -> int:
    """열린 갈래 중 judge_end < today 인 것의 active 시나리오를 pending_judgment 로.

    갈래당 한 번만 kind='judgment_due' 알림 행을 만든다 — 두 번째 실행부터는
    active 시나리오가 없어 아무 일도 일어나지 않는다(멱등).
    """
    today = date.today()
    rows = (
        await session.execute(
            select(Galae, Note)
            .join(Note, Galae.note_id == Note.id)
            .where(Galae.status == "open", Galae.judge_end < today)
        )
    ).all()

    transitioned_galae = 0
    for galae, note in rows:
        scenarios = (
            await session.scalars(
                select(Scenario).where(
                    Scenario.galae_id == galae.id, Scenario.status == "active"
                )
            )
        ).all()
        if not scenarios:
            continue  # 이미 전이됨 — 알림도 다시 만들지 않는다
        for scenario in scenarios:
            scenario.status = "pending_judgment"
        session.add(
            Notification(
                user_id=note.user_id,
                note_id=note.id,
                kind="judgment_due",
                payload={"galae_id": str(galae.id), "judge_end": galae.judge_end.isoformat()}
                if galae.judge_end is not None
                else {"galae_id": str(galae.id)},
                scheduled_for=datetime.now(UTC),
            )
        )
        transitioned_galae += 1

    await session.commit()
    logger.info("transition 완료: 도래 갈래 %d, 전이 %d", len(rows), transitioned_galae)
    return transitioned_galae
