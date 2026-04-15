"""
Lightweight price watcher — ZERO LLM calls. Pure Python math.

Modes:
  python price_watcher.py             → crypto + gold watcher (every 15 min via cron)
  python price_watcher.py --stocks-only → stock watcher (once daily 9:30pm SGT)
  python price_watcher.py --test      → test all fetchers, print results, no alerts

Reads/writes memory.json for price baseline and alert_flags.
Sends immediate Telegram alert if price moves >= ALERT_THRESHOLD.
Alpha Vantage budget: 25 calls/day — only runs once daily for stocks.
"""

import os
import sys
import json
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOLD_API_KEY     = os.getenv("GOLD_API_KEY", "")
AV_API_KEY       = os.getenv("ALPHAVANTAGE_API_KEY", "")

ALERT_THRESHOLD = 2.0  # percent — alert if abs(change) >= this

BASE_DIR       = Path(__file__).parent
MEMORY_FILE    = BASE_DIR / "memory.json"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
GOLD_URL    = "https://api.gold-api.com/price/XAU"
AV_URL      = "https://www.alphavantage.co/query"

# ── Memory helpers ────────────────────────────────────────────────────────────

def load_memory() -> dict:
    try:
        if MEMORY_FILE.exists():
            with open(MEMORY_FILE) as f:
                return json.load(f)
    except Exception as e:
        print(f"[Watcher] Memory load error: {e}")
    return {"last_run": None, "prices": {}, "alert_flags": []}


def save_memory(data: dict):
    try:
        data["last_updated"] = datetime.now().isoformat()
        with open(MEMORY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Watcher] Memory save error: {e}")


def load_watchlist() -> dict:
    try:
        if WATCHLIST_FILE.exists():
            with open(WATCHLIST_FILE) as f:
                return json.load(f)
    except Exception as e:
        print(f"[Watcher] Watchlist load error: {e}")
    return {"crypto": ["BTC", "ETH"], "stocks": ["DELL"], "gold": True}


# ── Telegram alert (no LLM) ───────────────────────────────────────────────────

def send_telegram_alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Watcher] Telegram not configured — would have sent: {message[:100]}")
        return
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(tg_url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }, timeout=10)
        if r.status_code == 400:
            r = requests.post(tg_url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text":    message,
            }, timeout=10)
        r.raise_for_status()
        print(f"[Watcher] Alert sent: {message[:80]}")
    except Exception as e:
        print(f"[Watcher] Telegram error: {e}")


# ── Price fetchers (zero LLM) ─────────────────────────────────────────────────

def fetch_crypto_price(symbol: str) -> float | None:
    """Fetch crypto price from Binance. Returns float or None on error."""
    try:
        r = requests.get(BINANCE_URL, params={"symbol": f"{symbol.upper()}USDT"}, timeout=10)
        r.raise_for_status()
        price = float(r.json().get("price", 0))
        return price if price else None
    except Exception as e:
        print(f"[Watcher] Binance {symbol} error: {e}")
        return None


def fetch_gold_price() -> float | None:
    """Fetch XAU from gold-api.com. Returns float or None on error."""
    try:
        headers = {"x-access-token": GOLD_API_KEY} if GOLD_API_KEY else {}
        r = requests.get(GOLD_URL, headers=headers, timeout=10)
        r.raise_for_status()
        price = float(r.json().get("price", 0))
        return price if price else None
    except Exception as e:
        print(f"[Watcher] Gold API error: {e}")
        return None


def fetch_stock_price(symbol: str) -> float | None:
    """Fetch stock closing price from Alpha Vantage. Returns float or None on error."""
    if not AV_API_KEY:
        print(f"[Watcher] ALPHAVANTAGE_API_KEY not set — skipping {symbol}")
        return None
    try:
        r = requests.get(AV_URL, params={
            "function": "GLOBAL_QUOTE",
            "symbol":   symbol.upper(),
            "apikey":   AV_API_KEY,
        }, timeout=15)
        r.raise_for_status()
        quote = r.json().get("Global Quote", {})
        price = float(quote.get("05. price", 0))
        return price if price else None
    except Exception as e:
        print(f"[Watcher] Alpha Vantage {symbol} error: {e}")
        return None


# ── Change calculation ────────────────────────────────────────────────────────

def calc_change(current: float, baseline: float) -> float:
    """Calculate percentage change. Zero LLM — pure math."""
    if not baseline or baseline == 0:
        return 0.0
    return ((current - baseline) / baseline) * 100


