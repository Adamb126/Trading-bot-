# XRP/EUR Trading Bot

Automated trading bot for XRP/EUR on Kraken, with multi-indicator confluence strategy and strict daily risk management.

## Features

- **Exchange:** Kraken (XRP/EUR pair)
- **Strategy:** Confluence of RSI + MACD + Bollinger Bands + EMA + Volume
- **Daily profit target:** 10% — bot stops trading once reached
- **Daily stop-loss:** 7.5% — bot stops and closes position if breached
- **Dry-run mode** (default) — no real orders until you explicitly enable live trading

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and add your Kraken API key and secret
```

On Kraken, create an API key with **Trade** permission only (no withdrawal permission needed).

### 3. Configure trade size

Edit `.env` and set `TRADE_AMOUNT_EUR` to the EUR amount you want to risk per trade.

### 4. Test in dry-run mode first

```bash
DRY_RUN=true python bot.py
```

The bot prints signals and simulated orders without touching your funds.

### 5. Run backtests

```bash
python backtest.py --days 30
```

### 6. Go live

Only after you are satisfied with dry-run behaviour:

```bash
DRY_RUN=false python bot.py
```

## Architecture

| File | Purpose |
|------|---------|
| `bot.py` | Main loop, order execution, position tracking |
| `strategy.py` | Signal generation (buy/sell/hold) |
| `indicators.py` | RSI, MACD, Bollinger Bands, EMA, ATR, Volume |
| `risk_manager.py` | Daily P&L tracking, profit target, stop-loss |
| `exchange.py` | Kraken API wrapper (ccxt) |
| `config.py` | All configuration, read from `.env` |
| `backtest.py` | Historical simulation |

## Strategy Logic

A **BUY** signal requires ≥ 3 of 5 factors:
1. RSI < 35 (oversold)
2. MACD bullish crossover
3. Price near Bollinger lower band (< 20th percentile)
4. Fast EMA above slow EMA (uptrend)
5. Volume above 20-period average (1.3× or more)

A **SELL** signal requires ≥ 3 of 5 factors (inverse conditions).

## Risk Management

- Daily P&L is tracked from session start.
- Once **+10% profit** or **−7.5% loss** is reached, the bot locks and stops opening new trades for the rest of the day.
- If an open position's unrealised loss would push the total daily loss past 7.5%, an emergency market sell is triggered immediately.

## Disclaimer

This bot is provided for educational purposes. Cryptocurrency trading carries significant risk. Always test in dry-run mode first and never trade more than you can afford to lose.
