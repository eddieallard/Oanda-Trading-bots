#!/usr/bin/env python3
import os, time, json, queue, threading, logging, statistics
import requests, pandas as pd
from decimal import Decimal, getcontext
from datetime import datetime, timezone
from dotenv import load_dotenv

# ============================
# ENV
# ============================
load_dotenv()
getcontext().prec = 12

ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
TOKEN = os.getenv("OANDA_ACCESS_TOKEN")
INSTRUMENTS = [i.strip() for i in os.getenv("TRADING_INSTRUMENTS").split(",")]

REST = "https://api-fxtrade.oanda.com/v3"

# ============================
# STRATEGY CONFIG
# ============================
EMA_FAST_BASE = 9
EMA_SLOW_BASE = 20
ATR_PERIOD = 14
ATR_MULT = Decimal("1.0")

BASE_R_USD = Decimal("0.16")
RR = Decimal("2.0")

SESSION_R_MULT = {
    "TOKYO": Decimal("0.75"),
    "LONDON": Decimal("1.25"),
    "NEWYORK": Decimal("1.00"),
}

MAX_TRADES_TOTAL = 3
MARGIN_KILL = Decimal("0.90")
IMBALANCE_THRESH = Decimal("0.20")

# ============================
# LOGGING
# ============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("ema_fixedR.log")]
)
log = logging.getLogger("FIXED_R")

# ============================
# OANDA API
# ============================
class OandaAPI:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {TOKEN}"})

    def req(self, m, e, p=None, d=None):
        r = self.s.request(m, f"{REST}{e}", params=p, json=d, timeout=10)
        r.raise_for_status()
        return r.json()

    def summary(self):
        return self.req("GET", f"/accounts/{ACCOUNT_ID}/summary")["account"]

    def open_trades(self):
        return self.req("GET", f"/accounts/{ACCOUNT_ID}/openTrades")["trades"]

    def market_order(self, inst, units, sl, tp):
        return self.req(
            "POST", f"/accounts/{ACCOUNT_ID}/orders",
            d={
                "order": {
                    "type": "MARKET",
                    "instrument": inst,
                    "units": str(units),
                    "timeInForce": "FOK",
                    "positionFill": "DEFAULT",
                    "takeProfitOnFill": {"price": str(tp)},
                    "stopLossOnFill": {"price": str(sl)},
                }
            }
        )

# ============================
# HTTP PRICING STREAM
# ============================
class PricingStream(threading.Thread):
    def __init__(self, instruments, q):
        super().__init__(daemon=True)
        self.instruments = instruments
        self.q = q

    def run(self):
        url = f"{REST}/accounts/{ACCOUNT_ID}/pricing/stream"
        params = {"instruments": ",".join(self.instruments)}
        headers = {"Authorization": f"Bearer {TOKEN}"}

        while True:
            try:
                with requests.get(url, headers=headers, params=params, stream=True, timeout=30) as r:
                    r.raise_for_status()
                    log.info("PRICING STREAM CONNECTED")
                    for line in r.iter_lines():
                        if not line:
                            continue
                        msg = json.loads(line.decode())
                        if msg.get("type") == "PRICE":
                            inst = msg["instrument"]
                            bid = Decimal(msg["bids"][0]["price"])
                            ask = Decimal(msg["asks"][0]["price"])
                            ts = datetime.fromisoformat(msg["time"].replace("Z", "+00:00"))
                            self.q.put((inst, ts, bid, ask))
            except Exception as e:
                log.error(f"PRICING STREAM ERROR: {e} - reconnect in 5s")
                time.sleep(5)

