[中文](README.md) | [English](README.en.md)

# ISBN Normaliser

A standalone normaliser and MediaWiki bot for `{{ISBN|...}}` templates in wikitext on MoegirlPedia.

## Core Features

- **isbn_normalise.py** — Pure ISBN normalization library
  - Hyphenate ISBN-10/13 per international registration group rules
  - Optional: Convert ISBN-10 to ISBN-13
  - Optional: Drop semantically identical second parameter

- **mw_isbn_bot.py** — MediaWiki bot runtime
  - Auto-fetch pages transcluding Template:ISBN and its redirects
  - Paginated queries with automatic continue handling
  - Allowbots compliance check before editing
  - Configurable edit count limit per run

## Dependencies

- `RangeMessage.xml` — Range message file from International ISBN Agency

## Usage

### Command-line Tool

**Format single file:**
```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format \
  --in-place
```

**Format + convert to ISBN-13:**
```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format -to13 \
  --in-place
```

**Format + drop equal label:**
```bash
python isbn_normalise.py \
  --text-file your_wikitext.txt \
  -format \
  --drop-equal-label \
  --in-place
```

### MediaWiki Bot

**Local execution** (requires `.env` file or command-line arguments):
```bash
python mw_isbn_bot.py \
  --wiki-api https://example.org/api.php \
  --bot-username MyBot \
  --bot-password MyBotPassword \
  --max-edits 10
```

**Dry run** (test without saving):
```bash
python mw_isbn_bot.py \
  --wiki-api https://example.org/api.php \
  --bot-username MyBot \
  --bot-password MyBotPassword \
  --dry-run
```

## GitHub Actions Automation

Configure in `.github/workflows/isbn-normaliser-bot.yml`:

1. **Manual trigger** (workflow_dispatch)
   - Optional `max_edits` input to limit edits per run
   - Access Actions tab and click "Run workflow"

2. **Scheduled execution** (optional)
   - Uncomment `schedule` section to enable daily UTC 03:30 execution

## Environment Configuration

Set in `.env` or GitHub Secrets:

```
BOT_USERNAME=YourBotName
BOT_PASSWORD=YourBotPassword
```

Default values:
- `WIKI_API` — Defaults to `https://mzh.moegirl.org.cn/api.php`
- `USER_AGENT` — Defaults to `ISBNNormaliserBot/1.0 (...)`

## Normalization Rules

- Always normalise template parameter 1 (when valid)
- Keep parameter 2 unchanged by default
- Only drop parameter 2 when explicitly enabled and semantically identical
- Edit summary: `根据 [https://www.iso.org/standard/65483.html ISO 2108:2017] 调整ISBN`

## References

- [ISO 2108:2017](https://www.iso.org/standard/65483.html)
- [International ISBN Agency](https://www.isbn-international.org/range_file_generation)
