"""
ReAct agentic loop for Daily Update Agent using Gemini 2.5 Flash native tool use.

Modes:
  - briefing: full morning briefing (called by daily_briefing.py)
  - command:  handle user Telegram command (called with message arg)

Token efficiency:
  - Gemini only called for: reasoning, email classification, market explanation, briefing composition
  - Target: 1-2 Gemini calls on quiet day, 3-5 on active day with market moves
  - All data fetching and math is pure Python (zero LLM)
"""

import os
import sys
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

from tools import TOOL_REGISTRY, TOOL_DECLARATIONS

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL   = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-2.0-flash"
MAX_TURNS      = 20
MAX_RETRIES    = 3

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
GOALS_FILE  = os.path.join(BASE_DIR, "goals.md")

_client = genai.Client(api_key=GOOGLE_API_KEY)


def _load_goals() -> str:
    if os.path.exists(GOALS_FILE):
        with open(GOALS_FILE) as f:
            return f.read()
    return "Deliver a daily briefing covering weather, markets, and email."


def _build_briefing_system() -> str:
    goals = _load_goals()
    today = datetime.now().strftime("%A, %d %B %Y, %H:%M SGT")
    return f"""You are a token-efficient daily briefing agent for Novianto in Singapore.

## Goals
{goals}

## Today
{today}

## Execution plan (follow this order, be efficient)
1. Call read_memory AND get_watchlist in the SAME turn (parallel).
2. Check alert_flags in memory:
   - If alert_flags is non-empty: fetch current price for flagged assets and include trend explanation in briefing.
   - If alert_flags is empty: skip market explanation — just report prices briefly.
3. Call get_weather AND get_emails in the SAME turn (parallel).
4. For market prices: call get_crypto_price / get_gold_price / get_stock_price ONLY for assets in the watchlist.
   Call them all in ONE turn (parallel), but ONLY if there are alert_flags or this is morning briefing.
5. For each email classified as 🔴 ACTION (urgent, needs reply): call draft_gmail_reply with a concise
   professional reply draft. Use the ID: field from get_emails. Reply in the same language as the original.
   Draft in the same turn as other actions if possible. Do NOT draft for 🟡 INFO or ✅ SKIP emails.
6. Compose the complete briefing and call send_telegram ONCE.
7. Call write_memory with updated prices, cleared alert_flags, and last_run timestamp.

## Token efficiency rules (CRITICAL)
- Market explanation: ONLY if alert_flags exist in memory. 1-2 sentences max per asset.
- Email classification: classify ALL emails in a single reasoning step — do NOT call get_emails more than once.
- Final briefing: ONE send_telegram call with complete message.
- Never call any tool more than once per run.
- Do NOT call get_stock_price during morning briefing if stocks have no alert flags — use price from memory instead.

## Briefing format (Telegram Markdown)
*🌅 DAILY BRIEFING — {today}*
──────────────────────────────
*🌤 WEATHER*
[2-3 lines]
──────────────────────────────
*📈 MARKETS* _(only flagged moves, or brief summary if quiet)_
[per asset: current price + % change. If alert: 1-2 sentence trend note.]
──────────────────────────────
*📧 EMAIL*
🔴 ACTION: [urgent emails] _(draft reply saved in Gmail)_
🟡 INFO: [notable emails]
✅ SKIP: [count]
──────────────────────────────
[Optional: short closing note if something is genuinely notable today]

Keep it mobile-friendly. Omit any section with nothing to report."""


def _build_command_system() -> str:
    today = datetime.now().strftime("%A, %d %B %Y, %H:%M SGT")
    return f"""You are an assistant managing the Daily Update Agent for Novianto.
Today: {today}

Handle these Telegram commands:
- "add crypto SYMBOL"     → call add_watchlist_item(asset_type="crypto", symbol=SYMBOL)
- "remove crypto SYMBOL"  → call remove_watchlist_item(asset_type="crypto", symbol=SYMBOL)
- "add stock SYMBOL"      → call add_watchlist_item(asset_type="stocks", symbol=SYMBOL)
- "remove stock SYMBOL"   → call remove_watchlist_item(asset_type="stocks", symbol=SYMBOL)
- "add gold"              → call add_watchlist_item(asset_type="gold", symbol="XAU")
- "remove gold"           → call remove_watchlist_item(asset_type="gold", symbol="XAU")
- "show watchlist"        → call get_watchlist()
- "run briefing"          → tell user to run: python daily_briefing.py
- "help"                  → send help text listing all commands

After any watchlist change: call send_telegram to confirm the update to the user.
Keep responses brief — 1-3 lines."""


