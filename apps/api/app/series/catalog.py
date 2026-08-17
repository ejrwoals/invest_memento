"""개별 주식 계열의 동적 등록 — 05-series-service §3.1.

지수·거시 계열은 010 마이그레이션이 시드하지만 **개별 주식은 시드하지 않는다.**
노트의 auto 조건이나 지켜보는 수치가 kis 종목을 처음 참조하는 순간 kind='equity' 로
upsert 한다 — series_snapshots 의 FK 가 series_catalog 에 걸려 있어, 등록 없이는
수집 배치의 스냅샷 insert 가 실패한다.
"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Instrument, SeriesCatalogEntry
from app.series.collect import is_kr_code


async def ensure_equity_series(session: AsyncSession, provider: str, code: str) -> bool:
    """kis 종목이 카탈로그에 없으면 등록한다. 새로 등록했으면 True.

    label 은 instruments 에서 찾고(symbol 또는 kis_code 일치), 없으면 코드 그대로 —
    이름은 나중에 고칠 수 있지만 FK 는 지금 필요하다. instruments 에도 없는 새 심볼
    (검색의 합성 후보 경로)은 instruments 부터 만든다 — market·currency 는 코드 형태로
    가른다(숫자=kr/KRW, 영문=us/USD — collect.is_kr_code 휴리스틱). flush 만 하고
    commit 은 호출자의 트랜잭션에 맡긴다.
    """
    if provider != "kis":
        return False
    exists = await session.scalar(
        select(SeriesCatalogEntry.code).where(
            SeriesCatalogEntry.provider == provider, SeriesCatalogEntry.code == code
        )
    )
    if exists is not None:
        return False
    instrument = await session.scalar(
        select(Instrument).where(or_(Instrument.symbol == code, Instrument.kis_code == code))
    )
    if instrument is None:
        kr = is_kr_code(code)
        session.add(
            Instrument(
                symbol=code,
                name=code,  # 이름은 나중에 고칠 수 있다 — 수집·판정에는 심볼이면 충분하다
                market="kr" if kr else "us",
                currency="KRW" if kr else "USD",
            )
        )
    label = instrument.name if instrument is not None else code
    session.add(
        SeriesCatalogEntry(
            provider=provider,
            code=code,
            label=label,
            kind="equity",
            unit=None,
            has_intraday=True,  # 주식은 장중이 있다 — 고가~저가 띠·장중 터치 판정의 전제
            search_keywords=[label, code] if label != code else [code],
        )
    )
    await session.flush()
    return True
