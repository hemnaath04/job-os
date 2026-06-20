from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models import Company


async def upsert_company(
    session: AsyncSession, *, name: str, domain: str | None = None
) -> Company:
    """Find by case-insensitive name + domain, or insert."""
    stmt = select(Company).where(func.lower(Company.name) == name.strip().lower())
    if domain:
        stmt = stmt.where(Company.domain == domain)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    company = Company(name=name.strip(), domain=domain)
    session.add(company)
    await session.flush()
    return company
