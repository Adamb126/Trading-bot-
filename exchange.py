import ccxt
import logging
import time
from typing import Optional
import config

logger = logging.getLogger(__name__)


class ExchangeClient:
    def __init__(self):
        self.exchange = ccxt.kraken({
            "apiKey": config.API_KEY,
            "secret": config.API_SECRET,
            "enableRateLimit": True,
        })
        self.dry_run = config.DRY_RUN
        if self.dry_run:
            logger.info("DRY RUN mode — no real orders will be placed")

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list:
        for attempt in range(3):
            try:
                return self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            except ccxt.NetworkError as e:
                logger.warning(f"Network error fetching OHLCV (attempt {attempt+1}): {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError("Failed to fetch OHLCV after 3 attempts")

    def fetch_ticker(self, symbol: str) -> dict:
        return self.exchange.fetch_ticker(symbol)

    def fetch_balance(self) -> dict:
        if self.dry_run:
            return {"XRP": {"free": 0.0}, "EUR": {"free": config.TRADE_AMOUNT_EUR * 10}}
        return self.exchange.fetch_balance()

    def get_free_balance(self, currency: str) -> float:
        balance = self.fetch_balance()
        return balance.get(currency, {}).get("free", 0.0)

    def create_market_buy(self, symbol: str, amount_eur: float, current_price: float) -> Optional[dict]:
        xrp_amount = amount_eur / current_price
        if self.dry_run:
            order = {
                "id": f"dry_{int(time.time())}",
                "symbol": symbol,
                "side": "buy",
                "amount": xrp_amount,
                "price": current_price,
                "cost": amount_eur,
                "status": "closed",
            }
            logger.info(f"[DRY RUN] BUY {xrp_amount:.4f} XRP @ {current_price:.4f} EUR")
            return order
        order = self.exchange.create_market_buy_order(symbol, xrp_amount)
        logger.info(f"BUY order placed: {order}")
        return order

    def create_market_sell(self, symbol: str, xrp_amount: float, current_price: float) -> Optional[dict]:
        if self.dry_run:
            order = {
                "id": f"dry_{int(time.time())}",
                "symbol": symbol,
                "side": "sell",
                "amount": xrp_amount,
                "price": current_price,
                "cost": xrp_amount * current_price,
                "status": "closed",
            }
            logger.info(f"[DRY RUN] SELL {xrp_amount:.4f} XRP @ {current_price:.4f} EUR")
            return order
        order = self.exchange.create_market_sell_order(symbol, xrp_amount)
        logger.info(f"SELL order placed: {order}")
        return order

    def create_limit_buy(self, symbol: str, amount_eur: float, price: float) -> Optional[dict]:
        xrp_amount = amount_eur / price
        if self.dry_run:
            order = {
                "id": f"dry_{int(time.time())}",
                "symbol": symbol,
                "side": "buy",
                "amount": xrp_amount,
                "price": price,
                "cost": amount_eur,
                "status": "open",
            }
            logger.info(f"[DRY RUN] LIMIT BUY {xrp_amount:.4f} XRP @ {price:.4f} EUR")
            return order
        order = self.exchange.create_limit_buy_order(symbol, xrp_amount, price)
        logger.info(f"LIMIT BUY order placed: {order}")
        return order

    def create_limit_sell(self, symbol: str, xrp_amount: float, price: float) -> Optional[dict]:
        if self.dry_run:
            order = {
                "id": f"dry_{int(time.time())}",
                "symbol": symbol,
                "side": "sell",
                "amount": xrp_amount,
                "price": price,
                "cost": xrp_amount * price,
                "status": "open",
            }
            logger.info(f"[DRY RUN] LIMIT SELL {xrp_amount:.4f} XRP @ {price:.4f} EUR")
            return order
        order = self.exchange.create_limit_sell_order(symbol, xrp_amount, price)
        logger.info(f"LIMIT SELL order placed: {order}")
        return order

    def cancel_order(self, order_id: str, symbol: str):
        if self.dry_run:
            logger.info(f"[DRY RUN] Cancel order {order_id}")
            return
        self.exchange.cancel_order(order_id, symbol)
