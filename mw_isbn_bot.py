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
from typing import Any

import brotli
import requests

from isbn_template_normalise import normalise_isbn_templates

DEFAULT_USER_AGENT = (
    "ISBNNormaliserBot/1.0 "
    "(https://github.com/kelly/ISBN-normaliser) requests/2.x")
DEFAULT_WIKI_API = "https://mzh.moegirl.org.cn/api.php"

# Debug-only override: when non-empty, only these pageids are fetched and processed.
# Example: DEBUG_TARGET_PAGEIDS = [12345, 67890]
DEBUG_TARGET_PAGEIDS: list[int] = [497944]


def parse_bool_env(raw_value: str, *, default: bool) -> bool:
    value = raw_value.strip().lower()
    if not value:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise RuntimeError("Only true/false or empty is supported.")


def load_env_file(env_path: str = ".env") -> None:
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


def get_login_token(
    session: requests.Session,
    wiki_api: str,
    timeout: int,
    max_lag: int,
) -> str:
    data = api_get_json(
        session=session,
        wiki_api=wiki_api,
        params={
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json",
            "maxlag": max_lag,
        },
        timeout=timeout,
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
    timeout: int,
    max_lag: int,
) -> None:
    login_token = get_login_token(
        session=session,
        wiki_api=wiki_api,
        timeout=timeout,
        max_lag=max_lag,
    )

    result = api_post_json(
        session=session,
        wiki_api=wiki_api,
        data={
            "action": "login",
            "lgname": bot_username,
            "lgpassword": bot_password,
            "lgtoken": login_token,
            "format": "json",
            "maxlag": max_lag,
        },
        timeout=timeout,
        error_context="Login request failed",
    )
    if result.get("login", {}).get("result") != "Success":
        raise RuntimeError(f"Login failed: {result}")


def get_csrf_token(
    session: requests.Session,
    wiki_api: str,
    timeout: int,
    max_lag: int,
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
            "maxlag": max_lag,
        },
        timeout=timeout,
        error_context="Failed to fetch CSRF token",
    )
    token = data.get("query", {}).get("tokens", {}).get("csrftoken")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"CSRF token missing: {data}")
    return token


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


def fetch_transcluded_pages_with_revisions(
    session: requests.Session,
    wiki_api: str,
    template_title: str,
    timeout: int,
    max_lag: int,
) -> tuple[list[int], dict[int, dict[str, Any]], str]:
    pageids: list[int] = []
    seen: set[int] = set()
    pages_by_id: dict[int, dict[str, Any]] = {}
    curtimestamp = ""

    request_data: dict[str, Any] = {
        "action": "query",
        "format": "json",
        "maxlag": max_lag,
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
            timeout=timeout,
            error_context="Failed to fetch transcluded pages with revisions",
        )
        if "error" in response_data:
            raise RuntimeError(
                f"API error on transcluded revisions query: {response_data['error']}"
            )

        # Check for API limits/truncation warnings
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


def fetch_pages_by_pageids_with_revisions(
    session: requests.Session,
    wiki_api: str,
    pageids: list[int],
    timeout: int,
    max_lag: int,
) -> tuple[list[int], dict[int, dict[str, Any]], str]:
    target_ids = [pid for pid in pageids if isinstance(pid, int) and pid > 0]
    if not target_ids:
        return [], {}, ""

    ordered_pageids: list[int] = []
    pages_by_id: dict[int, dict[str, Any]] = {}
    curtimestamp = ""

    # MediaWiki API accepts multiple pageids split by '|'. Keep chunks modest.
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
                "maxlag": max_lag,
                "prop": "revisions",
                "pageids": "|".join(str(pid) for pid in chunk),
                "rvprop": "content|ids",
                "rvslots": "main",
                "curtimestamp": 1,
            },
            timeout=timeout,
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

    # Keep caller-provided order, but only for pages successfully fetched.
    ordered = [pid for pid in target_ids if pid in pages_by_id]
    return ordered, pages_by_id, curtimestamp


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
) -> tuple[str, int]:
    return normalise_isbn_templates(
        content,
        xml_path,
        convert_10_to_13=args.to13,
        rehyphenate_equal_label=args.rehyphenate_equal_label,
    )


