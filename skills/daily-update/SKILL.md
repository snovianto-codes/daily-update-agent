# Daily Update Agent Skill

Sends a token-efficient daily briefing to Telegram at 7:30am SGT, monitors prices every 15 minutes, and handles watchlist commands from Telegram.

## IMPORTANT: OpenClaw must be started manually

OpenClaw does NOT auto-start on Mac boot. Before any cron jobs will run, you must start it:

```bash
openclaw gateway start
```

Run this whenever you restart your Mac or OpenClaw has stopped.

---

## What this skill does

### Morning Briefing (7:30am SGT)
Runs `daily_briefing.py` which calls the Gemini 2.5 Flash agentic loop:

1. Read memory.json — check overnight alert_flags and last prices
2. Fetch Singapore weather from NEA API (zero LLM)
3. Fetch prices for assets in watchlist.json (zero LLM)
4. Fetch unread Gmail from last 24 hours (zero LLM)
5. Compose briefing with Gemini — market explanation only if alert_flags exist
6. Send to Telegram
7. Write updated prices and cleared flags to memory.json

Token target: 1-2 Gemini calls quiet day, 3-5 active day.

### Price Watcher (every 15 min)
Runs `price_watcher.py` — zero LLM, pure Python math:
- Checks crypto (Binance) and gold (gold-api.com)
- If price moves >= 2%: writes flag to memory.json + sends immediate Telegram alert

### Stock Watcher (9:30pm SGT, weekdays)
Runs `price_watcher.py --stocks-only` — zero LLM:
- Checks stocks via Alpha Vantage (25 calls/day budget)
- Alerts if >= 2% move since last check

---

## Telegram watchlist commands

| Command | Action |
|---------|--------|
| `add crypto ADA` | Add ADA to crypto watchlist |
| `remove crypto ADA` | Remove ADA from crypto watchlist |
| `add stock MSFT` | Add MSFT to stock watchlist |
| `remove stock MSFT` | Remove MSFT from stock watchlist |
| `add gold` | Enable gold tracking |
| `remove gold` | Disable gold tracking |
| `show watchlist` | Display current tracked assets |
| `run briefing` | Instructions to trigger manual briefing |
| `help` | List all commands |

When user sends one of these, call:
```bash
cd /Users/novianto/Documents/Python/OpenClaw/daily-update-agent && python agent.py "<user message>"
```

---

## Manual triggers

### Run briefing immediately
```bash
cd /Users/novianto/Documents/Python/OpenClaw/daily-update-agent && python daily_briefing.py
```

### Run price watcher manually
```bash
cd /Users/novianto/Documents/Python/OpenClaw/daily-update-agent && python price_watcher.py --test
```

### Handle Telegram command
```bash
cd /Users/novianto/Documents/Python/OpenClaw/daily-update-agent && python agent.py "show watchlist"
```

---

## Context file locations

| File | Purpose |
|------|---------|
| `memory.json` | Prices, alert_flags, last run timestamp |
| `watchlist.json` | Tracked assets (crypto, stocks, gold) |
| `goals.md` | Agent objectives loaded at startup |
| `.env` | API keys |
| `briefing.log` | Run log |
| `briefing_error.log` | Error log |

---

## Session

Runs in `isolated` session — each cron job is independent. No persistent session context.

## Script locations

```
/Users/novianto/Documents/Python/OpenClaw/daily-update-agent/daily_briefing.py
/Users/novianto/Documents/Python/OpenClaw/daily-update-agent/price_watcher.py
/Users/novianto/Documents/Python/OpenClaw/daily-update-agent/agent.py
```
