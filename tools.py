"""
Tool implementations and Gemini FunctionDeclarations for the Daily Update Agent.

Each tool is a plain Python function returning a string result.
TOOL_REGISTRY maps name -> function for the agent dispatcher.
TOOL_DECLARATIONS is the list passed to the Gemini model.

Token efficiency rules enforced here:
- Weather: zero LLM (pure HTTP + formatting)
- Prices: zero LLM (pure HTTP)
- Memory: zero LLM (pure JSON)
- Watchlist: zero LLM (pure JSON)
- Gmail: zero LLM (OAuth2 + HTTP)
- Telegram: zero LLM (HTTP)
"""

import os
import re
import json
import base64
import pickle
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google.genai import types as genai_types

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GOLD_API_KEY     = os.getenv("GOLD_API_KEY", "")
AV_API_KEY       = os.getenv("ALPHAVANTAGE_API_KEY", "")

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE      = os.path.join(BASE_DIR, "token.pickle")
MEMORY_FILE     = os.path.join(BASE_DIR, "memory.json")
WATCHLIST_FILE  = os.path.join(BASE_DIR, "watchlist.json")

TG_URL      = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
GOLD_URL    = "https://api.gold-api.com/price/XAU"
AV_URL      = "https://www.alphavantage.co/query"
URL_2H      = "https://api-open.data.gov.sg/v2/real-time/api/two-hr-forecast"
URL_24H     = "https://api-open.data.gov.sg/v2/real-time/api/twenty-four-hr-forecast"

MAX_EMAILS     = 15
MAX_BODY_CHARS = 300

# ── Injection defence ─────────────────────────────────────────────────────────

_INJECTION_REGEX = re.compile(
    r'ignore\s+(all\s+)?(previous|prior|above)\s+instructions?'
    r'|disregard\s+(all\s+)?(previous|prior|above)\s+instructions?'
    r'|you\s+are\s+now\s+a'
    r'|act\s+as\s+(a\s+)?(?:different|new|another)'
    r'|new\s+instructions?:'
    r'|system\s*:'
    r'|<\s*system\s*>'
    r'|\[system\]'
    r'|override\s+(previous\s+)?instructions?'
    r'|send\s+(all\s+)?(emails?|data|information)\s+to'
    r'|forward\s+(all\s+)?(emails?|data)\s+to',
    re.IGNORECASE
)


def _sanitize(text: str) -> str:
    if not text:
        return text
    return _INJECTION_REGEX.sub('[redacted]', text)


# ── Weather ───────────────────────────────────────────────────────────────────

def get_weather() -> str:
    """Fetch Singapore NEA weather. Zero LLM calls."""
    lines = []
    try:
        r = requests.get(URL_2H, timeout=10)
        r.raise_for_status()
        items = r.json().get("data", {}).get("items", [])
        if items:
            forecasts = items[0].get("forecasts", [])
            # pick central Singapore or first area
            for entry in forecasts:
                area = entry.get("area", "").lower()
                if "singapore" in area or "city" in area or area == "central":
                    val = entry.get("forecast", "N/A")
                    if isinstance(val, dict):
                        val = val.get("text", "N/A")
                    lines.append(f"Now (2h): {val}")
                    break
            if not lines and forecasts:
                val = forecasts[0].get("forecast", "N/A")
                if isinstance(val, dict):
                    val = val.get("text", "N/A")
                lines.append(f"Now (2h): {val}")
    except Exception as e:
        lines.append(f"2h forecast unavailable: {e}")

    try:
        r = requests.get(URL_24H, timeout=10)
        r.raise_for_status()
        records = r.json().get("data", {}).get("records", [])
        if records:
            general  = records[0].get("general", {})
            temp     = general.get("temperature", {})
            humidity = general.get("relativeHumidity", {})
            forecast = general.get("forecast", {})
            if isinstance(forecast, dict):
                forecast = forecast.get("text", "N/A")
            wind     = general.get("wind", {})
            wind_spd = wind.get("speed", {})
            lines.append(f"Temp: {temp.get('low','?')}–{temp.get('high','?')}°C | "
                         f"Humidity: {humidity.get('low','?')}–{humidity.get('high','?')}%")
            lines.append(f"Outlook: {forecast}")
    except Exception as e:
        lines.append(f"24h forecast unavailable: {e}")

    # Check for rain warning
    rain_keywords = ("rain", "shower", "thunderstorm", "thunder")
    full_text = " ".join(lines).lower()
    if any(k in full_text for k in rain_keywords):
        lines.append("☔ Rain likely — bring an umbrella.")

    return "\n".join(lines) if lines else "Weather data unavailable."


