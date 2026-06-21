from __future__ import annotations

"""Рефералка «приведи друга».

Ссылка-приглашение - это deep-link Telegram: t.me/<bot>?start=ref_<user_id>.
Когда новый пользователь стартует по такой ссылке, и он, и пригласивший
получают бонус (дни полного доступа). В бете полный доступ и так у всех,
поэтому бонус «дозревает» к моменту включения платежей, а охват растет уже сейчас.
"""

from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User

REF_PREFIX = "ref_"
REFERRAL_REWARD_DAYS = 14


def referral_payload(user_id: int) -> str:
    return f"{REF_PREFIX}{user_id}"


def referral_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start={referral_payload(user_id)}"


def parse_ref_payload(payload: str | None) -> int | None:
    """Из «ref_123» достает 123. Возвращает None, если это не реферальный payload."""
    if not payload:
        return None
    payload = payload.strip()
    if not payload.startswith(REF_PREFIX):
        return None
    raw = payload[len(REF_PREFIX):]
    return int(raw) if raw.isdigit() else None


def _extend_full_access(user: User, days: int) -> None:
    now = datetime.utcnow()
    base = user.premium_until if (user.premium_until and user.premium_until > now) else now
    user.premium_until = base + timedelta(days=days)
    user.is_premium = True


async def try_register_referral(
    session: AsyncSession,
    new_user: User,
    referrer_user_id: int | None,
) -> User | None:
    """Привязывает приглашенного к пригласившему и начисляет бонус обоим.

    Возвращает пригласившего (для сообщения-благодарности) или None, если
    привязка невозможна (нет реферера, самоприглашение, уже привязан).
    """
    if referrer_user_id is None:
        return None
    if new_user.referred_by is not None:
        return None
    if referrer_user_id == new_user.id:
        return None

    referrer = (await session.execute(
        select(User).where(User.id == referrer_user_id)
    )).scalar_one_or_none()
    if referrer is None:
        return None

    new_user.referred_by = referrer.id
    referrer.referral_count = (referrer.referral_count or 0) + 1
    _extend_full_access(new_user, REFERRAL_REWARD_DAYS)
    _extend_full_access(referrer, REFERRAL_REWARD_DAYS)
    await session.commit()
    await session.refresh(new_user)
    return referrer


async def get_referral_count(session: AsyncSession, user: User) -> int:
    return user.referral_count or 0


def build_invite_text(referral_count: int) -> str:
    lines = [
        "👥 <b>Приглашай друзей - готовьтесь вместе</b>",
        "",
        "За каждого друга, который придет по твоей ссылке, вы оба получаете "
        f"<b>+{REFERRAL_REWARD_DAYS} дней полного доступа</b>.",
        "",
        f"Уже пришло по твоей ссылке: <b>{referral_count}</b>.",
        "",
        "Отправь другу ссылку ниже 👇",
    ]
    return "\n".join(lines)


def build_share_text() -> str:
    """Короткий текст, который подставится в окно «Поделиться»."""
    return (
        "Готовлюсь к ЕГЭ по русскому в этом боте - задания в формате ФИПИ, "
        "по 10 минут в день. Залетай, потренируемся вместе 👇"
    )


def share_dialog_url(bot_username: str, user_id: int) -> str:
    """Ссылка, открывающая окно Telegram «Поделиться» с приглашением и текстом."""
    link = referral_link(bot_username, user_id)
    return (
        "https://t.me/share/url?url="
        + quote(link, safe="")
        + "&text="
        + quote(build_share_text(), safe="")
    )
