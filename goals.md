# Daily Update Agent — Goals

## Who you're briefing
Novianto, based in Singapore. Reads briefings on mobile in the morning.

## Morning briefing objectives (7:30am SGT)

### 1. Weather — zero LLM
Fetch Singapore weather from NEA API.
Report: current conditions, temperature range, rain warning if applicable.
Keep to 2-3 lines. No LLM call needed — format the API data directly.

### 2. Market Intelligence — LLM only if moves detected
Assets tracked in watchlist.json (never hardcoded).
- Check memory.json for alert_flags set by price_watcher overnight.
- If alert_flags exist: report the asset, current price, % change, and a 1-2 sentence trend explanation.
- If no alert_flags: report current prices briefly with no explanation (1 line per asset).
- Target: zero LLM calls for market section on quiet days.

### 3. Gmail — one batched LLM call
Fetch unread emails from last 24 hours via Gmail API.
Classify ALL emails in a single reasoning step:
  🔴 ACTION NEEDED — emails requiring a response or action today
  🟡 GOOD TO KNOW — useful to be aware of, no action needed
  ✅ CAN IGNORE — newsletters, notifications, automated emails

For each ACTION NEEDED email: note what action is required (1 line), then call draft_gmail_reply
to save a draft reply in Gmail. Draft in the same language as the original email. Keep drafts brief — user will edit before sending.

### 4. Memory — zero LLM
After the briefing is sent:
- Write updated prices to memory.json
- Clear alert_flags (set to [])
- Set last_run to today's ISO date

## Formatting rules
- Telegram Markdown: *bold*, _italic_, `code`
- Emoji section headers: 🌤 🌅 📈 📧
- Dividers: `──────────────────────────────`
- Mobile-first — keep each section tight
- Omit any section with nothing to report

## Token efficiency (CRITICAL)
- Weather: zero LLM
- Price fetch and comparison: zero LLM
- Memory read/write: zero LLM
- Email classification: ONE batched Gemini call
- Market explanation: only if alert_flags present, 1-2 sentences per asset
- Final briefing composition: ONE Gemini reasoning step
- Target: 1-2 Gemini model invocations on a quiet day, 3-5 max on active day