# ── Watchlist ─────────────────────────────────────────────────────────────────

def get_watchlist() -> str:
    """Return current watchlist as JSON string."""
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Watchlist error: {e}"


def add_watchlist_item(asset_type: str, symbol: str) -> str:
    """Add an asset to watchlist.json. asset_type: 'crypto' or 'stocks'."""
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        symbol = symbol.upper()
        if asset_type == "gold":
            data["gold"] = True
            msg = "Gold tracking enabled."
        elif asset_type in ("crypto", "stocks"):
            if symbol not in data.get(asset_type, []):
                data.setdefault(asset_type, []).append(symbol)
                msg = f"Added {symbol} to {asset_type} watchlist."
            else:
                msg = f"{symbol} is already in {asset_type} watchlist."
        else:
            return f"Unknown asset type '{asset_type}'. Use 'crypto', 'stocks', or 'gold'."
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return msg
    except Exception as e:
        return f"Watchlist update error: {e}"


def remove_watchlist_item(asset_type: str, symbol: str) -> str:
    """Remove an asset from watchlist.json. asset_type: 'crypto' or 'stocks'."""
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        symbol = symbol.upper()
        if asset_type == "gold":
            data["gold"] = False
            msg = "Gold tracking disabled."
        elif asset_type in ("crypto", "stocks"):
            lst = data.get(asset_type, [])
            if symbol in lst:
                lst.remove(symbol)
                data[asset_type] = lst
                msg = f"Removed {symbol} from {asset_type} watchlist."
            else:
                msg = f"{symbol} not found in {asset_type} watchlist."
        else:
            return f"Unknown asset type '{asset_type}'. Use 'crypto', 'stocks', or 'gold'."
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return msg
    except Exception as e:
        return f"Watchlist remove error: {e}"


# ── Memory ────────────────────────────────────────────────────────────────────

def read_memory() -> str:
    """Read memory.json — persistent state from last run. Zero LLM."""
    try:
        with open(MEMORY_FILE) as f:
            return json.dumps(json.load(f), indent=2)
    except FileNotFoundError:
        return json.dumps({"last_run": None, "prices": {}, "alert_flags": []}, indent=2)
    except Exception as e:
        return f"Memory read error: {e}"


def write_memory(data: str) -> str:
    """Write full memory.json from a JSON string. Zero LLM. Call after briefing."""
    try:
        parsed = json.loads(data)
        parsed["last_updated"] = datetime.now().isoformat()
        with open(MEMORY_FILE, "w") as f:
            json.dump(parsed, f, indent=2)
        return "Memory updated."
    except Exception as e:
        return f"Memory write error: {e}"


# ── Prices ────────────────────────────────────────────────────────────────────

def get_crypto_price(symbol: str) -> str:
    """Fetch live crypto price from Binance public API. Zero LLM. symbol e.g. 'BTC'."""
    symbol = symbol.upper()
    pair = f"{symbol}USDT"
    try:
        r = requests.get(BINANCE_URL, params={"symbol": pair}, timeout=10)
        r.raise_for_status()
        price = float(r.json().get("price", 0))
        if not price:
            return f"{symbol}: price not found"
        return f"{symbol}: ${price:,.2f} USD"
    except Exception as e:
        return f"{symbol}: unavailable ({e})"


def get_gold_price() -> str:
    """Fetch XAU gold price from gold-api.com. Zero LLM."""
    try:
        headers = {"x-access-token": GOLD_API_KEY} if GOLD_API_KEY else {}
        r = requests.get(GOLD_URL, headers=headers, timeout=10)
        r.raise_for_status()
        price = float(r.json().get("price", 0))
        if not price:
            return "Gold: price not found"
        return f"XAU: ${price:,.2f} USD/oz"
    except Exception as e:
        return f"Gold: unavailable ({e})"


def get_stock_price(symbol: str) -> str:
    """Fetch stock price from Alpha Vantage. Zero LLM. Counts against 25 calls/day budget."""
    symbol = symbol.upper()
    if not AV_API_KEY:
        return f"{symbol}: ALPHAVANTAGE_API_KEY not set"
    try:
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol":   symbol,
            "apikey":   AV_API_KEY,
        }
        r = requests.get(AV_URL, params=params, timeout=15)
        r.raise_for_status()
        quote = r.json().get("Global Quote", {})
        price = float(quote.get("05. price", 0))
        change_pct = quote.get("10. change percent", "0%").replace("%", "")
        if not price:
            return f"{symbol}: price not found (check symbol or API key)"
        return f"{symbol}: ${price:,.2f} USD ({float(change_pct):+.2f}% today)"
    except Exception as e:
        return f"{symbol}: unavailable ({e})"


