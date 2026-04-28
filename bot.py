"""
bot.py — BingX Futures Telegram Bot
"""

import json
import logging
import os
import sys
import atexit
import time
import threading
import socket as _socket
from datetime import datetime, timezone
from pathlib import Path

import requests
import alerts as alert_mod

# ── Fix working directory (quan trọng khi chạy qua VBS/shortcut) ─────────────
BASE_DIR = Path(__file__).parent.resolve()
os.chdir(BASE_DIR)

from config import (
    TELEGRAM_TOKEN,
    TELEGRAM_CHAT_ID,
    SCAN_INTERVAL_SECONDS,
    ALERT_COOLDOWN_SECONDS,
    MAX_ALERTS_PER_SCAN,
    THRESHOLD_PERCENT,
    VOLUME_SPIKE_PERCENT,
    BINGX_BASE_URL,
)
from scanner import run_full_scan, get_filtered_symbols
from rsi_scanner import run_correction_scan, format_correction_message

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Khởi tạo alert module
alert_mod.init(BASE_DIR, BINGX_BASE_URL)

NOTIFIED_FILE = BASE_DIR / "notified.json"
PID_FILE      = BASE_DIR / "bot.pid"
TELEGRAM_API  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# ── Single-instance lock ──────────────────────────────────────────────────────
_instance_socket = None

def _acquire_instance_lock(port: int = 47832) -> bool:
    global _instance_socket
    _instance_socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        _instance_socket.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False

def _release_instance_lock():
    try:
        if _instance_socket:
            _instance_socket.close()
    except Exception:
        pass

def _write_pid():
    try:
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        pass

def _remove_pid():
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Chống scan chồng chéo ─────────────────────────────────────────────────────
_scan_lock = threading.Lock()


# ── Cooldown ──────────────────────────────────────────────────────────────────
def load_notified() -> dict:
    if NOTIFIED_FILE.exists():
        try:
            return json.loads(NOTIFIED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_notified(data: dict):
    NOTIFIED_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def is_on_cooldown(notified, symbol, timeframe):
    key = f"{symbol}_{timeframe}"
    last = notified.get(key)
    return last and (time.time() - last) < ALERT_COOLDOWN_SECONDS

def mark_notified(notified, symbol, timeframe):
    notified[f"{symbol}_{timeframe}"] = time.time()


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_message(text: str, reply_markup: dict = None, chat_id: str = None):
    payload = {
        "chat_id":    chat_id or TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Lỗi gửi Telegram: {e}")
        return None

def edit_message(chat_id, message_id, text: str, reply_markup: dict = None):
    payload = {
        "chat_id":    chat_id,
        "message_id": message_id,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        requests.post(f"{TELEGRAM_API}/editMessageText", json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Lỗi edit message: {e}")

def answer_callback(callback_query_id: str):
    try:
        requests.post(f"{TELEGRAM_API}/answerCallbackQuery",
                      json={"callback_query_id": callback_query_id}, timeout=5)
    except Exception:
        pass


# ── Keyboards ─────────────────────────────────────────────────────────────────
MAIN_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "🔥 Pump & Dump",    "callback_data": "pump"},
            {"text": "📈 Top 10 Gainers", "callback_data": "top10"},
        ],
        [
            {"text": "⚠️ Correction Signal", "callback_data": "correction"},
        ],
        [
            {"text": "🔔 Price Alerts", "callback_data": "alerts_menu"},
        ],
    ]
}

BACK_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "◀️ Quay lại", "callback_data": "back_main"},
    ]]
}

def build_alerts_keyboard() -> dict:
    """Tạo keyboard hiển thị danh sách alerts + nút xóa từng cái."""
    alerts = alert_mod._load()
    rows = []

    if alerts:
        for a in alerts:
            base  = a["symbol"].replace("-USDT", "")
            arrow = "📈" if a["direction"] == "above" else "📉"
            label = f"{arrow} {base} → {a['target']:g} USDT"
            rows.append([
                {"text": label,   "callback_data": f"noop"},
                {"text": "🗑 Xóa", "callback_data": f"del_alert_{a['id']}"},
            ])

    rows.append([
        {"text": "➕ Đặt Alert Mới", "callback_data": "alert_help"},
        {"text": "◀️ Quay lại",      "callback_data": "back_main"},
    ])
    return {"inline_keyboard": rows}

