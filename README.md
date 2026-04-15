# Daily Update Agentic AI

A token-efficient daily briefing agent built on [OpenClaw](https://openclaw.ai). Delivers a morning briefing via Telegram, monitors asset prices every 15 minutes, and auto-drafts Gmail replies for urgent emails — all powered by Gemini 2.5 Flash native function calling.

> Part of the OpenClaw portfolio: [github.com/snovianto-codes](https://github.com/snovianto-codes)

---

## Features

- **Morning briefing** at 7:30am (Singapore time) — weather, markets, email summary
- **Price watcher** every 15 min — instant Telegram alert if crypto/gold moves ≥2%
- **Stock watcher** at market open (weekdays) — alerts on ≥2% stock moves
- **Gmail draft replies** — automatically saves a draft reply for urgent emails
- **Watchlist-driven** — manage tracked assets via Telegram commands, no code changes
- **Token-efficient** — zero LLM calls for data fetching; 1–2 Gemini calls on quiet days

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  OpenClaw (cron scheduler)               │
└───────────────┬─────────────────────────┬───────────────┘
                │                         │
    ┌───────────▼──────────┐   ┌──────────▼──────────────┐
    │  daily_briefing.py   │   │   price_watcher.py       │
    │  (7:30am SGT cron)   │   │  (every 15min + stocks)  │
    └───────────┬──────────┘   └──────────┬───────────────┘
                │                         │ zero LLM
    ┌───────────▼──────────┐   ┌──────────▼───────────────┐
    │      agent.py        │   │      memory.json          │
    │  Gemini 2.5 Flash    │◄──│  prices + alert_flags     │
    │  function calling    │   └──────────────────────────┘
    └───────────┬──────────┘
                │
    ┌───────────▼──────────────────────────────┐
    │                tools.py                  │
    │  NEA weather · Binance · Alpha Vantage   │
    │  Gmail (read + draft) · Telegram         │
    └───────────┬──────────────────────────────┘
                │
    ┌───────────▼──────────┐
    │       Telegram        │
    │  Morning briefing    │
    │  Price alerts        │
    └──────────────────────┘
```

### Token efficiency

| Operation | LLM calls |
|-----------|-----------|
| Weather fetch | 0 |
| Price fetch + comparison | 0 |
| Memory read/write | 0 |
| Email fetch | 0 |
| Email classification + draft | 1 (batched) |
| Market explanation | 1 (only if ≥2% move) |
| Briefing composition | 1 |
| **Quiet day total** | **1–2** |
| **Active day total** | **3–5 max** |

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/snovianto-codes/daily-update-agent.git
cd daily-update-agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` with your keys:

| Key | Where to get it |
|-----|-----------------|
| `GOOGLE_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| `GOLD_API_KEY` | [gold-api.com](https://gold-api.com) |
| `ALPHAVANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co) — free tier, 25 calls/day |
| `TELEGRAM_TOKEN` | @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | Message your bot, then call `getUpdates` |

### 4. Set up Gmail OAuth

You need a `credentials.json` from Google Cloud Console (Gmail API, OAuth 2.0 desktop client), then run the auth flow once to generate `token.json`:

```bash
python -c "
from google_auth_oauthlib.flow import InstalledAppFlow
import json

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly',
          'https://www.googleapis.com/auth/gmail.compose']

flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=0)
with open('token.json', 'w') as f:
    f.write(creds.to_json())
print('token.json saved')
"
```

> If you have an existing `token.pickle` from a previous setup, the agent will automatically migrate it to `token.json` on first run and delete the old file.

### 5. Register cron jobs in OpenClaw

Open the OpenClaw dashboard and add these 3 jobs:

| Name | Cron | Timezone | Command |
|------|------|----------|---------|
| `daily-update-morning` | `30 7 * * *` | Asia/Singapore | `python /path/to/daily_briefing.py` |
| `price-watcher` | `*/15 * * * *` | Asia/Singapore | `python /path/to/price_watcher.py` |
| `stock-watcher` | `30 21 * * 1-5` | Asia/Singapore | `python /path/to/price_watcher.py --stocks-only` |

All jobs use **isolated** session mode.

---

## Watchlist management via Telegram

Message your bot directly:

| Command | Action |
|---------|--------|
| `add crypto ADA` | Track ADA |
| `remove crypto ADA` | Stop tracking ADA |
| `add stock MSFT` | Track MSFT |
| `remove stock MSFT` | Stop tracking MSFT |
| `add gold` | Enable gold tracking |
| `remove gold` | Disable gold tracking |
| `show watchlist` | Show all tracked assets |
| `run briefing` | Trigger immediate briefing |
| `help` | List all commands |

Default watchlist: **BTC, ETH** · **DELL** · **XAU (gold)**

---

## Gmail draft replies

When the morning briefing finds an email classified as `🔴 ACTION` (urgent, needs a reply), the agent automatically saves a draft reply in Gmail. The draft is never sent — you review and send it yourself.

The draft is composed in the same language as the original email and threaded correctly with the original message.

---

## Manual testing

```bash
# Test individual tools
python -c "from tools import get_weather; print(get_weather())"
python -c "from tools import get_crypto_price; print(get_crypto_price('BTC'))"
python -c "from tools import get_gold_price; print(get_gold_price())"
python -c "from tools import get_stock_price; print(get_stock_price('DELL'))"
python -c "from tools import read_memory; print(read_memory())"

# Test price watcher (no alerts, no memory write)
python price_watcher.py --test

# Run full briefing
python daily_briefing.py

# Test a Telegram command
python agent.py "show watchlist"
python agent.py "add crypto SOL"
```

---

## Project structure

```
daily-update-agent/
├── daily_briefing.py        # Entry point — called by OpenClaw cron
├── agent.py                 # Gemini 2.5 Flash ReAct agentic loop
├── tools.py                 # Tool functions + Gemini declarations (12 tools)
├── price_watcher.py         # Zero-LLM price monitor
├── run_daily_briefing.py    # Launcher: resolves path portably, calls daily_briefing.py
├── run_price_watcher.py     # Launcher: resolves path portably, calls price_watcher.py
├── run_stock_watcher.py     # Launcher: resolves path portably, calls price_watcher.py --stocks-only
├── goals.md                 # Agent objectives (loaded at runtime)
├── memory.json              # Persistent state — prices + alert_flags (gitignored)
├── watchlist.json           # Tracked assets
├── .env                     # API keys (gitignored)
├── .env.example             # Key template
├── requirements.txt
└── skills/
    └── daily-update/
        ├── SKILL.md         # OpenClaw skill definition
        └── cron.json        # Cron schedule reference
```

---

## Requirements

- Python 3.11+
- [OpenClaw](https://openclaw.ai) installed and running
- Gemini 2.5 Flash API key (Google AI Studio)
- Telegram bot token
- Gmail OAuth credentials

---

## Security

- **No credentials in source** — `.env`, `credentials.json`, and `token.json` are all gitignored
- **OAuth token stored as JSON** — uses `google.oauth2.credentials` instead of `pickle`, eliminating the pickle deserialisation RCE vector
- **Prompt injection defence** — all external string data (email subjects, bodies, weather forecasts) is passed through a regex filter before being handed to the LLM
- **Telegram token scoped to function** — the bot token is never materialised as a module-level string; it is only interpolated at call time inside `send_telegram()`

---

## License

MIT
