"""Investor portfolio report endpoints (also drivable from the demo script)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.report_agent import ReportAgent
from app.database import get_session
from app.models.user import User, UserRole

router = APIRouter(prefix="/reports", tags=["reports"])
_agent = ReportAgent()


@router.get("/investor/{investor_id}")
async def investor_report(
    investor_id: int,
    send: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Generate an investor's portfolio report. ``send=true`` also Telegrams it."""
    investor = await session.get(User, investor_id)
    if investor is None or investor.role != UserRole.investor:
        raise HTTPException(status_code=404, detail="investor not found")

    report = await _agent.generate_investor_report(session, investor_id)
    delivered = False
    if send and report:
        from app.tools.telegram import send_text_message

        await send_text_message(investor.phone, report)
        delivered = True
    return {"investor_id": investor_id, "report": report, "sent": delivered}


@router.post("/weekly")
async def trigger_weekly_reports(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Manually trigger the weekly report broadcast (same path as the Celery beat task)."""
    return await _agent.send_weekly_reports(session)
