#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

import brotli
import requests

from isbn_template_normalise import (
    normalise_isbn_templates,
    canonicalise_title_fragment,
    BOOKSOURCE_PAGE_ALIASES,
)

DEFAULT_USER_AGENT = (
    "ISBNNormaliser-Bot/1.1 "
    "(https://github.com/kelly/ISBN-normaliser; 75931686+Kelly-Hsueh@users.noreply.github.com) "
    "requests/2.33.x")
DEFAULT_WIKI_API = "https://mzh.moegirl.org.cn/api.php"
_FALLBACK_TEMPLATE_TITLE = "Template:ISBN|Template:ISBNT|Template:Cite book"
_FALLBACK_XML_PATH = "RangeMessage.xml"
_DEFAULT_SUMMARY = (
    "根据 ISO 2108:2017（https://www.iso.org/standard/65483.html ）自动"
    "调整ISBN（若阁下对此次修改感到疑惑，可以前往 https://grp.isbn-international.org/"
    " 查找出版社前缀信息）")

# Hardcoded operational constants — not exposed as CLI flags or env vars.
_MAXLAG: int = 3
_TIMEOUT: int = 30
_EDIT_INTERVAL: float = 0.2
_USE_BOT_FLAG: bool = True

# Debug-only override: when non-empty, only these pageids are fetched and processed.
# Example: DEBUG_TARGET_PAGEIDS = [12345, 67890]
DEBUG_TARGET_PAGEIDS: list[int] = []

# Query strategy registry.  Values are canonical names; keys include short aliases.
QUERY_ALIASES: dict[str, str] = {
    "transcludedin": "transcludedin",
    "ti": "transcludedin",
    "booksource-search": "booksource-search",
    "booksource": "booksource-search",
    "bs": "booksource-search",
}

# ---------------------------------------------------------------------------
# Environment loading
# ---------------------------------------------------------------------------


def load_env_file(env_path: str) -> None:
    """Load key=value pairs from *env_path* into os.environ via setdefault.

    setdefault means: values already present in os.environ (e.g. injected by
    GitHub Actions via ``env:``) are never overwritten.
    """
    path = Path(env_path)
    if not path.exists() or not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if ((value.startswith('"') and value.endswith('"'))
                or (value.startswith("'") and value.endswith("'"))):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def load_env_files() -> None:
    """Load both env files with the correct priority order.

    Priority (highest to lowest):
      1. Variables already in os.environ  (e.g. GHA ``env:`` injection)
      2. .env.pwd                         (private credentials, gitignored)
      3. .env                             (public config, version-controlled)

    Because load_env_file uses setdefault, loading .env.pwd *before* .env
    ensures .env.pwd values survive the subsequent .env load.
    """
    load_env_file(".env.pwd")
    load_env_file(".env")


def _parse_bool_env(key: str, *, default: bool) -> bool:
    """Read a boolean value from os.environ[key].

    Accepts: 1/true/yes (case-insensitive) -> True,
             0/false/no  (case-insensitive) -> False,
             absent / empty                 -> *default*.
    """
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    raise RuntimeError(
        f"Environment variable {key!r} must be true/false/1/0/yes/no, got {raw!r}."
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def safe_get_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"API returned non-JSON response, HTTP {response.status_code}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError("API JSON response is not an object.")
    return data


def parse_response_json(response: requests.Response) -> dict[str, Any]:
    # 1) Normal path: requests handles JSON (and often compression) for us.
    with contextlib.suppress(RuntimeError):
        return safe_get_json(response)

    # 2) Fallback: some endpoints may return brotli bytes without reliable headers.
    with contextlib.suppress(brotli.error, UnicodeDecodeError,
                             json.JSONDecodeError):
        decoded = brotli.decompress(response.content).decode("utf-8")
        data = json.loads(decoded)
        if isinstance(data, dict):
            return data

    preview = response.text[:160].replace("\n", " ")
    raise RuntimeError(
        f"API returned non-JSON response, HTTP {response.status_code}, body={preview!r}"
    )


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


def api_get_json(
    session: requests.Session,
    wiki_api: str,
    params: dict[str, Any],
    timeout: int,
    error_context: str,
) -> dict[str, Any]:
    try:
        response = session.get(wiki_api, params=params, timeout=timeout)
        response.raise_for_status()
        return parse_response_json(response)
    except Exception as exc:
        raise RuntimeError(f"{error_context}: {exc}") from exc


def api_post_json(
    session: requests.Session,
    wiki_api: str,
    data: dict[str, Any],
    timeout: int,
    error_context: str,
) -> dict[str, Any]:
    try:
        response = session.post(wiki_api, data=data, timeout=timeout)
        response.raise_for_status()
        return parse_response_json(response)
    except Exception as exc:
        raise RuntimeError(f"{error_context}: {exc}") from exc


# ---------------------------------------------------------------------------
# MediaWiki auth
# ---------------------------------------------------------------------------


def get_login_token(
    session: requests.Session,
    wiki_api: str,
) -> str:
    data = api_get_json(
        session=session,
        wiki_api=wiki_api,
        params={
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json",
            "maxlag": _MAXLAG,
        },
        timeout=_TIMEOUT,
        error_context="Failed to fetch login token",
    )
    token = data.get("query", {}).get("tokens", {}).get("logintoken")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"Login token missing: {data}")
    return token


