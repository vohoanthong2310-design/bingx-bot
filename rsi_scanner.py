"""
rsi_scanner.py — Quét tín hiệu CORRECTION (Short):
  Điều kiện TIÊN QUYẾT: Nến H4 hiện tại đang pump ≥ 50%
  Thông tin bổ sung:    RSI đa khung (hiển thị để tham khảo, không lọc)
  → Gửi alert canh short lên Telegram
"""

import logging
import requests
from datetime import datetime, timezone

from config import BINGX_BASE_URL

logger = logging.getLogger(__name__)

# ── Cấu hình ─────────────────────────────────────────────────────────────────
H4_PUMP_THRESHOLD   = 50.0   # % pump H4 tối thiểu
RSI_OVERBOUGHT      = 60.0   # ngưỡng RSI quá mua (hạ từ 80 → 60)
RSI_MIN_TIMEFRAMES  = 4      # số khung lý tưởng (chỉ hiển thị, không lọc)
RSI_LENGTH          = 14     # chu kỳ RSI
MIN_PRICE           = 0.000001

# 5 khung thời gian
RSI_TIMEFRAMES = [
    ("15m", "15m"),
    ("1h",  "1h"),
    ("4h",  "4h"),
    ("1d",  "1d"),
    ("1w",  "1w"),
]

