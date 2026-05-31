# إرم نيوز — Eram News Monitor

A production-grade async news monitor for [eremnews.com/sports](https://www.eremnews.com/sports),
built following the same architecture as the FilGoal monitor.

## Architecture

```
EremNewsScraper (Playwright)
    └── eremnews_parser.py      HTML parsing (Newspaper WP theme)
ArticleClassifier               UAE / Arab / Global keyword + OpenAI
ArticleRepository               Supabase persistence
DeduplicationCache              Redis fast-path dedup
TelegramSender                  aiogram Telegram notifications
Watchdog                        24/7 supervisor with crash recovery
```

## Scraping Strategy

Eram News uses the **Newspaper WordPress theme** with Cloudflare protection.
Playwright (headless Chromium) is used to render the page before parsing.

**Listing page selectors:**
- `article.post`, `article.td_module_wrap` — article cards
- `h3.entry-title a` — title + URL
- `time.entry-date[datetime]` — publish date
- `img.entry-thumb` — thumbnail image

**Detail page selectors:**
- `div.td-post-content` — article body (primary)
- `div.entry-content` — fallback
- `h1.entry-title` — title
- `time.entry-date` — date
- `div.td-post-featured-image img` — hero image
- `og:image` meta — image fallback

## Setup

```bash
# 1. Copy and fill in environment variables
cp .env.example .env
nano .env

# 2. Run the migration on your Supabase project
# Paste migrations/001_initial_schema.sql into the Supabase SQL editor

# 3. Build and start
docker compose up -d --build

# 4. Monitor logs
docker compose logs -f monitor
```

## Environment Variables

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key |
| `TELEGRAM_BOT_TOKEN_EREMNEWS` | Telegram bot token |
| `TELEGRAM_CHAT_ID_EREMNEWS` | Target Telegram chat/channel ID |
| `REDIS_URL` | Redis connection string |
| `POLL_INTERVAL_SECONDS` | How often to check for new articles (default: 60) |
| `USE_OPENAI` | Enable OpenAI classifier fallback (default: false) |
| `OPENAI_API_KEY` | OpenAI API key (if USE_OPENAI=true) |

## Services

| Service | Port | Description |
|---|---|---|
| `monitor` | — | Main scraper process |
| `admin-api` | 8080 | FastAPI admin interface |
| `redis` | — | Deduplication cache |

### Admin API Endpoints

- `GET /health` — Health probe
- `GET /articles/recent?limit=20` — Recent articles
- `GET /articles/by-classification/{type}` — Filter by UAE/Arab/Global
- `GET /stats` — Classification statistics