def normalise_assert_user(bot_username: str) -> str:
    # MediaWiki assertuser does not accept suffixes like @group or @host.
    return bot_username.split("@", 1)[0]


def login_with_bot_password(
    session: requests.Session,
    wiki_api: str,
    bot_username: str,
    bot_password: str,
) -> None:
    login_token = get_login_token(session=session, wiki_api=wiki_api)

    result = api_post_json(
        session=session,
        wiki_api=wiki_api,
        data={
            "action": "login",
            "lgname": bot_username,
            "lgpassword": bot_password,
            "lgtoken": login_token,
            "format": "json",
            "maxlag": _MAXLAG,
        },
        timeout=_TIMEOUT,
        error_context="Login request failed",
    )
    if result.get("login", {}).get("result") != "Success":
        raise RuntimeError(f"Login failed: {result}")


def get_csrf_token(
    session: requests.Session,
    wiki_api: str,
    assert_user: str,
) -> str:
    data = api_get_json(
        session=session,
        wiki_api=wiki_api,
        params={
            "action": "query",
            "meta": "tokens",
            "format": "json",
            "assertuser": assert_user,
            "maxlag": _MAXLAG,
        },
        timeout=_TIMEOUT,
        error_context="Failed to fetch CSRF token",
    )
    token = data.get("query", {}).get("tokens", {}).get("csrftoken")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"CSRF token missing: {data}")
    return token


# ---------------------------------------------------------------------------
# Page content helpers
# ---------------------------------------------------------------------------


def allowbots(text: str, user: str) -> bool:
    escaped_user = re.escape(user)
    pattern = (r"\{\{(nobots|bots\|"
               r"(allow=none|deny=.*?" + escaped_user + r".*?"
               r"|optout=all|deny=all))\}\}")
    return not re.search(pattern, text, flags=re.IGNORECASE)


def is_underconstruction(text: str) -> bool:
    pattern = r"\{\{(施工中|[编編][辑輯]中|inuse)(?:\|[^}]*)?\}\}"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _merge_generated_pages_with_revisions(
    data: dict[str, Any],
    pageids: list[int],
    seen: set[int],
    pages_by_id: dict[int, dict[str, Any]],
) -> None:
    pages = data.get("query", {}).get("pages", [])
    if not isinstance(pages, list):
        return

    for page in pages:
        if not isinstance(page, dict):
            continue

        pageid = page.get("pageid")
        if not isinstance(pageid, int):
            continue

        if pageid not in seen:
            seen.add(pageid)
            pageids.append(pageid)

        merged = dict(pages_by_id.get(pageid, {}))
        for key, value in page.items():
            if key == "revisions":
                # Keep existing revisions when this chunk only carries metadata.
                existing_revisions = merged.get("revisions")
                if isinstance(value, list) and (
                        value or not isinstance(existing_revisions, list)):
                    merged[key] = value
                continue
            merged[key] = value
        pages_by_id[pageid] = merged


# ---------------------------------------------------------------------------
# Query strategies
# ---------------------------------------------------------------------------


