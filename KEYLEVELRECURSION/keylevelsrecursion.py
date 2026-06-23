#!/usr/bin/env python3
import os, time, threading, logging
import requests, pandas as pd
from decimal import Decimal, getcontext
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ============================================================
# ENV
# ============================================================
load_dotenv()
getcontext().prec = 10

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
TOKEN = os.getenv("OANDA_ACCESS_TOKEN")
INSTRUMENTS = [i.strip() for i in os.getenv("TRADING_INSTRUMENTS").split(",")]

REST = "https://api-fxtrade.oanda.com/v3"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

NY = ZoneInfo("America/New_York")

# ============================================================
# CONFIG
# ============================================================
UNITS = 100
RR = Decimal("2")
MIN_SL_PIPS = Decimal("5")
SPREAD_PAD = Decimal("1.5")
MAX_SPREAD = Decimal("0.80")
MARGIN_KILL = Decimal("0.90")
WICK_RATIO = Decimal("0.60")

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("keylevel_wick_bot.log", encoding="utf-8")
    ]
)
log = logging.getLogger("KEYLEVEL_WICK")

# ============================================================
# TIME / SESSION
# ============================================================
def now_ny():
    return datetime.now(tz=NY)

def session():
    h = now_ny().hour
    if 2 <= h < 11:
        return "LONDON"
    if 8 <= h < 17:
        return "NEWYORK"
    return "OFF"

def friday_shutdown():
    t = now_ny()
    return t.weekday() == 4 and t.hour >= 11

# ============================================================
# OANDA HELPERS
# ============================================================
def pip(inst):
    return Decimal("0.01") if "JPY" in inst else Decimal("0.0001")

def margin_ok():
    r = requests.get(f"{REST}/accounts/{ACCOUNT_ID}/summary", headers=HEADERS)
    r.raise_for_status()
    return Decimal(r.json()["account"]["marginCloseoutPercent"]) < MARGIN_KILL

def spread(inst):
    r = requests.get(
        f"{REST}/accounts/{ACCOUNT_ID}/pricing",
        headers=HEADERS,
        params={"instruments": inst}
    )
    r.raise_for_status()
    p = r.json()["prices"][0]
    bid = Decimal(p["bids"][0]["price"])
    ask = Decimal(p["asks"][0]["price"])
    return (ask - bid) / pip(inst)

# ============================================================
# MARKET DATA
# ============================================================
def floor_to_m15(dt):
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute)

def candles(inst, start, end):
    start_utc = floor_to_m15(start)
    end_utc = floor_to_m15(end - timedelta(minutes=15))  # force past candle

    if start_utc >= end_utc:
        return pd.DataFrame()

    r = requests.get(
        f"{REST}/instruments/{inst}/candles",
        headers=HEADERS,
        params={
            "granularity": "M15",
            "from": start_utc.isoformat().replace("+00:00", "Z"),
            "to": end_utc.isoformat().replace("+00:00", "Z"),
            "price": "M"
        },
        timeout=10
    )
    r.raise_for_status()

    rows = []
    for c in r.json().get("candles", []):
        if c.get("complete"):
            rows.append({
                "open": Decimal(c["mid"]["o"]),
                "high": Decimal(c["mid"]["h"]),
                "low": Decimal(c["mid"]["l"]),
                "close": Decimal(c["mid"]["c"])
            })

    return pd.DataFrame(rows)


# ============================================================
# KEY LEVELS
# ============================================================
def previous_day(inst):
    today = now_ny().date()
    start = datetime.combine(today - timedelta(days=1), datetime.min.time(), NY)
    end = datetime.combine(today, datetime.min.time(), NY)
    df = candles(inst, start, end)
    return df["high"].max(), df["low"].min()

# ============================================================
# WICK CONFIRMATION
# ============================================================
def wick_rejection(candle, level, direction):
    high, low, close, open_ = candle["high"], candle["low"], candle["close"], candle["open"]
    rng = high - low
    if rng == 0:
        return False

    if direction == "SHORT":
        upper_wick = high - max(open_, close)
        return high > level and close < level and (upper_wick / rng) >= WICK_RATIO

    if direction == "LONG":
        lower_wick = min(open_, close) - low
        return low < level and close > level and (lower_wick / rng) >= WICK_RATIO

    return False

# ============================================================
# ORDER
# ============================================================
def place(inst, units, sl, tp):
    payload = {
        "order": {
            "instrument": inst,
            "units": str(units),
            "type": "MARKET",
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{sl:.5f}"},
            "takeProfitOnFill": {"price": f"{tp:.5f}"}
        }
    }
    r = requests.post(f"{REST}/accounts/{ACCOUNT_ID}/orders", headers=HEADERS, json=payload)
    r.raise_for_status()
    log.info(f"TRADE | {inst} | units={units} SL={sl} TP={tp}")

# ============================================================
# STRATEGY
# ============================================================
def run(inst):
    log.info(f"BOT ACTIVE | {inst}")

    while True:
        try:
            if friday_shutdown() or session() == "OFF":
                time.sleep(60)
                continue

            if not margin_ok():
                log.warning(f"{inst} MARGIN KILL")
                time.sleep(300)
                continue

            sp = spread(inst)
            if sp > MAX_SPREAD:
                time.sleep(30)
                continue

            pdh, pdl = previous_day(inst)
            now = now_ny()
            df = candles(inst, now - timedelta(minutes=45), now)
            if len(df) < 2:
                time.sleep(30)
                continue

            last = df.iloc[-2]
            price = last["close"]
            pipsl = max(MIN_SL_PIPS, sp * SPREAD_PAD)
            sl_dist = pipsl * pip(inst)
            tp_dist = sl_dist * RR

            if wick_rejection(last, Decimal(pdh), "SHORT"):
                sl = price + sl_dist
                tp = price - tp_dist
                place(inst, -UNITS, sl, tp)

            elif wick_rejection(last, Decimal(pdl), "LONG"):
                sl = price - sl_dist
                tp = price + tp_dist
                place(inst, UNITS, sl, tp)

            log.info(
                f"HEARTBEAT | {inst} | Session={session()} "
                f"Price={price} PDH={pdh} PDL={pdl} Spread={sp:.2f}"
            )

            time.sleep(60)

        except Exception as e:
            log.error(f"{inst} ERROR | {e}")
            time.sleep(60)

# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    log.info("KEY LEVEL + WICK REJECTION BOT ACTIVE")
    for inst in INSTRUMENTS:
        threading.Thread(target=run, args=(inst,), daemon=True).start()
    while True:
        time.sleep(300)
        log.info("HEARTBEAT")
        for inst in INSTRUMENTS:
            log.info(f"HEARTBEAT | {inst} | Session={session()}")

        if friday_shutdown():
            log.info("FRIDAY SHUTDOWN")
            break