# ============================
# TRADER
# ============================
class Trader:
    def __init__(self):
        self.api = OandaAPI()
        self.q = queue.Queue(5000)
        self.candles = {i: [] for i in INSTRUMENTS}
        self.pressure = {i: [] for i in INSTRUMENTS}
        self.last_mid = {}
        PricingStream(INSTRUMENTS, self.q).start()

    def pip(self, inst):
        return Decimal("0.01") if inst.endswith("_JPY") else Decimal("0.0001")

    def session(self):
        h = datetime.now(timezone.utc).hour
        if h < 7:
            return "TOKYO"
        elif h < 13:
            return "LONDON"
        return "NEWYORK"

    def margin_ok(self):
        a = self.api.summary()
        used = Decimal(a["marginUsed"])
        nav = Decimal(a["NAV"])
        if used / nav >= MARGIN_KILL:
            log.critical("MARGIN KILL SWITCH TRIGGERED")
            return False
        return True

    def build_candle(self, inst, ts, mid):
        bucket = ts.replace(minute=(ts.minute // 15) * 15, second=0, microsecond=0)
        c = self.candles[inst]
        if not c or c[-1]["t"] != bucket:
            c.append({"t": bucket, "o": mid, "h": mid, "l": mid, "c": mid})
        else:
            x = c[-1]
            x["h"] = max(x["h"], mid)
            x["l"] = min(x["l"], mid)
            x["c"] = mid
        self.candles[inst] = c[-300:]

    def atr(self, inst):
        c = self.candles[inst]
        if len(c) < ATR_PERIOD + 2:
            return None
        trs = []
        for i in range(1, len(c)):
            h, l, pc = c[i]["h"], c[i]["l"], c[i-1]["c"]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return Decimal(str(pd.Series(trs).ewm(span=ATR_PERIOD).mean().iloc[-1]))

    def imbalance(self, inst):
        p = self.pressure[inst]
        if len(p) < 20:
            return None
        return Decimal(sum(p)) / Decimal(len(p))

    def signal(self, inst):
        c = self.candles[inst]
        if len(c) < EMA_SLOW_BASE + 5:
            return None

        atr = self.atr(inst)
        if not atr:
            return None

        factor = min(max(atr / atr, Decimal("0.75")), Decimal("2.5"))
        fast = int(EMA_FAST_BASE * factor)
        slow = int(EMA_SLOW_BASE * factor)

        s = pd.Series([float(x["c"]) for x in c])
        ef = s.ewm(span=fast).mean()
        es = s.ewm(span=slow).mean()

        imb = self.imbalance(inst)
        if imb is None:
            return None

        if ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1] and imb >= IMBALANCE_THRESH:
            return "buy"
        if ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1] and imb <= -IMBALANCE_THRESH:
            return "sell"
        return None

    def execute(self, inst, side, mid, spread_pips):
        open_trades = self.api.open_trades()
        if not self.margin_ok():
            return
        if len(open_trades) >= MAX_TRADES_TOTAL:
            return
        if any(t["instrument"] == inst for t in open_trades):
            return

        atr = self.atr(inst)
        if not atr:
            return

        session = self.session()
        r_usd = BASE_R_USD * SESSION_R_MULT[session]
        sl_dist = atr * ATR_MULT

        units = int((r_usd / sl_dist).to_integral_value())
        if units <= 0:
            return

        entry = mid
        if side == "buy":
            sl = entry - sl_dist
            tp = entry + (sl_dist * RR)
        else:
            units = -units
            sl = entry + sl_dist
            tp = entry - (sl_dist * RR)

        t0 = time.perf_counter_ns()
        self.api.market_order(inst, units, sl, tp)
        t1 = time.perf_counter_ns()

        log.info(
            f"FILLED {inst} {side.upper()} | "
            f"R=${r_usd:.2f} | "
            f"SL={sl:.5f} TP={tp:.5f} | "
            f"spread={spread_pips:.2f}p | "
            f"latency={(t1-t0)/1e6:.1f}ms"
        )

    def run(self):
        log.info("EMA FIXED-R BOT LIVE")
        while True:
            inst, ts, bid, ask = self.q.get()
            mid = (bid + ask) / 2
            spread_pips = (ask - bid) / self.pip(inst)

            last = self.last_mid.get(inst)
            if last:
                self.pressure[inst].append(1 if mid > last else -1 if mid < last else 0)
                self.pressure[inst] = self.pressure[inst][-50:]
            self.last_mid[inst] = mid

            self.build_candle(inst, ts, mid)

            sig = self.signal(inst)
            if sig:
                self.execute(inst, sig, mid, spread_pips)

# ============================
if __name__ == "__main__":
    Trader().run()
