# MES Alert Monitor

Free, self-updating support/resistance alert system for /MES futures.

## How it works
- `daily_levels.yml` runs once a day, calls `calculate_levels.py`, which pulls 3 months of
  daily bars, finds swing highs/lows, cross-checks them against a volume profile, and
  writes the confirmed support/resistance to `levels.json`. No manual number entry, ever.
- `check_price.yml` runs every 15 minutes, calls `check_mes_price.py`, which reads
  `levels.json`, checks the live (delayed ~15min) price, and sends a Telegram alert
  the first time price touches support or resistance (won't spam on every run while
  sitting at the level).

## Setup required (one-time)
Repo secrets needed:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Data source
Free Yahoo Finance chart endpoint (`MES=F`), delayed ~15 minutes. No subscription cost.

## To hand-edit levels
Just edit `levels.json` directly and commit — the daily job will overwrite it again
at the next scheduled run, so treat manual edits as temporary overrides.

## To add more symbols later
Duplicate the pattern with a symbols list — not yet built, intentionally kept to
MES only for now.
