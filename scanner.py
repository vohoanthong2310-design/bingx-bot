"""
scanner.py — Scan pump & dump thông minh:
  Bước 1: Lấy toàn bộ ticker 1 lần → lọc coin biến động mạnh
  Bước 2: Chỉ fetch kline của coin đã lọc (~20-30 coin)
  → Nhanh hơn 20x, không lag máy
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
MAX_PCT_CAP  = 5000.0
KLINE_LIMIT  = 11
MAX_WORKERS  = 20
PRE_FILTER   = 5.0   # % thay đổi 24h tối thiểu để đưa vào danh sách kline


# ── Bước 1: Lấy toàn bộ ticker 1 lần, lọc nhanh ─────────────────────────────
def get_filtered_symbols() -> list:
    """Lấy all tickers, trả về list symbol có biến động 24h đáng kể."""
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        candidates = []
        for t in resp.json().get("data", []):
            try:
                sym    = t.get("symbol", "")
                price  = float(t.get("lastPrice", 0))
                open_  = float(t.get("openPrice", 0))
                if not sym or price < MIN_PRICE or open_ < MIN_PRICE:
                    continue
                pct = abs((price - open_) / open_) * 100
                if pct >= PRE_FILTER and pct <= MAX_PCT_CAP:
                    candidates.append((sym, round(pct, 2)))
            except Exception:
                continue
        # Sắp xếp theo % giảm dần, giữ top 60 coin biến động nhất
        candidates.sort(key=lambda x: x[1], reverse=True)
        symbols = [s for s, _ in candidates[:60]]
        logger.info(f"Pre-filter: {len(symbols)} coin biến động ≥{PRE_FILTER}% / tổng {len(resp.json().get('data', []))}")
        return symbols
    except Exception as e:
        logger.error(f"Lỗi lấy ticker: {e}")
        return []


# ── Bước 2: Fetch kline cho từng coin đã lọc ─────────────────────────────────
def get_klines(symbol: str, interval: str, limit: int = KLINE_LIMIT):
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
        klines = get_klines(symbol, tf, KLINE_LIMIT)
        if not klines or len(klines) < 3:
            continue
        try:
            current     = klines[-1]
            open_price  = float(current["open"])
            close_price = float(current["close"])
            current_vol = float(current["volume"])

            if open_price < MIN_PRICE or close_price < MIN_PRICE:
                continue

            past_vols = [float(k["volume"]) for k in klines[:-1] if float(k["volume"]) > 0]
            if not past_vols:
                continue
            avg_vol = sum(past_vols) / len(past_vols)
            if avg_vol <= 0:
                continue

            pct       = ((close_price - open_price) / open_price) * 100
            vol_spike = ((current_vol - avg_vol) / avg_vol) * 100

            if abs(pct) > MAX_PCT_CAP:
                continue

            if abs(pct) >= THRESHOLD_PERCENT and vol_spike >= VOLUME_SPIKE_PERCENT:
                alerts.append({
                    "symbol":         symbol,
                    "timeframe":      tf,
                    "percent_change": round(pct, 2),
                    "vol_spike":      round(vol_spike, 1),
                    "current_price":  close_price,
                    "open_price":     open_price,
                    "direction":      "pump" if pct > 0 else "dump",
                    "scanned_at":     datetime.now(timezone.utc),
                })
        except Exception:
            continue
    return alerts


# ── Full scan ─────────────────────────────────────────────────────────────────
def run_full_scan() -> list:
    t0 = time.time()
    logger.info("=== Scan bắt đầu ===")

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
