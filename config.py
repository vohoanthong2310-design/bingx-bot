# ============================================================
#  CẤU HÌNH BOT
# ============================================================

TELEGRAM_TOKEN   = "8662908189:AAGhEZ1WeUf65A_-kWgmf-oSKrG4aNsDMLk"
TELEGRAM_CHAT_ID = "-5283990846"

# ============================================================
#  CÀI ĐẶT SCAN PUMP & DUMP
# ============================================================

# Ngưỡng % pump/dump trong 1 nến để gửi alert
THRESHOLD_PERCENT = 50.0

# Khung thời gian scan nến vừa đóng
TIMEFRAMES = ["1h", "4h"]

# Volume spike — chỉ hiển thị thêm, không dùng để lọc
VOLUME_SPIKE_PERCENT = 50.0

# Cooldown mỗi coin/khung (giây) — 1 tiếng tránh spam
ALERT_COOLDOWN_SECONDS = 1 * 60 * 60
SCAN_INTERVAL_SECONDS = 1800
# Top N coin mỗi lần scan
MAX_ALERTS_PER_SCAN = 10

# ============================================================
#  BINGX API
# ============================================================
BINGX_BASE_URL = "https://open-api.bingx.com"
