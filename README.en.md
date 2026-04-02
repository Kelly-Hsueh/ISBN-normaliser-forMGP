[中文](README.md) | [English](README.en.md)

# ISBN Normaliser

A standalone normalisation tool and MediaWiki bot for the {{[ISBN](https://mzh.moegirl.org.cn/Template:ISBN)}} template in MoegirlPedia wikitext. The normaliser script can also run independently.

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

## Environment And Packages

- Python 3.10+ (3.11 recommended, consistent with GitHub Actions)
- Third-party packages:
  - `requests`
  - `brotli`
  - `mwparserfromhell`

Quick install:

```bash
python -m pip install --upgrade pip
pip install requests brotli mwparserfromhell
```

## Quick Start

```bash
git clone https://github.com/kelly/ISBN-normaliser.git
cd ISBN-normaliser
python -m pip install --upgrade pip
pip install requests brotli mwparserfromhell
```

Then ensure `RangeMessage.xml` is available (already included in this repository), and run the commands below.

## Usage

### Command-line Tool

**Normalise a single ISBN:**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  9787302511625
```

**Format single file:**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  --text-file your_wikitext.txt \
  -format \
  --in-place
```

**Format + convert to ISBN-13:**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
  --text-file your_wikitext.txt \
  -format -to13 \
  --in-place
```

**Format + drop equal label:**
```bash
python isbn_normalise.py \
  --xml RangeMessage.xml \
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

### 1) ISBN Bot Workflow

File: `.github/workflows/isbn-normaliser-bot.yml`

1. **Manual trigger (workflow_dispatch)**
  - Optional `max_edits` input to limit edits in this run
  - Open the Actions tab and click "Run workflow"

2. **Scheduled execution (optional)**
  - `schedule` is currently commented out and can be enabled if needed
  - Planned time: UTC `20:15` (cron: `15 20 * * *`)

### 2) RangeMessage Auto-update Workflow

File: `.github/workflows/update-rangemessage.yml`

1. **Trigger modes**
  - Manual trigger (`workflow_dispatch`)
  - Weekly scheduled run every Wednesday at UTC `03:05` (cron: `05 3 * * 3`)

2. **Behavior**
  - Downloads the latest `RangeMessage.xml`
  - Commits and pushes only when the file content changes

3. **Required permission**
  - Workflow declares `contents: write` to allow commit and push

## File Overview

- `isbn_normalise.py`:
  - Core ISBN normalisation logic
  - Supports both single-ISBN output and batch template rewrite in wikitext

- `mw_isbn_bot.py`:
  - MediaWiki bot entrypoint
  - Handles login, paginated page fetch, Allowbots checks, and edit submission

- `RangeMessage.xml`:
  - ISBN range rule source file
  - Published by the International ISBN Agency and used by the normalisation logic

- `.github/workflows/isbn-normaliser-bot.yml`:
  - Bot execution workflow (manual trigger, optional schedule)

- `.github/workflows/update-rangemessage.yml`:
  - Workflow to update `RangeMessage.xml` automatically

## Environment Configuration

Set in `.env` or GitHub Secrets:

```
BOT_USERNAME=YourBotName
BOT_PASSWORD=YourBotPassword
```

Default values:
- `WIKI_API` — Built-in default is `https://mzh.moegirl.org.cn/api.php`
- `USER_AGENT` — Built-in default is `ISBNNormaliserBot/1.0 (...)`

Notes:
- Current version only loads `BOT_USERNAME` and `BOT_PASSWORD` from `.env`
- Other runtime options (such as `--max-edits`, `--dry-run`, `--to13`) should be passed by command-line flags

## Normalization Rules

- Always normalise template parameter 1 (when valid)
- Keep parameter 2 unchanged by default
- Only drop parameter 2 when explicitly enabled and semantically identical
- Edit summary: `根据 ISO 2108:2017（https://www.iso.org/standard/65483.html ）自动调整ISBN（若阁下对此次修改感到疑惑，可以前往 https://grp.isbn-international.org/ 查找出版社前缀信息）`

## Troubleshooting

- `ModuleNotFoundError`:
  - Run `pip install requests brotli mwparserfromhell`

- `RangeMessage.xml` not found:
  - Ensure the file exists in the current directory, or pass the correct path using `--xml`

- Bot does not make edits:
  - Run with `--dry-run` first and check whether candidate pages are detected
  - Check whether `--max-edits` is set to 0
  - Verify bot permissions and local Allowbots/editing policy on the target wiki

- GitHub Actions run has no commit:
  - For `update-rangemessage`, "No changes to commit" is expected when upstream range data did not change

## References

- [ISO 2108:2017](https://www.iso.org/standard/65483.html)
- [International ISBN Agency](https://www.isbn-international.org/range_file_generation)