def fetch_transcluded_pages_with_revisions(
    session: requests.Session,
    wiki_api: str,
    template_title: str,
) -> tuple[list[int], dict[int, dict[str, Any]], str]:
    pageids: list[int] = []
    seen: set[int] = set()
    pages_by_id: dict[int, dict[str, Any]] = {}
    curtimestamp = ""

    request_data: dict[str, Any] = {
        "action": "query",
        "format": "json",
        "maxlag": _MAXLAG,
        "prop": "revisions",
        "titles": template_title,
        "generator": "transcludedin",
        "formatversion": 2,
        "rvprop": "content|ids",
        "rvslots": "main",
        "gtilimit": "max",
        "curtimestamp": 1,
    }

    while True:
        response_data = api_post_json(
            session=session,
            wiki_api=wiki_api,
            data=request_data,
            timeout=_TIMEOUT,
            error_context="Failed to fetch transcluded pages with revisions",
        )
        if "error" in response_data:
            raise RuntimeError(
                f"API error on transcluded revisions query: {response_data['error']}"
            )

        if warnings := response_data.get("warnings", {}).get("result", {}):
            if warning_msg := warnings.get("warnings", ""):
                print(f"[API WARNING] {warning_msg}", file=sys.stderr)

        if not curtimestamp and isinstance(response_data.get("curtimestamp"),
                                           str):
            curtimestamp = response_data["curtimestamp"]

        _merge_generated_pages_with_revisions(
            response_data,
            pageids,
            seen,
            pages_by_id,
        )

        cont = response_data.get("continue")
        if not isinstance(cont, dict):
            break
        request_data |= cont

    return pageids, pages_by_id, curtimestamp


def search_insource_pages(
    session: requests.Session,
    wiki_api: str,
    search_term: str,
) -> set[int]:
    """Run a single insource: search and return all matching pageids.

    Handles API continuation automatically.
    """
    pageids: set[int] = set()
    request_data: dict[str, Any] = {
        "action": "query",
        "format": "json",
        "list": "search",
        "formatversion": "2",
        "srsearch": f'insource:"{search_term}"',
        "srnamespace": "*",
        "srlimit": "max",
        "srprop": "",
        "maxlag": _MAXLAG,
    }

    while True:
        data = api_post_json(
            session=session,
            wiki_api=wiki_api,
            data=request_data,
            timeout=_TIMEOUT,
            error_context=f"Failed to search insource:{search_term!r}",
        )
        if "error" in data:
            raise RuntimeError(
                f"API error on insource search for {search_term!r}: {data['error']}"
            )

        for result in data.get("query", {}).get("search", []) or []:
            if not isinstance(result, dict):
                continue
            pageid = result.get("pageid")
            if isinstance(pageid, int) and pageid > 0:
                pageids.add(pageid)

        cont = data.get("continue")
        if not isinstance(cont, dict):
            break
        request_data |= cont

    return pageids


def collect_booksource_search_pageids(
    session: requests.Session,
    wiki_api: str,
) -> list[int]:
    """Search every alias in BOOKSOURCE_PAGE_ALIASES via insource:.

    Returns a deduplicated list of pageids across all alias searches.
    """
    all_pageids: set[int] = set()
    for alias in sorted(BOOKSOURCE_PAGE_ALIASES):
        print(f"Searching insource:{alias!r} ...")
        try:
            found = search_insource_pages(
                session=session,
                wiki_api=wiki_api,
                search_term=alias,
            )
        except RuntimeError as exc:
            print(
                f"[WARNING] insource search failed for {alias!r}: {exc}",
                file=sys.stderr,
            )
            found = set()
        print(f"  -> {len(found)} pages")
        all_pageids |= found
    print(f"Total unique pages (booksource-search): {len(all_pageids)}")
    return list(all_pageids)


def _fetch_by_booksource_search(
    session: requests.Session,
    wiki_api: str,
    template_title: str,  # unused; present for _FetchFn compatibility
) -> tuple[list[int], dict[int, dict[str, Any]], str]:
    raw_pageids = collect_booksource_search_pageids(
        session=session,
        wiki_api=wiki_api,
    )
    return fetch_pages_by_pageids_with_revisions(
        session=session,
        wiki_api=wiki_api,
        pageids=raw_pageids,
    )


