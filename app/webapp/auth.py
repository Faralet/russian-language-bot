"""Проверка Telegram WebApp initData.

Telegram присылает в Mini App строку initData. Подлинность проверяется по
HMAC-SHA256 с секретным ключом, выведенным из токена бота. Только так API
доверяет, что запрос пришёл от реального пользователя Telegram, а не подделан.

Алгоритм (официальный, Telegram WebApp):
  secret_key   = HMAC_SHA256(key="WebAppData", message=bot_token)
  check_string = "\n".join(sorted "key=value", кроме hash)
  ожидаемый hash = HMAC_SHA256(key=secret_key, message=check_string)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def parse_and_validate(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict | None:
    """Возвращает {"user": {...}, "auth_date": "...", "raw": {...}} или None при невалидности."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # Защита от переигрывания старого initData.
    if max_age_seconds:
        try:
            auth_date = int(pairs.get("auth_date", "0"))
            if auth_date and (time.time() - auth_date) > max_age_seconds:
                return None
        except ValueError:
            pass

    user = None
    if "user" in pairs:
        try:
            user = json.loads(pairs["user"])
        except Exception:
            user = None

    return {"user": user, "auth_date": pairs.get("auth_date"), "raw": pairs}