def edit_page_text(
    session: requests.Session,
    wiki_api: str,
    pageid: int,
    text: str,
    summary: str,
    timeout: int,
    max_lag: int,
    csrf_token: str,
    assert_user: str,
    bot: bool,
    baserevid: str = "",
    starttimestamp: str = "",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "action": "edit",
        "format": "json",
        "maxlag": max_lag,
        "assertuser": assert_user,
        "pageid": str(pageid),
        "text": text,
        "summary": summary,
        "token": csrf_token,
        "tags": "Bot",
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
        timeout=timeout,
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


def parse_runtime_config(
        args: argparse.Namespace) -> tuple[str, str, str, str]:
    wiki_api = args.wiki_api or DEFAULT_WIKI_API
    bot_username = (args.bot_username
                    or os.environ.get("BOT_USERNAME", "")).strip()
    bot_password = (args.bot_password
                    or os.environ.get("BOT_PASSWORD", "")).strip()
    user_agent = args.user_agent or DEFAULT_USER_AGENT

    if not wiki_api:
        raise RuntimeError("WIKI_API is required (flag or environment).")
    if not bot_username:
        raise RuntimeError("BOT_USERNAME is required (flag or environment).")
    if not bot_password:
        raise RuntimeError("BOT_PASSWORD is required (flag or environment).")

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
    use_bot_flag: bool,
) -> int:
    login_with_bot_password(
        session=session,
        wiki_api=wiki_api,
        bot_username=bot_username,
        bot_password=bot_password,
        timeout=args.timeout,
        max_lag=args.maxlag,
    )
    assert_user = normalise_assert_user(bot_username)
    csrf_token = get_csrf_token(
        session=session,
        wiki_api=wiki_api,
        timeout=args.timeout,
        max_lag=args.maxlag,
        assert_user=assert_user,
    )

    if DEBUG_TARGET_PAGEIDS:
        pageids, pages_by_id, curtimestamp = (
            fetch_pages_by_pageids_with_revisions(
                session=session,
                wiki_api=wiki_api,
                pageids=DEBUG_TARGET_PAGEIDS,
                timeout=args.timeout,
                max_lag=args.maxlag,
            ))
        print("Fetched pages with revisions (test pageids): "
              f"{len(pages_by_id)}")
    else:
        pageids, pages_by_id, curtimestamp = (
            fetch_transcluded_pages_with_revisions(
                session=session,
                wiki_api=wiki_api,
                template_title=args.template_title,
                timeout=args.timeout,
                max_lag=args.maxlag,
            ))
        print(f"Fetched pages with revisions: {len(pages_by_id)}")

    processed, skipped_bots, changed, failed = process_pages(
        args=args,
        session=session,
        wiki_api=wiki_api,
        bot_username=bot_username,
        xml_path=xml_path,
        pageids=pageids,
        pages_by_id=pages_by_id,
        csrf_token=csrf_token,
        use_bot_flag=use_bot_flag,
        start_timestamp=curtimestamp,
    )

    result_msg = (f"Done. processed={processed}, changed={changed}, "
                  f"skipped_bots={skipped_bots}, failed={failed}")
    print(result_msg)
    return 0 if failed == 0 else 2


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
    use_bot_flag: bool,
    baserevid: str,
    start_timestamp: str,
) -> tuple[bool, bool]:
    """Apply changes to page (dry-run or real edit).

    Returns: (changed_count_incremented, should_fail)
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
            timeout=args.timeout,
            max_lag=args.maxlag,
            csrf_token=csrf_token,
            assert_user=assert_user,
            bot=use_bot_flag,
            baserevid=baserevid,
            starttimestamp=start_timestamp,
        )
        print(
            f"[EDITED] pageid={pageid} title={title} replacements={replacements}"
        )
        time.sleep(args.edit_interval)
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
    use_bot_flag: bool,
    start_timestamp: str = "",
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
            use_bot_flag=use_bot_flag,
            baserevid=baserevid,
            start_timestamp=start_timestamp,
        )
        if changed_flag:
            changed += 1
        if failed_flag:
            failed += 1

    return processed, skipped_bots, changed, failed


def execute(args: argparse.Namespace) -> int:
    try:
        load_env_file()

        wiki_api, bot_username, bot_password, user_agent = parse_runtime_config(
            args)
        xml_path = validate_xml_path(args.xml)

        use_bot_flag = parse_bool_env(str(args.bot_flag), default=True)

        session = build_session(user_agent)
        return run_normalization_workflow(
            args=args,
            session=session,
            wiki_api=wiki_api,
            bot_username=bot_username,
            bot_password=bot_password,
            xml_path=xml_path,
            use_bot_flag=use_bot_flag,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    return execute(build_parser().parse_args())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MediaWiki runner for ISBN template normalization.")
    parser.add_argument(
        "--xml",
        default="RangeMessage.xml",
        help="Path to ISBNRangeMessage XML file.",
    )
    parser.add_argument(
        "-to13",
        "--to13",
        action="store_true",
        help="Convert ISBN-10 template values to ISBN-13 before output.",
    )
    parser.add_argument(
        "--rehyphenate-equal-label",
        action="store_true",
        help=(
            "When template param1 and param2 are semantically the same ISBN, "
            "set param1 to non-hyphenated form and keep param2 hyphenated."),
    )
    parser.add_argument(
        "--wiki-api",
        help="MediaWiki API endpoint, e.g. https://example.org/api.php",
    )
    parser.add_argument(
        "--bot-username",
        help="Bot username for login.",
    )
    parser.add_argument(
        "--bot-password",
        help="Bot password for login.",
    )
    parser.add_argument(
        "--user-agent",
        help="HTTP User-Agent used by the bot.",
    )
    parser.add_argument(
        "--template-title",
        default="Template:ISBN|Template:Cite book",
        help="Template title for transclusion lookup.",
    )
    parser.add_argument(
        "--summary",
        default="根据 ISO 2108:2017（https://www.iso.org/standard/65483.html ）自动"
        "调整ISBN（若阁下对此次修改感到疑惑，可以前往 https://grp.isbn-international.org/ 查找出版社前缀信息）",
        help="Edit summary used when saving pages.",
    )
    parser.add_argument(
        "--maxlag",
        type=int,
        default=3,
        help="MediaWiki maxlag value.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout (seconds).",
    )
    parser.add_argument(
        "--edit-interval",
        type=float,
        default=0.2,
        help="Seconds to sleep between successful edits.",
    )
    parser.add_argument(
        "--bot-flag",
        default="true",
        help="Whether to submit edit with bot=1 (true/false).",
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
        help="Maximum number of edits to perform. None means unlimited.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
