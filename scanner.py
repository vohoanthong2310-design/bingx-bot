"""
scanner.py — Scan pump đột ngột theo nến vừa đóng:
  - Lấy toàn bộ ticker 1 lần → lọc coin biến động ≥10% trong 24h
  - Lấy nến H1/H4 vừa đóng của từng coin
  - Nếu nến đó pump/dump ≥ THRESHOLD_PERCENT → báo signal
  - Volume chỉ hiển thị thêm, không dùng để lọc
"""

import time
import requests
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    BINGX_BASE_URL,
    TIMEFRAMES,
    THRESHOLD_PERCENT,
    VOLUME_SPIKE_PERCENT,
    MAX_ALERTS_PER_SCAN,
)

logger = logging.getLogger(__name__)

MIN_PRICE    = 0.000001
MAX_PCT_CAP  = 2000.0
MAX_WORKERS  = 20
PRE_FILTER   = 10.0   # % thay đổi 24h tối thiểu để lọc vào danh sách scan


# ── Bước 1: Lấy toàn bộ ticker, lọc coin biến động ≥10% trong 24h ────────────
def get_filtered_symbols() -> list:
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        candidates = []
        total = 0
        for t in resp.json().get("data", []):
            try:
                sym   = t.get("symbol", "")
                price = float(t.get("lastPrice", 0))
                open_ = float(t.get("openPrice", 0))
                if not sym or price < MIN_PRICE or open_ < MIN_PRICE:
                    continue
                total += 1
                pct = abs((price - open_) / open_) * 100
                if pct >= PRE_FILTER and pct <= MAX_PCT_CAP:
                    candidates.append((sym, round(pct, 2)))
            except Exception:
                continue
        candidates.sort(key=lambda x: x[1], reverse=True)
        symbols = [s for s, _ in candidates[:80]]
        logger.info(f"Pre-filter: {len(symbols)} coin biến động ≥{PRE_FILTER}% / tổng {total}")
        return symbols
    except Exception as e:
        logger.error(f"Lỗi lấy ticker: {e}")
        return []


# ── Bước 2: Lấy nến vừa đóng của từng coin ───────────────────────────────────
def get_klines(symbol: str, interval: str, limit: int = 3):
    url = f"{BINGX_BASE_URL}/openApi/swap/v3/quote/klines"
    try:
        resp = requests.get(
            url,
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception:
        return None


def analyze_symbol(symbol: str) -> list:
    alerts = []
    for tf in TIMEFRAMES:
        # Lấy 3 nến: [-3]=cũ, [-2]=vừa đóng, [-1]=đang chạy
        klines = get_klines(symbol, tf, limit=12)
        if not klines or len(klines) < 3:
            continue
        try:
            # Nến vừa đóng là klines[-2]
            candle      = klines[-2]
            open_price  = float(candle["open"])
            close_price = float(candle["close"])
            high_price  = float(candle["high"])
            low_price   = float(candle["low"])
            volume      = float(candle["volume"])

            if open_price < MIN_PRICE or close_price < MIN_PRICE:
                continue

            pct = ((close_price - open_price) / open_price) * 100

            if abs(pct) > MAX_PCT_CAP:
                continue

            # Điều kiện chính: pump/dump ≥ ngưỡng trong 1 nến vừa đóng
            if abs(pct) < THRESHOLD_PERCENT:
                continue

            # Tính volume spike để hiển thị thêm
            vol_spike = None
            try:
                past_vols = [float(k["volume"]) for k in klines[:-2] if float(k["volume"]) > 0]
                if past_vols:
                    avg_vol = sum(past_vols) / len(past_vols)
                    if avg_vol > 0:
                        vol_spike = round(((volume - avg_vol) / avg_vol) * 100, 1)
            except Exception:
                pass

            alerts.append({
                "symbol":         symbol,
                "timeframe":      tf,
                "percent_change": round(pct, 2),
                "vol_spike":      vol_spike,
                "current_price":  close_price,
                "open_price":     open_price,
                "high_price":     high_price,
                "low_price":      low_price,
                "direction":      "pump" if pct > 0 else "dump",
                "scanned_at":     datetime.now(timezone.utc),
            })
        except Exception:
            continue
    return alerts


# ── Full scan ─────────────────────────────────────────────────────────────────
def run_full_scan() -> list:
    t0 = time.time()
    logger.info("=== Pump scan bắt đầu ===")

    symbols = get_filtered_symbols()
    if not symbols:
        return []

    all_alerts = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(analyze_symbol, s): s for s in symbols}
        for future in as_completed(futures):
            try:
                all_alerts.extend(future.result())
            except Exception as e:
                logger.warning(f"Lỗi scan {futures[future]}: {e}")

    all_alerts.sort(key=lambda x: abs(x["percent_change"]), reverse=True)
    elapsed = time.time() - t0
    logger.info(f"=== Xong: {len(all_alerts)} alert(s) từ {len(symbols)} coin trong {elapsed:.1f}s ===")
    return all_alerts
