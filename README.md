# AI news digest

Fetches AI/tech news from a handful of RSS feeds, has a free-tier LLM
(Gemini) pick and summarize the most significant stories, and sends
you a digest twice a day — via Telegram or email. Runs entirely on
GitHub Actions' free tier, so there's no server to host or pay for.

## How it works

1. `digest.py` pulls recent articles from the feeds listed at the top
   of the file (TechCrunch AI, The Verge, Ars Technica, MIT Tech
   Review, VentureBeat AI, Hacker News, arXiv cs.AI, Wired AI).
2. It drops anything you've already been sent (tracked in `seen.json`,
   which the workflow commits back to the repo each run) and anything
   older than ~14 hours.
3. The remaining articles go to Gemini's free-tier API, which picks
   the ~10 best stories and writes a short summary of each.
4. The digest is sent to you via Telegram or email.
5. `.github/workflows/news-digest.yml` runs this on a schedule — by
   default 8am and 8pm UTC — for free.

## Setup

### 1. Create the repo

Create a new GitHub repository and add these files, keeping the
folder structure intact (the workflow file must stay at
`.github/workflows/news-digest.yml`). Public repos get unlimited free
Actions minutes; private repos get a generous free monthly quota too.

### 2. Get a free Gemini API key

Go to [Google AI Studio](https://aistudio.google.com/), sign in, and
create an API key. No credit card required. Free-tier rate limits
change from time to time — check the current numbers on the API key
page if you're curious, but at two runs a day you'll never get close.

### 3. Set up delivery

**Telegram (simplest):**
1. Message [@BotFather](https://t.me/BotFather) on Telegram, send
   `/newbot`, and follow the prompts. It'll give you a bot token.
2. Send your new bot any message (so it can find your chat).
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a
   browser and find the `"chat":{"id": ...}` value — that's your chat ID.

**Email (alternative):**
1. Turn on 2-factor authentication on the Gmail account you want to
   send from.
2. Generate an [app password](https://myaccount.google.com/apppasswords).
3. In `.github/workflows/news-digest.yml`, change `DELIVERY_METHOD`
   from `telegram` to `email`.

### 4. Add secrets

In your repo: **Settings → Secrets and variables → Actions → New
repository secret.** Add:

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (if using Telegram)
- `EMAIL_FROM`, `EMAIL_TO`, `EMAIL_APP_PASSWORD` (if using email)

### 5. Test it

Go to the **Actions** tab → **AI News Digest** → **Run workflow** to
trigger it manually before waiting for the schedule.

## Adjusting things

- **Schedule**: edit the two `cron` lines in the workflow file. GitHub
  Actions schedules are always UTC — convert your preferred local
  times manually, and expect runs to sometimes fire a few minutes late
  during peak periods.
- **Sources**: edit the `FEEDS` list in `digest.py` — add or remove
  any RSS feed URL.
- **How many stories**: change `MAX_STORIES` in `digest.py`.
- **Lookback window**: change `LOOKBACK_HOURS` if you shift the
  schedule times and need the window to match.

## Notes

- Google's free tier terms allow your prompts to be used to improve
  their models, so don't route anything sensitive through this.
- If the Gemini call fails for a run, the script falls back to sending
  raw headlines instead of skipping delivery entirely.
- Total cost: $0, as long as you stay within GitHub Actions' and
  Gemini's free tiers.
