"""Секреты не должны попадать в логи/HTTP, дефолтный webhook-секрет выключен."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402


def test_redact_removes_bot_token_from_requests_error(monkeypatch):
    monkeypatch.setattr(cfg, "BOT_TOKEN", "123456:AAH-secret")
    text = ("HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded "
            "with url: /bot123456:AAH-secret/getMe")
    out = cfg.redact(text)
    assert "123456:AAH-secret" not in out
    assert "bot***" in out


def test_redact_removes_sub_and_crypto_tokens(monkeypatch):
    monkeypatch.setattr(cfg, "SUB_BOT_TOKEN", "999:SUB-secret")
    monkeypatch.setattr(cfg, "CRYPTO_PAY_TOKEN", "crypto-secret")
    out = cfg.redact("x 999:SUB-secret y crypto-secret")
    assert "SUB-secret" not in out and "crypto-secret" not in out


def test_webhook_is_disabled_for_empty_or_default_secret(monkeypatch):
    for value in ("", "change-me", "change-me-to-long-random-string"):
        monkeypatch.setattr(cfg, "WEBHOOK_SECRET", value)
        assert not cfg.webhook_enabled()
    monkeypatch.setattr(cfg, "WEBHOOK_SECRET", "a-long-random-secret")
    assert cfg.webhook_enabled()