# Callable type alias for query strategy functions.
_FetchFn = Callable[
    [requests.Session, str, str],
    tuple[list[int], dict[int, dict[str, Any]], str],
]

_QUERY_STRATEGIES: dict[str, _FetchFn] = {
    "transcludedin": fetch_transcluded_pages_with_revisions,
    "booksource-search": _fetch_by_booksource_search,
}


def fetch_pages_by_pageids_with_revisions(
    session: requests.Session,
    wiki_api: str,
    pageids: list[int],
) -> tuple[list[int], dict[int, dict[str, Any]], str]:
    target_ids = [pid for pid in pageids if isinstance(pid, int) and pid > 0]
    if not target_ids:
        return [], {}, ""

    ordered_pageids: list[int] = []
    pages_by_id: dict[int, dict[str, Any]] = {}
    curtimestamp = ""

    chunk_size = 50
    for i in range(0, len(target_ids), chunk_size):
        chunk = target_ids[i:i + chunk_size]
        response_data = api_post_json(
            session=session,
            wiki_api=wiki_api,
            data={
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "maxlag": _MAXLAG,
                "prop": "revisions",
                "pageids": "|".join(str(pid) for pid in chunk),
                "rvprop": "content|ids",
                "rvslots": "main",
                "curtimestamp": 1,
            },
            timeout=_TIMEOUT,
            error_context="Failed to fetch pages by pageid with revisions",
        )
        if "error" in response_data:
            raise RuntimeError(
                f"API error on pageid revisions query: {response_data['error']}"
            )

        if not curtimestamp and isinstance(response_data.get("curtimestamp"),
                                           str):
            curtimestamp = response_data["curtimestamp"]

        pages = response_data.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            continue

        for page in pages:
            if not isinstance(page, dict):
                continue
            pageid = page.get("pageid")
            if not isinstance(pageid, int):
                continue

            if pageid not in ordered_pageids:
                ordered_pageids.append(pageid)

            pages_by_id[pageid] = page

    ordered = [pid for pid in target_ids if pid in pages_by_id]
    return ordered, pages_by_id, curtimestamp


# ---------------------------------------------------------------------------
# Page processing
# ---------------------------------------------------------------------------


def extract_main_content(page: dict[str, Any]) -> str | None:
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        return None

    rev0 = revisions[0]
    if not isinstance(rev0, dict):
        return None

    slots = rev0.get("slots")
    if not isinstance(slots, dict):
        content = rev0.get("content")
        return content if isinstance(content, str) else None

    main = slots.get("main")
    if not isinstance(main, dict):
        return None
    content = main.get("content")
    return content if isinstance(content, str) else None


def extract_baserevid(page: dict[str, Any]) -> str:
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        return ""

    rev0 = revisions[0]
    if not isinstance(rev0, dict):
        return ""

    revid = rev0.get("revid")
    return str(revid) if revid is not None else ""


def get_skip_reason(content: str, assert_user: str) -> str | None:
    if not allowbots(content, assert_user):
        return "bots"
    return "inuse" if is_underconstruction(content) else None


def normalise_page_isbn_templates(
    content: str,
    args: argparse.Namespace,
    xml_path: Path,
    template_preferred_map: dict[str, str] | None = None,
) -> tuple[str, int]:
    return normalise_isbn_templates(
        content,
        xml_path,
        convert_10_to_13=args.to13,
        rehyphenate_equal_label=args.rehyphenate_equal_label,
        template_preferred_map=template_preferred_map,
    )


