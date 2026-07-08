import os
from dotenv import load_dotenv

load_dotenv()

# ── Broker: Alpaca (US stocks) ────────────────────────────────────────────────
EXCHANGE_ID = "alpaca"

# Watchlist — the few stocks the bot trades. Comma-separated env var overrides.
WATCHLIST = [s.strip().upper() for s in os.getenv("WATCHLIST", "AAPL,MSFT,NVDA").split(",") if s.strip()]

# Single-symbol fallback (used by backtest / legacy code paths)
SYMBOL = WATCHLIST[0] if WATCHLIST else "AAPL"
QUOTE_CURRENCY = "USD"

# API credentials (Alpaca key + secret)
API_KEY = os.getenv("ALPACA_API_KEY", os.getenv("KRAKEN_API_KEY", ""))
API_SECRET = os.getenv("ALPACA_API_SECRET", os.getenv("KRAKEN_API_SECRET", ""))

# Paper (fake money) vs live (REAL money). Default paper for safety.
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

# Trade sizing — USD spent per position. (Env var name kept for compatibility.)
TRADE_AMOUNT_EUR = float(os.getenv("TRADE_AMOUNT_USD", os.getenv("TRADE_AMOUNT_EUR", "100")))

# Daily risk management
DAILY_PROFIT_TARGET = float(os.getenv("DAILY_PROFIT_TARGET", "0.10"))   # 10%
DAILY_STOP_LOSS = float(os.getenv("DAILY_STOP_LOSS", "0.075"))           # 7.5%

# Strategy parameters
TIMEFRAME = "15m"           # Candle timeframe for analysis
LOOKBACK_CANDLES = 200      # How many candles to fetch for analysis

# Technical indicator settings
RSI_PERIOD = 14
RSI_OVERSOLD = 38
RSI_OVERBOUGHT = 62

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