def _generate_with_retry(model: str, contents, config) -> tuple:
    """Call generate_content with retries on transient errors. Returns (response, model_used)."""
    for attempt in range(MAX_RETRIES):
        try:
            response = _client.models.generate_content(
                model=model, contents=contents, config=config
            )
            return response, model
        except genai_errors.ServerError:
            wait = 10 * (attempt + 1)
            print(f"[Agent] 503 on {model} (attempt {attempt+1}) — waiting {wait}s...")
            time.sleep(wait)
            if attempt == MAX_RETRIES - 1 and model != FALLBACK_MODEL:
                print(f"[Agent] Falling back to {FALLBACK_MODEL}")
                model = FALLBACK_MODEL
        except genai_errors.ClientError as e:
            if "429" in str(e):
                wait = 15 * (attempt + 1)
                print(f"[Agent] Rate limited (attempt {attempt+1}) — waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    response = _client.models.generate_content(
        model=model, contents=contents, config=config
    )
    return response, model


def _run_loop(system: str, initial_message: str) -> str | None:
    """Core ReAct loop. Returns final text response or None."""
    tool_config  = genai_types.Tool(function_declarations=TOOL_DECLARATIONS)
    contents: list[genai_types.Content] = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=initial_message)]
        )
    ]

    active_model = GEMINI_MODEL
    for turn in range(MAX_TURNS):
        print(f"[Agent] Turn {turn + 1}...")

        response, active_model = _generate_with_retry(
            model=active_model,
            contents=contents,
            config=genai_types.GenerateContentConfig(
                system_instruction=system,
                tools=[tool_config],
                temperature=0.2,
            )
        )

        candidate    = response.candidates[0]
        model_content = candidate.content
        contents.append(model_content)

        function_calls = [p for p in model_content.parts if p.function_call]

        if not function_calls:
            final_text = response.text or ""
            print(f"[Agent] Done after {turn + 1} turn(s).")
            return final_text

        tool_response_parts: list[genai_types.Part] = []
        for part in function_calls:
            fc   = part.function_call
            name = fc.name
            args = dict(fc.args) if fc.args else {}

            preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            print(f"[Agent]   → {name}({preview})")

            fn = TOOL_REGISTRY.get(name)
            if fn:
                try:
                    result = fn(**args)
                except Exception as e:
                    result = f"Tool error in {name}: {e}"
            else:
                result = f"Unknown tool: {name}"

            print(f"[Agent]   ← {str(result)[:80].replace(chr(10), ' ')}...")

            tool_response_parts.append(
                genai_types.Part.from_function_response(
                    name=name,
                    response={"output": result},
                )
            )

        contents.append(
            genai_types.Content(role="tool", parts=tool_response_parts)
        )

    print(f"[Agent] Reached max turns ({MAX_TURNS}).")
    return None


def run_briefing() -> str | None:
    """Run the morning briefing."""
    now = datetime.now().strftime("%H:%M")
    print(f"[Agent] Morning briefing at {now} — model: {GEMINI_MODEL}")
    system  = _build_briefing_system()
    message = (
        "Run the daily briefing now. "
        "Check memory for overnight alert flags, fetch today's data, "
        "compose the briefing, send to Telegram, and update memory."
    )
    return _run_loop(system, message)


def run_command(user_message: str) -> str | None:
    """Handle a Telegram user command."""
    print(f"[Agent] Command: {user_message}")
    system = _build_command_system()
    return _run_loop(system, user_message)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = " ".join(sys.argv[1:])
        run_command(cmd)
    else:
        run_briefing()
