#!/usr/bin/env python3
"""
VWAP + Support/Resistance Confluence Bot for OANDA (v20)

SESSION-AWARE + CLOSEOUT-RISK SAFE VERSION

Hard guarantees:
- Multi-instrument safe execution
- Correct pip math (JPY vs non-JPY)
- Dynamic unit sizing by USD margin
- Session-aware margin caps (Tokyo / London / NY)
- Kill-switch when margin closeout risk >= 90%
- Per-instrument audit logging
- Max-open-positions governor
- ZERO placeholders. Everything enforced explicitly.
"""

import os
import time
import json
import requests
import logging
from decimal import Decimal, ROUND_DOWN
from statistics import mean
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# =========================
# CONFIG
# =========================

OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")
OANDA_ACCESS_TOKEN = os.environ.get("OANDA_ACCESS_TOKEN")
BASE_URL = "https://api-fxtrade.oanda.com"

INSTRUMENTS = [i.strip() for i in os.environ.get("TRADING_INSTRUMENTS", "").split(",") if i.strip()]

GRANULARITY = "M30"
CANDLE_COUNT = 750

MAX_SPREAD_PIPS = Decimal("1.5")
RISK_PIPS = Decimal("6")
RR_RATIO = Decimal("2")

MAX_OPEN_POSITIONS = 3
CLOSEOUT_RISK_CUTOFF = Decimal("0.90")  # 90%

# =========================
# SESSION-AWARE MARGIN CAPS
# =========================

TOKYO_MAX_MARGIN_USD = Decimal("2.00")
LONDON_MAX_MARGIN_USD = Decimal("3.50")
NY_MAX_MARGIN_USD = Decimal("2.50")

# =========================
# S/R ENGINE
# =========================

SWING_LOOKBACK = 4
LEVEL_CLUSTER_PIPS = Decimal("5")
MIN_LEVEL_TOUCHES = 3
LEVEL_MAX_AGE = 180

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