def resolve_template_aliases(
    session: requests.Session,
    wiki_api: str,
    template_titles: str | None,
) -> dict[str, str]:
    preferred: dict[str, str] = {}
    if template_titles:
        for title in template_titles.split("|"):
            frag = title.rsplit(":", 1)[-1].strip()
            if not frag:
                continue
            if key := canonicalise_title_fragment(frag):
                preferred.setdefault(key, frag)
    if not template_titles:
        preferred = {}

    try:
        data = api_post_json(
            session=session,
            wiki_api=wiki_api,
            data={
                "action": "query",
                "format": "json",
                "prop": "redirects",
                "titles": template_titles,
                "redirects": 1,
                "formatversion": "2",
                "rdprop": "title",
            },
            timeout=_TIMEOUT,
            error_context="Failed to resolve template redirects",
        )
    except Exception:
        return preferred

    pages = data.get("query", {}).get("pages", [])
    if not isinstance(pages, list):
        return preferred

    for page in pages:
        if not isinstance(page, dict):
            continue
        page_title = page.get("title")
        if not isinstance(page_title, str):
            continue
        page_frag = page_title.rsplit(":", 1)[-1].strip()
        if not page_frag:
            continue
        page_key = canonicalise_title_fragment(page_frag)
        preferred.setdefault(page_key, page_frag)

        for rd in page.get("redirects", []) or []:
            rd_title = rd.get("title")
            if not isinstance(rd_title, str):
                continue
            rd_frag = rd_title.rsplit(":", 1)[-1].strip()
            if not rd_frag:
                continue
            rd_key = canonicalise_title_fragment(rd_frag)
            preferred.setdefault(rd_key, preferred.get(page_key, page_frag))

    return preferred


def edit_page_text(
    session: requests.Session,
    wiki_api: str,
    pageid: int,
    text: str,
    summary: str,
    csrf_token: str,
    assert_user: str,
    bot: bool,
    baserevid: str = "",
    starttimestamp: str = "",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "action": "edit",
        "format": "json",
        "maxlag": _MAXLAG,
        "assertuser": assert_user,
        "pageid": str(pageid),
        "text": text,
        "summary": summary,
        "token": csrf_token,
        "tags": "Bot",
        "minor": 1,
    }
    if bot:
        data["bot"] = "1"
    if baserevid:
        data["baserevid"] = baserevid
    if starttimestamp:
        data["starttimestamp"] = starttimestamp

    result = api_post_json(
        session=session,
        wiki_api=wiki_api,
        data=data,
        timeout=_TIMEOUT,
        error_context=f"Failed to edit pageid={pageid}",
    )
    if "error" in result:
        error_code = result.get("error", {}).get("code", "")
        if error_code == "editconflict":
            raise RuntimeError(
                f"[editconflict] pageid={pageid}: {result['error']}")
        raise RuntimeError(
            f"API edit error for pageid={pageid}: {result['error']}")
    return result


def _try_apply_changes(
    session: requests.Session,
    wiki_api: str,
    pageid: int,
    title: str,
    new_text: str,
    replacements: int,
    args: argparse.Namespace,
    csrf_token: str,
    assert_user: str,
    baserevid: str,
    start_timestamp: str,
) -> tuple[bool, bool]:
    """Apply changes to page (dry-run or real edit).

    Returns: (changed_flag, failed_flag)
    """
    if args.dry_run:
        print(
            f"[DRY-RUN][CHANGE] pageid={pageid} title={title} replacements={replacements}"
        )
        return True, False

    try:
        edit_page_text(
            session=session,
            wiki_api=wiki_api,
            pageid=pageid,
            text=new_text,
            summary=args.summary,
            csrf_token=csrf_token,
            assert_user=assert_user,
            bot=_USE_BOT_FLAG,
            baserevid=baserevid,
            starttimestamp=start_timestamp,
        )
        print(
            f"[EDITED] pageid={pageid} title={title} replacements={replacements}"
        )
        time.sleep(_EDIT_INTERVAL)
        return True, False
    except RuntimeError as exc:
        if "editconflict" not in str(exc):
            print(f"[FAILED] pageid={pageid} title={title} error={exc}",
                  file=sys.stderr)
            return False, True
        print(f"[SKIP][conflict] pageid={pageid} title={title}")
        return False, False
    except Exception as exc:
        print(f"[FAILED] pageid={pageid} title={title} error={exc}",
              file=sys.stderr)
        return False, True


