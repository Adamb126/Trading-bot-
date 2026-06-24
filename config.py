import os
from dotenv import load_dotenv

load_dotenv()

# Exchange
EXCHANGE_ID = "kraken"
SYMBOL = "XRP/EUR"
BASE_CURRENCY = "XRP"
QUOTE_CURRENCY = "EUR"

# API credentials
API_KEY = os.getenv("KRAKEN_API_KEY", "")
API_SECRET = os.getenv("KRAKEN_API_SECRET", "")

# Trade sizing
TRADE_AMOUNT_EUR = float(os.getenv("TRADE_AMOUNT_EUR", "100"))

# Daily risk management
DAILY_PROFIT_TARGET = float(os.getenv("DAILY_PROFIT_TARGET", "0.10"))   # 10%
DAILY_STOP_LOSS = float(os.getenv("DAILY_STOP_LOSS", "0.075"))           # 7.5%

# Strategy parameters
TIMEFRAME = "15m"           # Candle timeframe for analysis
LOOKBACK_CANDLES = 200      # How many candles to fetch for analysis

# Technical indicator settings
RSI_PERIOD = 14
RSI_OVERSOLD = 45
RSI_OVERBOUGHT = 55

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

BB_PERIOD = 20
BB_STD = 2.0

EMA_FAST = 9
EMA_SLOW = 21

# Order settings
ORDER_TIMEOUT_SECONDS = 30  # Cancel unfilled limit orders after this
USE_LIMIT_ORDERS = True     # Use limit orders (True) or market orders (False)
LIMIT_ORDER_SLIPPAGE = 0.001  # 0.1% slippage tolerance for limit orders

# Dry-run mode — no real orders placed
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# Logging
LOG_FILE = "trading_bot.log"
LOG_LEVEL = "INFO"