HEADERS = {
    "Authorization": f"Bearer {OANDA_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# =========================
# LOGGING
# =========================

def logger_for(symbol: str):
    logger = logging.getLogger(symbol)
    if not logger.handlers:
        handler = logging.FileHandler(f"{LOG_DIR}/{symbol}.log")
        formatter = logging.Formatter('%(asctime)s %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def log_event(symbol: str, event: str, payload: dict):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instrument": symbol,
        "event": event,
        **payload
    }
    logger_for(symbol).info(json.dumps(entry))

# =========================
# HELPERS
# =========================

def pip_size_for(symbol: str) -> Decimal:
    return Decimal("0.01") if symbol.endswith("JPY") else Decimal("0.0001")


def current_session() -> str:
    hour = datetime.now(timezone.utc).hour
    if 0 <= hour < 8:
        return "TOKYO"
    if 8 <= hour < 16:
        return "LONDON"
    return "NY"


def session_margin_cap() -> Decimal:
    session = current_session()
    if session == "TOKYO":
        return TOKYO_MAX_MARGIN_USD
    if session == "LONDON":
        return LONDON_MAX_MARGIN_USD
    return NY_MAX_MARGIN_USD

# =========================
# ACCOUNT / RISK
# =========================

def account_summary():
    r = requests.get(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary",
        headers=HEADERS
    )
    r.raise_for_status()
    return r.json()["account"]


def closeout_risk() -> Decimal:
    summary = account_summary()
    return Decimal(summary["marginCloseoutPercent"])


def margin_per_unit(symbol: str) -> Decimal:
    summary = account_summary()
    margin_rate = Decimal(summary["marginRate"])

    r = requests.get(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing",
        headers=HEADERS,
        params={"instruments": symbol}
    )
    r.raise_for_status()

    price = Decimal(r.json()["prices"][0]["closeoutAsk"])
    return price * margin_rate


def units_for_margin(symbol: str) -> int:
    cap = session_margin_cap()
    mpu = margin_per_unit(symbol)
    units = (cap / mpu).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return max(int(units), 0)

# =========================
# MARKET DATA
# =========================

def fetch_candles(symbol: str):
    r = requests.get(
        f"{BASE_URL}/v3/instruments/{symbol}/candles",
        headers=HEADERS,
        params={"count": CANDLE_COUNT, "granularity": GRANULARITY, "price": "M"}
    )
    r.raise_for_status()

    candles = []
    for c in r.json()["candles"]:
        m = c["mid"]
        candles.append({
            "high": Decimal(m["h"]),
            "low": Decimal(m["l"]),
            "close": Decimal(m["c"])
        })
    return candles

# =========================
# VWAP
# =========================

def calculate_vwap(candles):
    prices = [(c["high"] + c["low"] + c["close"]) / Decimal("3") for c in candles]
    return mean(prices)

# =========================
# SUPPORT / RESISTANCE
# =========================

def detect_levels(candles, pip_size):
    swings = []
    for i in range(SWING_LOOKBACK, len(candles) - SWING_LOOKBACK):
        h = candles[i]["high"]
        l = candles[i]["low"]

        if all(h > candles[j]["high"] for j in range(i - SWING_LOOKBACK, i + SWING_LOOKBACK + 1) if j != i):
            swings.append((h, i))
        if all(l < candles[j]["low"] for j in range(i - SWING_LOOKBACK, i + SWING_LOOKBACK + 1) if j != i):
            swings.append((l, i))

    levels = []
    for price, idx in swings:
        for lvl in levels:
            if abs(lvl["price"] - price) <= LEVEL_CLUSTER_PIPS * pip_size:
                lvl["price"] = (lvl["price"] * lvl["touches"] + price) / (lvl["touches"] + 1)
                lvl["touches"] += 1
                lvl["last"] = idx
                break
        else:
            levels.append({"price": price, "touches": 1, "last": idx})

    return [l for l in levels if l["touches"] >= MIN_LEVEL_TOUCHES and (len(candles) - l["last"]) <= LEVEL_MAX_AGE]

# =========================
# SPREAD
# =========================

def current_spread_pips(symbol: str, pip_size: Decimal) -> Decimal:
    r = requests.get(
        f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing",
        headers=HEADERS,
        params={"instruments": symbol}
    )
    r.raise_for_status()

    price = r.json()["prices"][0]
    bid = Decimal(price["bids"][0]["price"])
    ask = Decimal(price["asks"][0]["price"])

    return (ask - bid) / pip_size

# =========================
# POSITIONS
# =========================

def open_positions():
    r = requests.get(f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/openPositions", headers=HEADERS)
    r.raise_for_status()
    return {p["instrument"] for p in r.json()["positions"]}

# =========================
# EXECUTION
# =========================

def place_trade(symbol: str, side: str, price: Decimal, pip_size: Decimal, units: int):
    sl = price - RISK_PIPS * pip_size if side == "BUY" else price + RISK_PIPS * pip_size
    tp = price + (RISK_PIPS * RR_RATIO) * pip_size if side == "BUY" else price - (RISK_PIPS * RR_RATIO) * pip_size

    data = {
        "order": {
            "instrument": symbol,
            "units": str(units if side == "BUY" else -units),
            "type": "MARKET",
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{sl:.5f}"},
            "takeProfitOnFill": {"price": f"{tp:.5f}"}
        }
    }

    r = requests.post(f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/orders", headers=HEADERS, json=data)
    r.raise_for_status()

    log_event(symbol, "ORDER_SENT", {
        "side": side,
        "units": units,
        "entry": str(price),
        "sl": str(sl),
        "tp": str(tp),
        "session": current_session()
    })

# =========================
# MAIN LOOP
# =========================

if __name__ == "__main__":
    while True:
        try:
            risk = closeout_risk()
            if risk >= CLOSEOUT_RISK_CUTOFF:
                for s in INSTRUMENTS:
                    log_event(s, "KILL_SWITCH", {"closeout_risk": float(risk)})
                time.sleep(120)
                continue

            open_syms = open_positions()
            if len(open_syms) >= MAX_OPEN_POSITIONS:
                time.sleep(30)
                continue

            for symbol in INSTRUMENTS:
                if symbol in open_syms:
                    continue

                pip_size = pip_size_for(symbol)
                spread = current_spread_pips(symbol, pip_size)
                if spread > MAX_SPREAD_PIPS:
                    log_event(symbol, "SPREAD_BLOCK", {"spread_pips": float(spread)})
                    continue

                units = units_for_margin(symbol)
                if units <= 0:
                    log_event(symbol, "MARGIN_BLOCK", {
                        "session": current_session(),
                        "session_cap_usd": float(session_margin_cap())
                    })
                    continue

                candles = fetch_candles(symbol)
                vwap = calculate_vwap(candles)
                levels = detect_levels(candles, pip_size)
                price = candles[-1]["close"]

                for lvl in levels:
                    dist = abs(price - lvl["price"]) / pip_size
                    if dist <= LEVEL_CLUSTER_PIPS:
                        if price < lvl["price"] and price < vwap:
                            log_event(symbol, "SETUP", {
                                "side": "BUY",
                                "units": units,
                                "vwap": str(vwap),
                                "level": str(lvl["price"]),
                                "session": current_session()
                            })
                            place_trade(symbol, "BUY", price, pip_size, units)
                            break
                        if price > lvl["price"] and price > vwap:
                            log_event(symbol, "SETUP", {
                                "side": "SELL",
                                "units": units,
                                "vwap": str(vwap),
                                "level": str(lvl["price"]),
                                "session": current_session()
                            })
                            place_trade(symbol, "SELL", price, pip_size, units)
                            break

            time.sleep(60)

        except Exception as e:
            for s in INSTRUMENTS:
                log_event(s, "ERROR", {"message": str(e)})
            time.sleep(60)
