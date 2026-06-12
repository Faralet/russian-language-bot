from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Payment, Subscription, User


async def activate_premium_month(
    session: AsyncSession,
    user: User,
    provider: str,
    provider_payment_id: str | None,
    amount: float,
    currency: str,
    raw_payload: dict[str, Any] | None = None,
) -> Subscription:
    settings = get_settings()
    now = datetime.utcnow()
    base_start = user.premium_until if user.premium_until and user.premium_until > now else now
    expires_at = base_start + timedelta(days=settings.premium_month_days)

    subscription = Subscription(
        user_id=user.id,
        plan_code="premium_month",
        status="active",
        started_at=now,
        expires_at=expires_at,
    )
    session.add(subscription)
    await session.flush()

    payment = Payment(
        user_id=user.id,
        subscription_id=subscription.id,
        provider=provider,
        provider_payment_id=provider_payment_id,
        amount=amount,
        currency=currency,
        status="succeeded",
        description="Premium Month",
        raw_payload=raw_payload,
        paid_at=now,
    )
    session.add(payment)

    user.is_premium = True
    user.premium_until = expires_at

    await session.commit()
    await session.refresh(subscription)
    return subscription