# ── Gmail ─────────────────────────────────────────────────────────────────────

def _get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)


def _clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _get_email_body(payload: dict) -> str:
    body = ""
    if payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
    elif payload.get("parts"):
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                break
            elif part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
    return _clean_text(body)[:MAX_BODY_CHARS]


def get_emails() -> str:
    """
    Fetch unread emails from last 24 hours via Gmail API.
    Returns sanitized email data ready for classification. Zero LLM.
    """
    service = _get_gmail_service()
    if not service:
        return "Gmail not connected — token.pickle missing or expired."

    try:
        after_ts = int((datetime.now() - timedelta(hours=24)).timestamp())
        query    = f"is:unread after:{after_ts}"
        results  = service.users().messages().list(
            userId="me", q=query, maxResults=MAX_EMAILS
        ).execute()
        messages = results.get("messages", [])

        if not messages:
            return "No unread emails in last 24 hours."

        lines = [f"[EMAIL_BATCH: {len(messages)} unread emails, last 24h]"]
        for i, msg in enumerate(messages, 1):
            msg_data = service.users().messages().get(
                userId="me", id=msg["id"], format="full"
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in msg_data.get("payload", {}).get("headers", [])
            }
            sender  = _sanitize(headers.get("From", "Unknown"))[:80]
            subject = _sanitize(headers.get("Subject", "(No subject)"))[:100]
            body    = _sanitize(_get_email_body(msg_data.get("payload", {})))
            lines.append(
                f"[EMAIL_{i}]\nID: {msg['id']}\nFrom: {sender}\nSubject: {subject}\nBody: {body}\n[/EMAIL_{i}]"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Gmail error: {e}"


def draft_gmail_reply(message_id: str, to: str, subject: str, body: str) -> str:
    """
    Save a Gmail draft reply to an email. Does NOT send — user reviews first.
    message_id: Gmail message ID from get_emails (the ID: field)
    to: recipient email address
    subject: subject line e.g. 'Re: Original Subject'
    body: plain text draft body
    """
    service = _get_gmail_service()
    if not service:
        return "Gmail not connected — token.pickle missing or expired."
    try:
        original = service.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["Message-ID"]
        ).execute()
        thread_id = original.get("threadId")
        orig_headers = {
            h["name"]: h["value"]
            for h in original.get("payload", {}).get("headers", [])
        }
        original_msg_id = orig_headers.get("Message-ID", "")

        mime_msg = MIMEText(body, "plain")
        mime_msg["To"]      = to
        mime_msg["Subject"] = subject
        if original_msg_id:
            mime_msg["In-Reply-To"] = original_msg_id
            mime_msg["References"]  = original_msg_id

        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
        draft_body: dict = {"message": {"raw": raw}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        service.users().drafts().create(userId="me", body=draft_body).execute()
        return f"Draft saved — To: {to} | Subject: {subject}. Review in Gmail before sending."
    except Exception as e:
        return f"Draft error: {e}"


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> str:
    """Send message to Telegram. Use Telegram Markdown: *bold*, _italic_, `code`."""
    try:
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }
        r = requests.post(TG_URL, json=payload, timeout=10)
        if r.status_code == 400:
            payload["parse_mode"] = ""
            r = requests.post(TG_URL, json=payload, timeout=10)
        r.raise_for_status()
        return "Message sent to Telegram. Do not call send_telegram again."
    except Exception as e:
        return f"Telegram error: {e}"


# ── Tool registry & Gemini declarations ───────────────────────────────────────

TOOL_REGISTRY: dict = {
    "get_weather":          get_weather,
    "get_crypto_price":     get_crypto_price,
    "get_gold_price":       get_gold_price,
    "get_stock_price":      get_stock_price,
    "get_emails":           get_emails,
    "draft_gmail_reply":    draft_gmail_reply,
    "send_telegram":        send_telegram,
    "read_memory":          read_memory,
    "write_memory":         write_memory,
    "get_watchlist":        get_watchlist,
    "add_watchlist_item":   add_watchlist_item,
    "remove_watchlist_item": remove_watchlist_item,
}

_NO_PARAMS = genai_types.Schema(type=genai_types.Type.OBJECT, properties={})