def process_pages(
    args: argparse.Namespace,
    session: requests.Session,
    wiki_api: str,
    bot_username: str,
    xml_path: Path,
    pageids: list[int],
    pages_by_id: dict[int, dict[str, Any]],
    csrf_token: str,
    start_timestamp: str = "",
    template_preferred_map: dict[str, str] | None = None,
) -> tuple[int, int, int, int]:
    processed = 0
    skipped_bots = 0
    changed = 0
    failed = 0
    assert_user = normalise_assert_user(bot_username)

    for pageid in pageids:
        page = pages_by_id.get(pageid)
        if page is None:
            continue

        title = page.get("title", "")
        content = extract_main_content(page)
        if content is None:
            continue

        baserevid = extract_baserevid(page)
        processed += 1

        skip_reason = get_skip_reason(content, assert_user)
        if skip_reason is not None:
            skipped_bots += 1
            print(f"[SKIP][{skip_reason}] pageid={pageid} title={title}")
            continue

        new_text, replacements = normalise_page_isbn_templates(
            content,
            args,
            xml_path,
            template_preferred_map=template_preferred_map,
        )
        if replacements <= 0 or new_text == content:
            continue

        if args.max_edits is not None and changed >= args.max_edits:
            print(
                f"[LIMIT] Reached max_edits limit ({args.max_edits}), stopping."
            )
            break

        changed_flag, failed_flag = _try_apply_changes(
            session=session,
            wiki_api=wiki_api,
            pageid=pageid,
            title=title,
            new_text=new_text,
            replacements=replacements,
            args=args,
            csrf_token=csrf_token,
            assert_user=assert_user,
            baserevid=baserevid,
            start_timestamp=start_timestamp,
        )
        if changed_flag:
            changed += 1
        if failed_flag:
            failed += 1

    return processed, skipped_bots, changed, failed


# ---------------------------------------------------------------------------
# Runtime config & workflow
# ---------------------------------------------------------------------------


def parse_runtime_config(
        args: argparse.Namespace) -> tuple[str, str, str, str]:
    """Resolve runtime config with priority: CLI flag > env var > built-in default."""
    wiki_api = args.wiki_api or os.environ.get("WIKI_API", DEFAULT_WIKI_API)
    bot_username = (args.bot_username
                    or os.environ.get("BOT_USERNAME", "")).strip()
    bot_password = (args.bot_password
                    or os.environ.get("BOT_PASSWORD", "")).strip()
    user_agent = args.user_agent or os.environ.get("USER_AGENT",
                                                   DEFAULT_USER_AGENT)

    if not wiki_api:
        raise RuntimeError(
            "WIKI_API is required (--wiki-api flag, or WIKI_API in .env).")
    if not bot_username:
        raise RuntimeError(
            "BOT_USERNAME is required (--bot-username flag, or BOT_USERNAME in .env.pwd)."
        )
    if not bot_password:
        raise RuntimeError(
            "BOT_PASSWORD is required (--bot-password flag, or BOT_PASSWORD in .env.pwd)."
        )

    return wiki_api, bot_username, bot_password, user_agent


def validate_xml_path(xml_arg: str) -> Path:
    xml_path = Path(xml_arg)
    if not xml_path.exists():
        raise RuntimeError(f"XML file not found: {xml_path}")
    return xml_path


def run_normalization_workflow(
    args: argparse.Namespace,
    session: requests.Session,
    wiki_api: str,
    bot_username: str,
    bot_password: str,
    xml_path: Path,
) -> int:
    login_with_bot_password(
        session=session,
        wiki_api=wiki_api,
        bot_username=bot_username,
        bot_password=bot_password,
    )
    assert_user = normalise_assert_user(bot_username)
    csrf_token = get_csrf_token(
        session=session,
        wiki_api=wiki_api,
        assert_user=assert_user,
    )

    template_preferred_map = resolve_template_aliases(
        session=session,
        wiki_api=wiki_api,
        template_titles=args.template_title,
    )

    if DEBUG_TARGET_PAGEIDS:
        pageids, pages_by_id, curtimestamp = fetch_pages_by_pageids_with_revisions(
            session=session,
            wiki_api=wiki_api,
            pageids=DEBUG_TARGET_PAGEIDS,
        )
        print(
            f"Fetched pages with revisions (debug pageids): {len(pages_by_id)}"
        )
    else:
        fetch_fn = _QUERY_STRATEGIES[args.query]
        pageids, pages_by_id, curtimestamp = fetch_fn(
            session,
            wiki_api,
            args.template_title,
        )
        print(f"Fetched pages ({args.query}): {len(pages_by_id)}")

    processed, skipped_bots, changed, failed = process_pages(
        args=args,
        session=session,
        wiki_api=wiki_api,
        bot_username=bot_username,
        xml_path=xml_path,
        pageids=pageids,
        pages_by_id=pages_by_id,
        csrf_token=csrf_token,
        start_timestamp=curtimestamp,
        template_preferred_map=template_preferred_map,
    )

    result_msg = (f"Done. processed={processed}, changed={changed}, "
                  f"skipped_bots={skipped_bots}, failed={failed}")
    print(result_msg)
    return 0 if failed == 0 else 2


