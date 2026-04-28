"""
alerts.py — Quản lý price alert realtime cho BingX bot
"""

import json
import time
import logging
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ALERTS_FILE: Path = None
BINGX_BASE_URL: str = None
_lock = threading.Lock()


def init(base_dir: Path, bingx_url: str):
    global ALERTS_FILE, BINGX_BASE_URL
    ALERTS_FILE  = base_dir / "alerts.json"
    BINGX_BASE_URL = bingx_url


# ── Đọc / ghi ────────────────────────────────────────────────────────────────
def _load() -> list:
    try:
        if ALERTS_FILE.exists():
            return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []

def _save(data: list):
    ALERTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Normalize symbol ──────────────────────────────────────────────────────────
def _normalize(symbol: str) -> str:
    s = symbol.upper().replace("_", "-")
    if "-" not in s:
        s = f"{s}-USDT"
    return s


# ── Lấy giá hiện tại từ BingX ─────────────────────────────────────────────────
def get_price(symbol: str) -> float | None:
    try:
        resp = requests.get(
            f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker",
            params={"symbol": symbol},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        # API trả về list hoặc dict tuỳ query
        if isinstance(data, list):
            for item in data:
                if item.get("symbol") == symbol:
                    return float(item["lastPrice"])
        elif isinstance(data, dict):
            return float(data["lastPrice"])
    except Exception:
        pass
    return None


# ── CRUD ──────────────────────────────────────────────────────────────────────
def add_alert(symbol: str, target: float) -> str:
    symbol = _normalize(symbol)

    current = get_price(symbol)
    if current is None:
        return f"❌ Không tìm thấy <b>{symbol}</b> trên BingX. Kiểm tra lại tên coin."

    if abs(current - target) < 1e-9:
        return "⚠️ Giá mục tiêu trùng với giá hiện tại."

    direction = "above" if target > current else "below"

    with _lock:
        alerts = _load()
        # Chặn trùng lặp
        for a in alerts:
            if a["symbol"] == symbol and abs(a["target"] - target) < 1e-9:
                return f"⚠️ Alert <b>{symbol} @ {target:g}</b> đã tồn tại rồi."

        alert_id = int(time.time()) % 100000
        alerts.append({
            "id":           alert_id,
            "symbol":       symbol,
            "target":       target,
            "direction":    direction,
            "price_at_set": round(current, 8),
            "created_at":   datetime.now(timezone.utc).strftime("%H:%M %d/%m"),
        })
        _save(alerts)

    base  = symbol.replace("-USDT", "")
    arrow = "📈" if direction == "above" else "📉"
    cond  = "vượt lên ≥" if direction == "above" else "rớt xuống ≤"
    return (
        f"✅ <b>Đặt alert thành công!</b>\n\n"
        f"{arrow} <b>{base}</b>\n"
        f"Giá hiện tại : <b>{current:g} USDT</b>\n"
        f"Giá mục tiêu : <b>{target:g} USDT</b>\n"
        f"Kích hoạt khi: <b>{cond} {target:g}</b>\n"
        f"ID : <code>{alert_id}</code>"
    )


def delete_alert(alert_id: int) -> bool:
    with _lock:
        alerts = _load()
        new = [a for a in alerts if a["id"] != alert_id]
        if len(new) == len(alerts):
            return False
        _save(new)
    return True


def list_alerts_msg() -> str:
    alerts = _load()
    if not alerts:
        return (
            "📋 <b>Chưa có alert nào.</b>\n\n"
            "Dùng lệnh:\n<code>/alert BTC 50000</code>"
        )
    lines = [f"📋 <b>PRICE ALERTS ({len(alerts)})</b>\n"]
    for a in alerts:
        base  = a["symbol"].replace("-USDT", "")
        arrow = "📈" if a["direction"] == "above" else "📉"
        lines.append(
            f"{arrow} <b>{base}</b> → <b>{a['target']:g} USDT</b>  "
            f"[đặt lúc {a['created_at']}]\n"
            f"   Xóa: <code>/delalert {a['id']}</code>"
        )
    return "\n".join(lines)


# ── Monitor thread ─────────────────────────────────────────────────────────────
def monitor_loop(send_fn, interval: int = 15):
    """Chạy ngầm, check giá mỗi `interval` giây, bắn alert khi chạm mốc."""
    logger.info(f"Price alert monitor — check mỗi {interval}s")
    while True:
        time.sleep(interval)
        try:
            with _lock:
                alerts = list(_load())

            if not alerts:
                continue

            # Gom các symbol cần fetch giá
            symbols = list({a["symbol"] for a in alerts})
            prices  = {s: get_price(s) for s in symbols}

            fired     = []
            remaining = []

            for a in alerts:
                price = prices.get(a["symbol"])
                if price is None:
                    remaining.append(a)
                    continue

                hit = (
                    (a["direction"] == "above" and price >= a["target"]) or
                    (a["direction"] == "below" and price <= a["target"])
                )

                if hit:
                    fired.append((a, price))
                else:
                    remaining.append(a)

            # Lưu lại danh sách chưa kích hoạt
            if fired:
                with _lock:
                    _save(remaining)

                for a, price in fired:
                    base  = a["symbol"].replace("-USDT", "")
                    arrow = "🚀" if a["direction"] == "above" else "💥"
                    diff  = abs(price - a["price_at_set"])
                    pct   = (diff / a["price_at_set"]) * 100 if a["price_at_set"] else 0
                    msg = (
                        f"🔔 <b>ALERT KÍCH HOẠT!</b>\n\n"
                        f"{arrow} <b>{base}</b> đã chạm mốc!\n"
                        f"Giá hiện tại : <b>{price:g} USDT</b>\n"
                        f"Mục tiêu     : <b>{a['target']:g} USDT</b>\n"
                        f"Giá lúc đặt  : <b>{a['price_at_set']:g} USDT</b>  "
                        f"({'+' if price >= a['price_at_set'] else ''}{pct:.1f}%)"
                    )
                    send_fn(msg)
                    logger.info(f"Alert fired: {a['symbol']} @ {price} (target {a['target']})")

        except Exception as e:
            logger.error(f"Lỗi alert monitor: {e}")
