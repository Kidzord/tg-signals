"""Приём оплаты криптой через Crypto Pay (@CryptoBot).

Токен приложения берётся в @CryptoBot → Create App → API token и кладётся
в .env как CRYPTO_PAY_TOKEN. Цена — CRYPTO_PRICE_USDT (0 или пусто — способ
оплаты выключен, premium-бот работает только на Stars).

Документация: https://help.crypt.bot/crypto-pay-api
"""
from __future__ import annotations

import logging

import requests

import config as cfg

log = logging.getLogger("crypto_pay")

MAINNET = "https://pay.crypt.bot/api/"
TESTNET = "https://testnet-pay.crypt.bot/api/"


def _url() -> str:
    return TESTNET if cfg.CRYPTO_PAY_TESTNET else MAINNET


def enabled() -> bool:
    """Можно ли принимать крипту: есть токен приложения и задана цена."""
    return bool(cfg.CRYPTO_PAY_TOKEN) and cfg.CRYPTO_PRICE_USDT > 0


def _call(method: str, payload: dict | None = None,
          timeout: int = 20) -> tuple[bool, object]:
    if not cfg.CRYPTO_PAY_TOKEN:
        return False, "нет CRYPTO_PAY_TOKEN"
    try:
        r = requests.post(_url() + method,
                          headers={"Crypto-Pay-API-Token": cfg.CRYPTO_PAY_TOKEN},
                          json=payload or {}, timeout=timeout)
    except requests.RequestException as e:
        log.error("сеть %s: %s", method, e)
        return False, str(e)
    try:
        data = r.json()
    except ValueError:
        return False, f"не JSON: {r.text[:200]}"
    if not data.get("ok"):
        return False, str(data.get("error", "ошибка API"))
    return True, data.get("result")


def create_invoice(amount: float, description: str, payload: str,
                   expires_in: int = 3600) -> tuple[bool, dict | str]:
    """Счёт в крипте. Возвращает (успех, счёт со ссылкой на оплату либо ошибку)."""
    return _call("createInvoice", {
        "asset": cfg.CRYPTO_ASSET,
        "amount": f"{amount:.2f}",
        "description": description[:1024],
        "payload": payload[:1024],
        "expires_in": int(expires_in),
    })


def paid_invoices(count: int = 100) -> tuple[bool, list]:
    """Последние оплаченные счета — по ним выдаём подписку."""
    ok, res = _call("getInvoices", {"status": "paid", "count": int(count)})
    if not ok:
        return False, []
    items = res.get("items", []) if isinstance(res, dict) else []
    return True, items if isinstance(items, list) else []