def execute(args: argparse.Namespace) -> int:
    try:
        load_env_files()

        # Resolve query strategy: CLI arg > DEFAULT_QUERY env var > 'transcludedin'
        if args.query is None:
            args.query = os.environ.get("DEFAULT_QUERY", "transcludedin")
        if args.query not in QUERY_ALIASES:
            valid = ", ".join(sorted(set(QUERY_ALIASES.values())))
            raise RuntimeError(
                f"Unknown query strategy: {args.query!r}. Valid: {valid}")
        args.query = QUERY_ALIASES[args.query]

        # Resolve env-backed optional CLI args.
        # Boolean flags: CLI flag (True) wins; otherwise fall back to env var.
        if not args.to13:
            args.to13 = _parse_bool_env("TO13", default=False)
        if not args.rehyphenate_equal_label:
            args.rehyphenate_equal_label = _parse_bool_env(
                "REHYPHENATE_EQUAL_LABEL", default=False)

        # String args: non-empty CLI value wins; otherwise env var; then built-in default.
        if not args.template_title:
            args.template_title = os.environ.get("TEMPLATE_TITLE",
                                                 _FALLBACK_TEMPLATE_TITLE)
        if not args.xml:
            args.xml = os.environ.get("XML_PATH", _FALLBACK_XML_PATH)
        if not args.summary:
            args.summary = os.environ.get("SUMMARY", _DEFAULT_SUMMARY)

        wiki_api, bot_username, bot_password, user_agent = parse_runtime_config(
            args)
        xml_path = validate_xml_path(args.xml)
        session = build_session(user_agent)

        return run_normalization_workflow(
            args=args,
            session=session,
            wiki_api=wiki_api,
            bot_username=bot_username,
            bot_password=bot_password,
            xml_path=xml_path,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MediaWiki runner for ISBN template normalization.")
    parser.add_argument(
        "--xml",
        default=None,
        help="Path to ISBNRangeMessage XML file. Overrides XML_PATH in .env.",
    )
    parser.add_argument(
        "-to13",
        "--to13",
        action="store_true",
        default=False,
        help="Convert ISBN-10 template values to ISBN-13 before output. "
        "Overrides TO13 in .env.",
    )
    parser.add_argument(
        "--rehyphenate-equal-label",
        action="store_true",
        default=False,
        help=(
            "When template param1 and param2 are semantically the same ISBN, "
            "replace the template with {{ISBNT|$1}} and keep parameter 1 "
            "hyphenated. Overrides REHYPHENATE_EQUAL_LABEL in .env."),
    )
    parser.add_argument(
        "--query",
        "-q",
        choices=list(QUERY_ALIASES),
        metavar="{transcludedin|ti|booksource-search|booksource|bs}",
        default=None,
        help="Page fetch strategy. "
        "Default: DEFAULT_QUERY env var (from .env), or 'transcludedin' if unset.",
    )
    parser.add_argument(
        "--wiki-api",
        default=None,
        help="MediaWiki API endpoint. Overrides WIKI_API in .env.",
    )
    parser.add_argument(
        "--bot-username",
        default=None,
        help="Bot username. Overrides BOT_USERNAME in .env.pwd.",
    )
    parser.add_argument(
        "--bot-password",
        default=None,
        help="Bot password. Overrides BOT_PASSWORD in .env.pwd.",
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        help="HTTP User-Agent. Overrides USER_AGENT in .env.",
    )
    parser.add_argument(
        "--template-title",
        default=None,
        help="Template title(s) for transclusion lookup. "
        "Overrides TEMPLATE_TITLE in .env.",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="Edit summary used when saving pages. Overrides SUMMARY in .env.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run full workflow but do not save edits.",
    )
    parser.add_argument(
        "--max-edits",
        type=int,
        default=None,
        help="Maximum number of edits to perform. Omit for unlimited.",
    )
    return parser


def main() -> int:
    return execute(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