def flag_alert(memory: dict, asset_type: str, symbol: str, change_pct: float, price: float):
    """Write alert flag to memory.json and send immediate Telegram alert."""
    arrow = "🔺" if change_pct > 0 else "🔻"
    emoji_map = {"crypto": "💎", "gold": "🥇", "stocks": "📊"}
    emoji = emoji_map.get(asset_type, "📈")

    alert = {
        "asset_type":   asset_type,
        "symbol":       symbol,
        "change_pct":   round(change_pct, 2),
        "current_price": round(price, 2),
        "timestamp":    datetime.now().isoformat(),
    }

    flags = memory.get("alert_flags", [])
    # Update existing flag for same asset or append new
    updated = False
    for i, f in enumerate(flags):
        if f.get("symbol") == symbol and f.get("asset_type") == asset_type:
            flags[i] = alert
            updated = True
            break
    if not updated:
        flags.append(alert)
    memory["alert_flags"] = flags

    tg_msg = (
        f"{emoji} *{symbol} ALERT* {arrow}\n"
        f"Price: ${price:,.2f} | Change: {change_pct:+.2f}%\n"
        f"_Morning briefing will include analysis._"
    )
    send_telegram_alert(tg_msg)
    print(f"[Watcher] Flagged: {symbol} {change_pct:+.2f}%")


# ── Watcher modes ─────────────────────────────────────────────────────────────

def watch_crypto_and_gold(memory: dict, watchlist: dict, test_mode: bool = False):
    """
    Watch crypto (Binance) and gold (gold-api.com).
    Runs every 15 minutes. Zero LLM.
    """
    prices = memory.setdefault("prices", {})

    # Crypto
    crypto_list = watchlist.get("crypto", [])
    for symbol in crypto_list:
        price = fetch_crypto_price(symbol)
        if price is None:
            continue
        print(f"[Watcher] {symbol}: ${price:,.2f}")

        baseline_entry = prices.get("crypto", {}).get(symbol, {})
        baseline = baseline_entry.get("price")

        if baseline:
            change = calc_change(price, baseline)
            print(f"[Watcher] {symbol} change: {change:+.2f}%")
            if not test_mode and abs(change) >= ALERT_THRESHOLD:
                flag_alert(memory, "crypto", symbol, change, price)

        # Update baseline
        prices.setdefault("crypto", {})[symbol] = {
            "price":     price,
            "timestamp": datetime.now().isoformat(),
        }

    # Gold
    if watchlist.get("gold"):
        price = fetch_gold_price()
        if price is not None:
            print(f"[Watcher] XAU: ${price:,.2f}")

            baseline_entry = prices.get("gold", {}).get("XAU", {})
            baseline = baseline_entry.get("price")

            if baseline:
                change = calc_change(price, baseline)
                print(f"[Watcher] XAU change: {change:+.2f}%")
                if not test_mode and abs(change) >= ALERT_THRESHOLD:
                    flag_alert(memory, "gold", "XAU", change, price)

            prices.setdefault("gold", {})["XAU"] = {
                "price":     price,
                "timestamp": datetime.now().isoformat(),
            }

    memory["prices"] = prices


def watch_stocks(memory: dict, watchlist: dict, test_mode: bool = False):
    """
    Watch stocks via Alpha Vantage.
    Runs once daily at 9:30pm SGT (US market open). Zero LLM.
    Alpha Vantage: 25 calls/day budget.
    """
    prices = memory.setdefault("prices", {})
    stock_list = watchlist.get("stocks", [])

    if len(stock_list) > 20:
        print(f"[Watcher] WARNING: {len(stock_list)} stocks risks Alpha Vantage 25/day limit.")

    for symbol in stock_list:
        price = fetch_stock_price(symbol)
        if price is None:
            continue
        print(f"[Watcher] {symbol}: ${price:,.2f}")

        baseline_entry = prices.get("stocks", {}).get(symbol, {})
        baseline = baseline_entry.get("price")

        if baseline:
            change = calc_change(price, baseline)
            print(f"[Watcher] {symbol} change: {change:+.2f}%")
            if not test_mode and abs(change) >= ALERT_THRESHOLD:
                flag_alert(memory, "stocks", symbol, change, price)

        prices.setdefault("stocks", {})[symbol] = {
            "price":     price,
            "timestamp": datetime.now().isoformat(),
        }

    memory["prices"] = prices


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    test_mode   = "--test" in args
    stocks_only = "--stocks-only" in args

    print(f"[Watcher] Starting — mode: {'test' if test_mode else 'stocks-only' if stocks_only else 'crypto+gold'}")

    memory    = load_memory()
    watchlist = load_watchlist()

    if stocks_only:
        watch_stocks(memory, watchlist, test_mode)
    else:
        watch_crypto_and_gold(memory, watchlist, test_mode)

    if not test_mode:
        save_memory(memory)
        print("[Watcher] Memory updated.")
    else:
        print("[Watcher] Test mode — memory NOT written.")
        print(f"[Watcher] Current prices snapshot:\n{json.dumps(memory.get('prices', {}), indent=2)}")

    print("[Watcher] Done.")
