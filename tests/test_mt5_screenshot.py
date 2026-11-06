"""Обвязка ScreenshotEA: маппинг ТФ и проверка heartbeat."""
from __future__ import annotations

import os
import sys
import threading
import time

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mt5_screenshot  # noqa: E402


def test_tf_mapping():
    assert mt5_screenshot.tf_mql("15") == "M15"
    assert mt5_screenshot.tf_mql("240") == "H4"
    assert mt5_screenshot.tf_mql("1D") == "D1"
    assert mt5_screenshot.tf_mql("unknown") == "M15"


def test_unavailable_without_heartbeat(tmp_path):
    assert mt5_screenshot.available("") is False
    assert mt5_screenshot.available(str(tmp_path)) is False
    assert mt5_screenshot.request("XAUUSD", "15", data_path=str(tmp_path)) is None


def test_available_with_fresh_heartbeat(tmp_path):
    files = tmp_path / "MQL5" / "Files"
    files.mkdir(parents=True)
    hb = files / mt5_screenshot.HB
    hb.write_text("ts=1\n", encoding="utf-8")
    assert mt5_screenshot.available(str(tmp_path)) is True
    os.utime(hb, (time.time() - 120, time.time() - 120))
    assert mt5_screenshot.available(str(tmp_path)) is False


def test_request_roundtrip_with_fake_ea(tmp_path, monkeypatch):
    files = tmp_path / "MQL5" / "Files"
    files.mkdir(parents=True)
    renders = tmp_path / "renders"
    monkeypatch.setattr(mt5_screenshot, "available", lambda *a, **kw: True)
    monkeypatch.setattr(mt5_screenshot, "RENDERS", str(renders))
    monkeypatch.setattr(mt5_screenshot, "LOCK_PATH", str(tmp_path / "lock"))

    def fake_ea():
        req = files / mt5_screenshot.REQ
        for _ in range(100):
            if req.exists():
                data = {}
                for line in req.read_text(encoding="utf-8").splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        data[k] = v
                out = files / data["out"]
                Image.new("RGB", (20, 20), (30, 40, 50)).save(out)
                (files / mt5_screenshot.DONE).write_text(
                    f"status=ok\nout={data['out']}\nerror=\n", encoding="utf-8")
                return
            time.sleep(0.05)

    t = threading.Thread(target=fake_ea, daemon=True)
    t.start()
    path = mt5_screenshot.request("XAUUSD", "15", data_path=str(tmp_path), timeout=3)
    assert path and os.path.exists(path)
    assert os.path.dirname(path) == str(renders)
