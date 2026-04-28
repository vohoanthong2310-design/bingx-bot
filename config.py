# ============================================================
#  CẤU HÌNH BOT
# ============================================================

TELEGRAM_TOKEN   = "8662908189:AAGhEZ1WeUf65A_-kWgmf-oSKrG4aNsDMLk"
TELEGRAM_CHAT_ID = "-5283990846"

# ============================================================
#  CÀI ĐẶT SCAN
# ============================================================

# Ngưỡng % thay đổi để gửi alert (pump hoặc dump)
THRESHOLD_PERCENT = 20.0

# Các khung thời gian scan (M1 → H4)
TIMEFRAMES = ["15m", "1h", "4h"]

# Ngưỡng volume đột biến so với trung bình
VOLUME_SPIKE_PERCENT = 50.0

# Tần suất scan (giây) — mặc định 2 phút (do có M1/M5)
SCAN_INTERVAL_SECONDS = 1800

# Cooldown mỗi coin (giây) — 1 tiếng
ALERT_COOLDOWN_SECONDS = 1 * 60 * 60

# Top N coin mỗi lần scan
MAX_ALERTS_PER_SCAN = 10

# ============================================================
#  BINGX API
# ============================================================
BINGX_BASE_URL = "https://open-api.bingx.com"
