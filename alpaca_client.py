"""
Alpaca broker client for US stock trading.

Uses Alpaca's REST API directly (no heavy SDK dependency — just `requests`).
Mirrors the shape of the old Kraken ExchangeClient so the rest of the bot
barely changes.

Paper vs live is controlled by config.ALPACA_PAPER:
    True  -> https://paper-api.alpaca.markets  (fake money, real prices)
    False -> https://api.alpaca.markets         (REAL money)

Market data always comes from the free IEX feed.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

DATA_URL = "https://data.alpaca.markets"

# config.TIMEFRAME ("15m") -> Alpaca bar timeframe ("15Min")
_TF_MAP = {
    "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
    "1h": "1Hour", "1d": "1Day",
}


class AlpacaClient:
    def __init__(self):
        self.key = config.API_KEY
        self.secret = config.API_SECRET
        self.paper = config.ALPACA_PAPER
        self.trade_url = (
            "https://paper-api.alpaca.markets" if self.paper
            else "https://api.alpaca.markets"
        )
        self.dry_run = config.DRY_RUN
        self._headers = {
            "APCA-API-KEY-ID": self.key,
            "APCA-API-SECRET-KEY": self.secret,
        }
        mode = "PAPER" if self.paper else "LIVE (REAL MONEY)"
        logger.info(f"Alpaca client initialised — {mode}"
                    + (" | DRY RUN (no orders)" if self.dry_run else ""))

    # ── Low-level request with retry ───────────────────────────────────────────

    def _request(self, method: str, url: str, **kwargs) -> Optional[dict]:
        for attempt in range(3):
            try:
                resp = requests.request(method, url, headers=self._headers, timeout=15, **kwargs)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json() if resp.text else {}
            except requests.RequestException as e:
                logger.warning(f"Request error {method} {url} (attempt {attempt+1}): {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Failed {method} {url} after 3 attempts")

    # ── Market data ────────────────────────────────────────────────────────────

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list:
        """Return [[ts_ms, o, h, l, c, v], ...] — drop-in for ohlcv_to_df()."""
        tf = _TF_MAP.get(timeframe, "15Min")
        url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
        params = {"timeframe": tf, "limit": min(limit, 1000), "feed": "iex", "adjustment": "raw"}
        data = self._request("GET", url, params=params) or {}
        bars = data.get("bars", []) or []
        out = []
        for b in bars:
            ts = int(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000)
            out.append([ts, b["o"], b["h"], b["l"], b["c"], b["v"]])
        return out

    def fetch_ticker(self, symbol: str) -> dict:
        """Latest trade price. Returns {"last": price}."""
        url = f"{DATA_URL}/v2/stocks/{symbol}/trades/latest"
        data = self._request("GET", url, params={"feed": "iex"}) or {}
        price = data.get("trade", {}).get("p")
        if price is None:
            # Fall back to latest bar close
            bars = self.fetch_ohlcv(symbol, config.TIMEFRAME, 1)
            price = bars[-1][4] if bars else 0.0
        return {"last": float(price)}

    # ── Account & positions ────────────────────────────────────────────────────

    def is_market_open(self) -> bool:
        data = self._request("GET", f"{self.trade_url}/v2/clock") or {}
        return bool(data.get("is_open", False))

    def get_cash(self) -> float:
        """Free USD buying power (cash)."""
        if self.dry_run:
            return config.TRADE_AMOUNT_EUR * 10
        data = self._request("GET", f"{self.trade_url}/v2/account") or {}
        return float(data.get("cash", 0.0))

    def get_position_qty(self, symbol: str) -> float:
        """Shares currently held for a symbol (0 if none)."""
        if self.dry_run:
            return 0.0
        data = self._request("GET", f"{self.trade_url}/v2/positions/{symbol}")
        if not data:
            return 0.0
        return float(data.get("qty", 0.0))

    def get_position_avg_entry(self, symbol: str) -> float:
        if self.dry_run:
            return 0.0
        data = self._request("GET", f"{self.trade_url}/v2/positions/{symbol}")
        if not data:
            return 0.0
        return float(data.get("avg_entry_price", 0.0))

    # ── Orders ─────────────────────────────────────────────────────────────────

    def create_market_buy(self, symbol: str, amount_usd: float, current_price: float) -> Optional[dict]:
        qty_est = amount_usd / current_price if current_price else 0.0
        if self.dry_run:
            logger.info(f"[DRY RUN] BUY ${amount_usd:.2f} of {symbol} (~{qty_est:.4f} sh @ {current_price:.2f})")
            return {"symbol": symbol, "side": "buy", "amount": qty_est,
                    "price": current_price, "cost": amount_usd, "status": "filled"}
        body = {
            "symbol": symbol,
            "notional": round(amount_usd, 2),   # fractional dollar-based buy
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
        }
        order = self._request("POST", f"{self.trade_url}/v2/orders", json=body)
        if not order:
            return None
        logger.info(f"BUY order submitted: {symbol} ${amount_usd:.2f}")
        return {"symbol": symbol, "side": "buy", "amount": qty_est,
                "price": current_price, "cost": amount_usd, "status": order.get("status")}

    def create_market_sell(self, symbol: str, qty: float, current_price: float) -> Optional[dict]:
        if self.dry_run:
            logger.info(f"[DRY RUN] SELL {qty:.4f} sh of {symbol} @ {current_price:.2f}")
            return {"symbol": symbol, "side": "sell", "amount": qty,
                    "price": current_price, "cost": qty * current_price, "status": "filled"}
        body = {
            "symbol": symbol,
            "qty": round(qty, 6),
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
        }
        order = self._request("POST", f"{self.trade_url}/v2/orders", json=body)
        if not order:
            return None
        logger.info(f"SELL order submitted: {symbol} {qty:.4f} sh")
        return {"symbol": symbol, "side": "sell", "amount": qty,
                "price": current_price, "cost": qty * current_price, "status": order.get("status")}