TOOL_DECLARATIONS: list[genai_types.FunctionDeclaration] = [
    genai_types.FunctionDeclaration(
        name="get_weather",
        description=(
            "Fetch current Singapore weather from NEA API. "
            "Returns 2-hour area forecast and 24-hour outlook. Zero LLM — always call this."
        ),
        parameters=_NO_PARAMS,
    ),
    genai_types.FunctionDeclaration(
        name="get_crypto_price",
        description=(
            "Fetch live crypto price from Binance public API. Zero LLM. "
            "Call for each crypto in the watchlist."
        ),
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "symbol": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Crypto symbol e.g. 'BTC', 'ETH', 'ADA'."
                ),
            },
            required=["symbol"],
        ),
    ),
    genai_types.FunctionDeclaration(
        name="get_gold_price",
        description=(
            "Fetch current XAU gold price from gold-api.com. Zero LLM. "
            "Call only if gold is enabled in watchlist."
        ),
        parameters=_NO_PARAMS,
    ),
    genai_types.FunctionDeclaration(
        name="get_stock_price",
        description=(
            "Fetch stock price from Alpha Vantage. Zero LLM. "
            "Budget: 25 calls/day max. Call only for stocks in watchlist."
        ),
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "symbol": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Stock ticker symbol e.g. 'DELL', 'MSFT', 'AAPL'."
                ),
            },
            required=["symbol"],
        ),
    ),
    genai_types.FunctionDeclaration(
        name="get_emails",
        description=(
            "Fetch unread Gmail from last 24 hours. Returns sanitized email data. "
            "Call once — classify all emails in a single reasoning step."
        ),
        parameters=_NO_PARAMS,
    ),
    genai_types.FunctionDeclaration(
        name="draft_gmail_reply",
        description=(
            "Save a Gmail draft reply for an urgent email. Does NOT send — user reviews first. "
            "Call for each email classified as 🔴 ACTION. Use the ID field from get_emails output."
        ),
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "message_id": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Gmail message ID from the ID: field in get_emails output."
                ),
                "to": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Recipient email address (from the From: field of the original email)."
                ),
                "subject": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Reply subject, e.g. 'Re: Original Subject'."
                ),
                "body": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Plain text draft reply. Keep it concise — user will edit before sending."
                ),
            },
            required=["message_id", "to", "subject", "body"],
        ),
    ),
    genai_types.FunctionDeclaration(
        name="send_telegram",
        description=(
            "Send the complete formatted briefing or command response to Telegram. "
            "Telegram Markdown: *bold*, _italic_, `code`. "
            "Call ONCE — do not split into multiple messages."
        ),
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "message": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Complete formatted message in Telegram Markdown."
                ),
            },
            required=["message"],
        ),
    ),
    genai_types.FunctionDeclaration(
        name="read_memory",
        description=(
            "Read persistent state from memory.json — includes last prices, "
            "alert_flags set by price_watcher, and last run timestamp. Call first."
        ),
        parameters=_NO_PARAMS,
    ),
    genai_types.FunctionDeclaration(
        name="write_memory",
        description=(
            "Write updated memory.json as a JSON string. "
            "Call after sending the briefing. Include updated prices, cleared flags, and last_run."
        ),
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "data": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description=(
                        "Full memory JSON string. Must include: last_run (ISO date), "
                        "prices (dict of asset prices), alert_flags (empty list after reporting)."
                    )
                ),
            },
            required=["data"],
        ),
    ),
    genai_types.FunctionDeclaration(
        name="get_watchlist",
        description="Return current tracked assets from watchlist.json.",
        parameters=_NO_PARAMS,
    ),
    genai_types.FunctionDeclaration(
        name="add_watchlist_item",
        description="Add an asset to the watchlist. Confirm the change to the user.",
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "asset_type": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="'crypto', 'stocks', or 'gold'."
                ),
                "symbol": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Asset symbol e.g. 'ADA', 'MSFT'. Ignored for gold."
                ),
            },
            required=["asset_type", "symbol"],
        ),
    ),
    genai_types.FunctionDeclaration(
        name="remove_watchlist_item",
        description="Remove an asset from the watchlist. Confirm the change to the user.",
        parameters=genai_types.Schema(
            type=genai_types.Type.OBJECT,
            properties={
                "asset_type": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="'crypto', 'stocks', or 'gold'."
                ),
                "symbol": genai_types.Schema(
                    type=genai_types.Type.STRING,
                    description="Asset symbol to remove. Ignored for gold."
                ),
            },
            required=["asset_type", "symbol"],
        ),
    ),
]