# ── Lấy kline từ BingX ───────────────────────────────────────────────────────
def _get_klines(symbol: str, interval: str, limit: int = 50):
    url = f"{BINGX_BASE_URL}/openApi/swap/v3/quote/klines"
    try:
        resp = requests.get(
            url,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        logger.debug(f"Lỗi kline {symbol} {interval}: {e}")
        return []


# ── Tính RSI ─────────────────────────────────────────────────────────────────
def _calc_rsi(closes: list, length: int = RSI_LENGTH) -> float | None:
    if len(closes) < length + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length

    for i in range(length, len(gains)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ── Kiểm tra H4 pump ─────────────────────────────────────────────────────────
def _check_h4_pump(symbol: str) -> tuple[bool, float, float, float]:
    """
    Trả về (is_pumping, pump_pct, current_price, h4_high)
    Tính (giá hiện tại - open nến H4) / open nến H4 * 100
    """
    klines = _get_klines(symbol, "4h", limit=3)
    if not klines:
        return False, 0.0, 0.0, 0.0
    try:
        current     = klines[-1]
        open_price  = float(current["open"])
        close_price = float(current["close"])
        high_price  = float(current["high"])
        if open_price < MIN_PRICE:
            return False, 0.0, 0.0, 0.0
        pump_pct = ((close_price - open_price) / open_price) * 100
        return pump_pct >= H4_PUMP_THRESHOLD, round(pump_pct, 2), close_price, high_price
    except Exception:
        return False, 0.0, 0.0, 0.0


# ── Kiểm tra RSI đa khung ────────────────────────────────────────────────────
def _check_multi_rsi(symbol: str) -> tuple[int, list]:
    """
    Trả về (số khung RSI ≥ 80, danh sách chi tiết)
    """
    red_count = 0
    details   = []

    for label, interval in RSI_TIMEFRAMES:
        # Cần đủ nến để tính RSI 14 + buffer
        klines = _get_klines(symbol, interval, limit=RSI_LENGTH * 3)
        if not klines:
            details.append((label, None))
            continue
        try:
            closes = [float(k["close"]) for k in klines]
            rsi    = _calc_rsi(closes)
            details.append((label, rsi))
            if rsi is not None and rsi >= RSI_OVERBOUGHT:
                red_count += 1
        except Exception:
            details.append((label, None))

    return red_count, details


# ── Scan 1 coin ──────────────────────────────────────────────────────────────
def analyze_correction_signal(symbol: str) -> dict | None:
    """
    Trả về dict nếu H4 pump ≥ 50% (điều kiện tiên quyết).
    RSI đa khung chỉ là thông tin bổ sung, không lọc.
    """
    # Điều kiện TIÊN QUYẾT: H4 pump ≥ 50%
    is_pumping, pump_pct, price, h4_high = _check_h4_pump(symbol)
    if not is_pumping:
        return None

    # Thông tin bổ sung: RSI đa khung (luôn lấy, không lọc)
    red_count, rsi_details = _check_multi_rsi(symbol)

    return {
        "symbol":      symbol,
        "pump_pct":    pump_pct,
        "price":       price,
        "h4_high":     h4_high,
        "red_count":   red_count,
        "rsi_details": rsi_details,
        "scanned_at":  datetime.now(timezone.utc),
    }


# ── Scan toàn bộ thị trường ──────────────────────────────────────────────────
def run_correction_scan(symbols: list) -> list:
    """
    Nhận list symbol (đã pre-filter pump mạnh), trả về list tín hiệu correction.
    """
    results = []
    for symbol in symbols:
        try:
            signal = analyze_correction_signal(symbol)
            if signal:
                results.append(signal)
                logger.info(f"[CORRECTION] {symbol} | H4 +{signal['pump_pct']}% | RSI đỏ {signal['red_count']}/10")
        except Exception as e:
            logger.warning(f"Lỗi analyze {symbol}: {e}")
    return results


# ── Tính thời gian đóng nến H4 tiếp theo ────────────────────────────────────
def _next_h4_close() -> str:
    """Trả về giờ đóng nến H4 tiếp theo (UTC)."""
    now = datetime.now(timezone.utc)
    # Nến H4 đóng lúc 0,4,8,12,16,20 UTC
    h4_closes = [0, 4, 8, 12, 16, 20, 24]
    for h in h4_closes:
        if now.hour < h:
            return f"{h:02d}:00 UTC"
    return "00:00 UTC"


# ── Tính Signal Entry ─────────────────────────────────────────────────────────
def calc_short_signal(price: float, high: float) -> dict:
    """Tính entry, SL, TP1-4 cho short x2."""
    sl   = round(high * 1.02, 8)        # SL = high nến H4 + 2%
    tp1  = round(price * 0.95, 8)       # -5%
    tp2  = round(price * 0.90, 8)       # -10%
    tp3  = round(price * 0.80, 8)       # -20%
    tp4  = round(price * 0.50, 8)       # -50%
    # Tính R:R dựa trên TP1
    risk   = abs(sl - price)
    reward = abs(price - tp1)
    rr     = round(reward / risk, 2) if risk > 0 else 0
    return {
        "entry": price,
        "sl":    sl,
        "tp1":   tp1,
        "tp2":   tp2,
        "tp3":   tp3,
        "tp4":   tp4,
        "rr":    rr,
    }


# ── Format message Telegram ───────────────────────────────────────────────────
def format_correction_message(signal: dict) -> str:
    base = signal["symbol"].replace("-USDT", "").replace("_USDT", "")
    tv   = f"https://www.tradingview.com/chart/?symbol=BINGX:{base}USDT.P"
    now  = signal["scanned_at"].strftime("%H:%M UTC")
    close_time = _next_h4_close()

    # Build RSI table
    rsi_lines = []
    for label, rsi in signal["rsi_details"]:
        if rsi is None:
            icon = "⬜"
            val  = "N/A"
        elif rsi >= 80:
            icon = "🔴"
            val  = f"{rsi:.1f}"
        elif rsi >= 60:
            icon = "🟠"
            val  = f"{rsi:.1f}"
        elif rsi >= 50:
            icon = "🟡"
            val  = f"{rsi:.1f}"
        else:
            icon = "🟢"
            val  = f"{rsi:.1f}"
        rsi_lines.append(f"  {icon} {label:<4} {val}")

    rsi_block = "\n".join(rsi_lines)

    # Tính signal entry dựa trên giá hiện tại
    sig = calc_short_signal(signal["price"], signal["h4_high"])

    # Format giá linh hoạt theo độ lớn
    def fmt(p):
        if p >= 100:
            return f"{p:.2f}"
        elif p >= 1:
            return f"{p:.4f}"
        else:
            return f"{p:.8g}"

    return (
        f"🚨 <b>CORRECTION SIGNAL — SHORT x2</b>  |  {now}\n\n"
        f"🎯 <a href='{tv}'><b>{base}/USDT</b></a>  —  {fmt(signal['price'])} USDT\n"
        f"📊 H4 pump: <b>+{signal['pump_pct']:.1f}%</b>  |  RSI ≥60: <b>{signal['red_count']}/10 khung</b>"
        f"{'  ⭐ Lý tưởng!' if signal['red_count'] >= 7 else ''}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>SHORT SIGNAL x2</b>\n"
        f"⏰ Entry khi đóng nến H4 lúc <b>{close_time}</b>\n\n"
        f"🔴 Entry:  <code>{fmt(sig['entry'])}</code>\n"
        f"🛑 SL:     <code>{fmt(sig['sl'])}</code>  (+2% từ high)\n\n"
        f"✅ TP1:   <code>{fmt(sig['tp1'])}</code>  (-5%)\n"
        f"✅ TP2:   <code>{fmt(sig['tp2'])}</code>  (-10%)\n"
        f"✅ TP3:   <code>{fmt(sig['tp3'])}</code>  (-20%)\n"
        f"✅ TP4:   <code>{fmt(sig['tp4'])}</code>  (-50%)\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>RSI đa khung:</b>\n{rsi_block}\n\n"
        f"⚡ <i>Chờ đóng nến H4 mới vào lệnh!</i>"
    )