def tv_link(base: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol=BINGX:{base}USDT.P"


# ── Pump & Dump ───────────────────────────────────────────────────────────────
def get_pump_alerts() -> list:
    notified = load_notified()
    alerts   = run_full_scan()
    fresh = [a for a in alerts if not is_on_cooldown(notified, a["symbol"], a["timeframe"])]
    fresh.sort(key=lambda x: abs(x["percent_change"]), reverse=True)
    return fresh[:MAX_ALERTS_PER_SCAN]

def format_pump_message(alerts: list) -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if not alerts:
        return f"🔍 Không có tín hiệu bất thường lúc {now}."
    lines = [f"⚡ <b>PUMP &amp; DUMP BẤT THƯỜNG</b>  |  {now}\n"]
    for i, a in enumerate(alerts, 1):
        base  = a["symbol"].replace("-USDT", "").replace("_USDT", "")
        emoji = "🚀" if a["direction"] == "pump" else "💥"
        sign  = "+" if a["percent_change"] > 0 else ""
        lines.append(
            f"{i}. {emoji} <a href='{tv_link(base)}'><b>{base}</b></a>"
            f"  {sign}{a['percent_change']:.1f}%  [{a['timeframe']}]"
            f"  vol+{a['vol_spike']:.0f}%"
        )
    return "\n".join(lines)

def run_pump_scan(update_cooldown=True) -> str:
    alerts = get_pump_alerts()
    if update_cooldown and alerts:
        notified = load_notified()
        for a in alerts:
            mark_notified(notified, a["symbol"], a["timeframe"])
        save_notified(notified)
    return format_pump_message(alerts)


# ── Top 10 Gainers ────────────────────────────────────────────────────────────
def get_top10_gainers() -> list:
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        results = []
        for t in resp.json().get("data", []):
            try:
                price = float(t.get("lastPrice", 0))
                open_ = float(t.get("openPrice", 0))
                sym   = t.get("symbol", "")
                if price < 0.000001 or open_ < 0.000001 or not sym:
                    continue
                pct = ((price - open_) / open_) * 100
                if pct > 5000 or pct <= 0:
                    continue
                results.append({"symbol": sym, "pct": round(pct, 2), "price": price})
            except Exception:
                continue
        results.sort(key=lambda x: x["pct"], reverse=True)
        return results[:10]
    except Exception as e:
        logger.error(f"Lỗi lấy top10: {e}")
        return []

def format_top10_message(coins: list) -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    if not coins:
        return f"📈 Không lấy được dữ liệu lúc {now}."
    lines = [f"📈 <b>TOP 10 GAINERS — Futures</b>  |  {now}\n"]
    for i, c in enumerate(coins, 1):
        base = c["symbol"].replace("-USDT", "").replace("_USDT", "")
        lines.append(
            f"{i}. <a href='{tv_link(base)}'><b>{base}</b></a>"
            f"  +{c['pct']:.1f}%  —  {c['price']:.6g} USDT"
        )
    return "\n".join(lines)


# ── Correction Signal (RSI + H4 pump) ────────────────────────────────────────
def run_correction_check() -> str:
    """Quét correction signal trên top 10 gainers 24h."""
    coins = get_top10_gainers()
    symbols = [c["symbol"] for c in coins]
    if not symbols:
        return "⚠️ Không lấy được danh sách coin."
    signals = run_correction_scan(symbols)
    if not signals:
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        return f"✅ Không có Correction Signal lúc {now}.\n(Top 10 gainers chưa có coin nào pump H4 ≥50%)"
    msgs = [format_correction_message(s) for s in signals]
    return "\n\n──────────────\n\n".join(msgs)


# ── Auto scan scheduler ───────────────────────────────────────────────────────
def process_auto_scan():
    # Scan pump & dump thông thường
    alerts = get_pump_alerts()
    if alerts:
        send_message(format_pump_message(alerts), reply_markup=MAIN_KEYBOARD)
        notified = load_notified()
        for a in alerts:
            mark_notified(notified, a["symbol"], a["timeframe"])
        save_notified(notified)
        logger.info(f"Auto scan: gửi {len(alerts)} pump/dump alert")
    else:
        logger.info("Auto scan: không có tín hiệu pump/dump mới")

    # Scan correction signal — quét top 10 gainers 24h
    try:
        coins = get_top10_gainers()
        symbols = [c["symbol"] for c in coins]
        if symbols:
            signals = run_correction_scan(symbols)
            for sig in signals:
                key = f"correction_{sig['symbol']}"
                notified = load_notified()
                if not is_on_cooldown(notified, key, "correction"):
                    send_message(format_correction_message(sig), reply_markup=MAIN_KEYBOARD)
                    mark_notified(notified, key, "correction")
                    save_notified(notified)
                    logger.info(f"Correction signal gửi: {sig['symbol']}")
    except Exception as e:
        logger.error(f"Lỗi correction scan: {e}", exc_info=True)

def scheduler_loop():
    logger.info(f"Scheduler — nghỉ {SCAN_INTERVAL_SECONDS}s sau mỗi lần scan")
    while True:
        with _scan_lock:
            try:
                process_auto_scan()
            except Exception as e:
                logger.error(f"Lỗi scheduler: {e}", exc_info=True)
        time.sleep(SCAN_INTERVAL_SECONDS)


# ── Telegram update listener ──────────────────────────────────────────────────
def get_updates(offset: int = 0) -> list:
    try:
        resp = requests.get(
            f"{TELEGRAM_API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception:
        return []

def handle_updates():
    offset = 0
    logger.info("Update listener bắt đầu")
    while True:
        for update in get_updates(offset):
            offset = update["update_id"] + 1

            if "callback_query" in update:
                cb      = update["callback_query"]
                data    = cb.get("data", "")
                chat_id = str(cb["message"]["chat"]["id"])
                msg_id  = cb["message"]["message_id"]
                answer_callback(cb["id"])

                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if data == "pump":
                    edit_message(chat_id, msg_id, "🔄 Đang scan pump & dump...", reply_markup=MAIN_KEYBOARD)
                    def do_pump(cid=chat_id, mid=msg_id):
                        if _scan_lock.acquire(blocking=False):
                            try:
                                result = run_pump_scan()
                                edit_message(cid, mid, result, reply_markup=MAIN_KEYBOARD)
                            except Exception as e:
                                logger.error(f"Lỗi do_pump: {e}", exc_info=True)
                                edit_message(cid, mid, f"❌ Lỗi scan: {e}", reply_markup=MAIN_KEYBOARD)
                            finally:
                                _scan_lock.release()
                        else:
                            edit_message(cid, mid,
                                "⏳ Bot đang scan tự động, vui lòng thử lại sau ít phút.",
                                reply_markup=MAIN_KEYBOARD)
                    threading.Thread(target=do_pump, daemon=True).start()

                elif data == "top10":
                    edit_message(chat_id, msg_id, "🔄 Đang lấy top 10...", reply_markup=MAIN_KEYBOARD)
                    def do_top10(cid=chat_id, mid=msg_id):
                        edit_message(cid, mid, format_top10_message(get_top10_gainers()), reply_markup=MAIN_KEYBOARD)
                    threading.Thread(target=do_top10, daemon=True).start()

                elif data == "correction":
                    edit_message(chat_id, msg_id, "🔄 Đang quét Correction Signal...\n(mất 20-40 giây do quét RSI 10 khung)", reply_markup=MAIN_KEYBOARD)
                    def do_correction(cid=chat_id, mid=msg_id):
                        if _scan_lock.acquire(blocking=False):
                            try:
                                result = run_correction_check()
                                edit_message(cid, mid, result, reply_markup=MAIN_KEYBOARD)
                            except Exception as e:
                                logger.error(f"Lỗi correction: {e}", exc_info=True)
                                edit_message(cid, mid, f"❌ Lỗi: {e}", reply_markup=MAIN_KEYBOARD)
                            finally:
                                _scan_lock.release()
                        else:
                            edit_message(cid, mid, "⏳ Bot đang scan, thử lại sau ít phút.", reply_markup=MAIN_KEYBOARD)
                    threading.Thread(target=do_correction, daemon=True).start()

                elif data == "alerts_menu":
                    alerts = alert_mod._load()
                    if alerts:
                        text = f"🔔 <b>PRICE ALERTS ({len(alerts)} đang theo dõi)</b>\n\nBấm 🗑 để xóa từng alert:"
                    else:
                        text = "🔔 <b>PRICE ALERTS</b>\n\nChưa có alert nào.\nBấm ➕ để xem cách đặt."
                    edit_message(chat_id, msg_id, text, reply_markup=build_alerts_keyboard())

                elif data == "alert_help":
                    edit_message(
                        chat_id, msg_id,
                        "➕ <b>Đặt Price Alert</b>\n\n"
                        "Gõ lệnh vào chat:\n"
                        "<code>/alert COIN GIÁ</code>\n\n"
                        "Ví dụ:\n"
                        "• <code>/alert BTC 95000</code>\n"
                        "• <code>/alert ETH 2000</code>\n"
                        "• <code>/alert SOL 150</code>\n\n"
                        "Bot tự phát hiện hướng (lên/xuống) và báo ngay khi chạm mốc.",
                        reply_markup=BACK_KEYBOARD,
                    )

                elif data.startswith("del_alert_"):
                    try:
                        aid = int(data.replace("del_alert_", ""))
                        alert_mod.delete_alert(aid)
                    except Exception:
                        pass
                    alerts = alert_mod._load()
                    if alerts:
                        text = f"🔔 <b>PRICE ALERTS ({len(alerts)} đang theo dõi)</b>\n\nBấm 🗑 để xóa từng alert:"
                    else:
                        text = "🔔 <b>PRICE ALERTS</b>\n\nChưa có alert nào.\nBấm ➕ để xem cách đặt."
                    edit_message(chat_id, msg_id, text, reply_markup=build_alerts_keyboard())

                elif data == "back_main":
                    edit_message(
                        chat_id, msg_id,
                        "👋 <b>BingX Futures Bot</b>\nChọn chức năng:",
                        reply_markup=MAIN_KEYBOARD,
                    )

                elif data == "noop":
                    pass  # nút label alert, không làm gì

            elif "message" in update:
                msg     = update["message"]
                text    = msg.get("text", "").strip().lower()
                chat_id = str(msg["chat"]["id"])
                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text.startswith("/start") or text.startswith("/menu"):
                    send_message(
                        "👋 <b>BingX Futures Bot</b>\n\n"
                        "Chọn chức năng bên dưới hoặc dùng lệnh:\n"
                        "• <code>/rsi COIN</code> — xem RSI 10 khung của coin\n"
                        "• <code>/alert BTC 50000</code> — đặt price alert\n"
                        "• <code>/alerts</code> — xem danh sách alert\n"
                        "• <code>/delalert [id]</code> — xóa alert\n"
                        "• <code>/status</code> — trạng thái bot\n"
                        "• <code>/reset</code> — reset cooldown",
                        reply_markup=MAIN_KEYBOARD,
                    )

                elif text.startswith("/alert ") or text.startswith("/arlet "):
                    parts = text.split()
                    if len(parts) != 3:
                        send_message(
                            "⚠️ Cú pháp: <code>/alert COIN GIÁ</code>\n"
                            "Ví dụ: <code>/alert BTC 50000</code>",
                            reply_markup=MAIN_KEYBOARD,
                        )
                    else:
                        try:
                            target = float(parts[2])
                            def do_alert(sym=parts[1], tgt=target):
                                result = alert_mod.add_alert(sym, tgt)
                                send_message(result, reply_markup=MAIN_KEYBOARD)
                            threading.Thread(target=do_alert, daemon=True).start()
                        except ValueError:
                            send_message("⚠️ Giá không hợp lệ. Ví dụ: <code>/alert BTC 50000</code>", reply_markup=MAIN_KEYBOARD)

                elif text.startswith("/alerts"):
                    send_message(alert_mod.list_alerts_msg(), reply_markup=MAIN_KEYBOARD)

                elif text.startswith("/delalert "):
                    parts = text.split()
                    if len(parts) != 2:
                        send_message("⚠️ Cú pháp: <code>/delalert [id]</code>", reply_markup=MAIN_KEYBOARD)
                    else:
                        try:
                            aid = int(parts[1])
                            if alert_mod.delete_alert(aid):
                                send_message(f"🗑 Đã xóa alert <code>{aid}</code>.", reply_markup=MAIN_KEYBOARD)
                            else:
                                send_message(f"⚠️ Không tìm thấy alert <code>{aid}</code>.", reply_markup=MAIN_KEYBOARD)
                        except ValueError:
                            send_message("⚠️ ID không hợp lệ.", reply_markup=MAIN_KEYBOARD)

                elif text.startswith("/rsi "):
                    parts = text.split()
                    if len(parts) != 2:
                        send_message("⚠️ Cú pháp: <code>/rsi COIN</code>\nVí dụ: <code>/rsi HIGH</code>", reply_markup=MAIN_KEYBOARD)
                    else:
                        coin = parts[1].upper()
                        send_message(f"🔄 Đang lấy RSI của <b>{coin}</b>...", reply_markup=MAIN_KEYBOARD)
                        def do_rsi(c=coin):
                            from rsi_scanner import _check_multi_rsi, _check_h4_pump, RSI_OVERBOUGHT
                            # Thử các format symbol
                            for sym in [f"{c}-USDT", f"{c}_USDT", c]:
                                is_pump, pump_pct, price, h4_high = _check_h4_pump(sym)
                                red_count, details = _check_multi_rsi(sym)
                                if any(r is not None for _, r in details):
                                    break
                            lines = [f"🌡 <b>RSI đa khung — {c}/USDT</b>\n"]
                            for label, rsi in details:
                                if rsi is None:
                                    icon = "⬜"; val = "N/A"
                                elif rsi >= 80: icon = "🔴"; val = f"{rsi:.1f}"
                                elif rsi >= 60: icon = "🟠"; val = f"{rsi:.1f}"
                                elif rsi >= 50: icon = "🟡"; val = f"{rsi:.1f}"
                                else:           icon = "🟢"; val = f"{rsi:.1f}"
                                lines.append(f"  {icon} {label:<4} {val}")
                            lines.append(f"\n📊 H4 pump: <b>+{pump_pct:.1f}%</b>  |  RSI đỏ: <b>{red_count}/10</b>")
                            if red_count >= 7 and is_pump:
                                lines.append("⚠️ <b>Đủ điều kiện Correction Signal!</b>")
                            send_message("\n".join(lines), reply_markup=MAIN_KEYBOARD)
                        threading.Thread(target=do_rsi, daemon=True).start()

                elif text.startswith("/reset"):
                    save_notified({})
                    send_message("🗑 Đã reset cooldown.", reply_markup=MAIN_KEYBOARD)

                elif text.startswith("/status"):
                    scan_status = "🔄 Đang scan..." if _scan_lock.locked() else "✅ Rảnh"
                    n_alerts = len(alert_mod._load())
                    send_message(
                        f"⚙️ <b>Trạng thái</b>\n"
                        f"Scan: <b>{scan_status}</b>\n"
                        f"Price alerts: <b>{n_alerts} đang theo dõi</b>\n"
                        f"Ngưỡng pump/dump: <b>±{THRESHOLD_PERCENT:.0f}%</b>\n"
                        f"Volume spike: <b>+{VOLUME_SPIKE_PERCENT:.0f}%</b>\n"
                        f"Scan mỗi: <b>{SCAN_INTERVAL_SECONDS // 60} phút</b>\n"
                        f"Cooldown: <b>{ALERT_COOLDOWN_SECONDS // 3600}h / coin</b>",
                        reply_markup=MAIN_KEYBOARD,
                    )

                else:
                    send_message(
                        "👋 <b>BingX Futures Bot</b>\n\n"
                        "Chọn chức năng bên dưới hoặc dùng lệnh:\n"
                        "• <code>/alert BTC 50000</code> — đặt price alert\n"
                        "• <code>/alerts</code> — xem danh sách alert\n"
                        "• <code>/delalert [id]</code> — xóa alert",
                        reply_markup=MAIN_KEYBOARD,
                    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Trên cloud (Railway) bỏ qua single-instance lock vì chỉ có 1 container
    is_cloud = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT")
    if not is_cloud:
        if not _acquire_instance_lock():
            logger.error("Bot đang chạy rồi — thoát.")
            sys.exit(1)
        _write_pid()
        atexit.register(_release_instance_lock)
        atexit.register(_remove_pid)

    logger.info("🤖 Bot khởi động")
    send_message(
        f"🤖 <b>BingX Futures Bot</b> đã khởi động!\n"
        f"Ngưỡng: <b>±{THRESHOLD_PERCENT:.0f}%</b>  |  "
        f"Scan mỗi <b>{SCAN_INTERVAL_SECONDS // 60} phút</b>\n\n"
        "Chọn chức năng:",
        reply_markup=MAIN_KEYBOARD,
    )

    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=alert_mod.monitor_loop, args=(send_message,), daemon=True).start()
    handle_updates()
