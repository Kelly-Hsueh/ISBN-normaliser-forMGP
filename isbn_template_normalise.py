#!/usr/bin/env python3
"""Rewrite ISBN templates in wikitext using ISBN normalization rules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import mwparserfromhell

from isbn_normalise import Group, isbn_equivalence_key, load_groups, normalise_token

SPECIAL_NAMESPACE_ALIASES = frozenset({"special", "特殊"})
BOOKSOURCE_PAGE_ALIASES = frozenset({"网络书源", "網絡書源", "booksources"})


def try_normalise_template_value(
    raw_value: str,
    groups: list[Group],
    convert_10_to_13: bool,
) -> str | None:
    try:
        return normalise_token(
            raw_value,
            groups,
            convert_10_to_13=convert_10_to_13,
            with_label=False,
        )
    except ValueError:
        return None


def get_template_label_value(
    template: Any,
    groups: list[Group],
    convert_10_to_13: bool,
) -> str | None:
    if not template.has("2"):
        return None

    label_str = str(template.get("2").value).strip()
    if not label_str:
        return None

    normalised = try_normalise_template_value(
        label_str,
        groups,
        convert_10_to_13,
    )
    return normalised if normalised is not None else label_str


def are_semantically_equal_isbns(
    code_str: str,
    output_label: str | None,
) -> bool:
    if output_label is None:
        return False

    key1 = isbn_equivalence_key(code_str)
    key2 = isbn_equivalence_key(output_label)
    return key1 is not None and key1 == key2


def update_template_label(template: Any, output_label: str | None) -> None:
    if output_label is None:
        if template.has("2"):
            template.remove("2")
        return

    if template.has("2"):
        template.get("2").value = output_label
    else:
        template.add("2", output_label)


def extract_booksource_isbn_from_title(title: Any) -> str | None:
    title_str = str(title).strip()
    if not title_str:
        return None

    # Allow links like [[:Special:网络书源/...]] and normalize for matching.
    if title_str.startswith(":"):
        title_str = title_str[1:].strip()

    if "/" not in title_str:
        return None

    prefix_text, suffix_text = title_str.split("/", 1)
    if ":" not in prefix_text:
        return None

    namespace_raw, page_raw = prefix_text.split(":", 1)

    namespace = canonicalise_title_fragment(namespace_raw)
    page_name = canonicalise_title_fragment(page_raw)

    if namespace not in SPECIAL_NAMESPACE_ALIASES:
        return None
    if page_name not in BOOKSOURCE_PAGE_ALIASES:
        return None

    isbn_text = suffix_text.strip()
    return isbn_text or None


def canonicalise_title_fragment(value: str) -> str:
    # MediaWiki titles treat underscores and spaces similarly.
    return "".join(ch for ch in value.strip().casefold()
                   if not ch.isspace() and ch != "_")


def parse_template_name_aliases(
        template_preferred_map: dict[str, str] | None) -> frozenset[str]:
    # Derive canonical aliases from the provided preferred-name mapping.
    if not template_preferred_map:
        return frozenset()
    return frozenset(template_preferred_map.keys())


# The preferred-name mapping should come from the MediaWiki API via the bot
# process. Do not hardcode defaults here; when called from CLI, the caller
# may provide a mapping constructed from user input if desired.


def split_isbn_prefixed_label(label: str) -> str | None:
    text = label.strip()
    if len(text) <= 4:
        return None

    if text[:4].casefold() != "isbn":
        return None

    rest = text[4:]
    if not rest or not rest[0].isspace():
        return None

    extracted = rest.strip()
    return extracted or None


def get_template_param_by_name(template: Any, target_name: str) -> Any | None:
    if not (target := target_name.strip().casefold()):
        return None

    return next(
        (param for param in template.params
         if str(param.name).strip().casefold() == target),
        None,
    )


def is_cite_book_template(template: Any) -> bool:
    return canonicalise_title_fragment(str(template.name)) == "citebook"


def normalise_cite_book_isbn_templates(
    code: Any,
    groups: list[Group],
    convert_10_to_13: bool,
    template_name_aliases: frozenset[str],
) -> int:
    changed = 0
    templates_found = list(
        code.filter_templates(
            matches=lambda template: canonicalise_title_fragment(
                str(template.name)) in template_name_aliases))

    for template in templates_found:
        isbn_param = get_template_param_by_name(template, "isbn")
        if isbn_param is None:
            continue

        raw_value = str(isbn_param.value).strip()
        if not raw_value:
            continue

        normalised_value = normalise_if_isbn(
            raw_value,
            groups,
            convert_10_to_13,
        )
        if normalised_value is None or normalised_value == raw_value:
            continue

        isbn_param.value = normalised_value
        changed += 1

    return changed


def build_isbn_template_node(
    code_value: str,
    label_value: str | None,
    template_name: str = "ISBN",
) -> Any:
    # Keep the template name as provided (preserve user's casing).
    if label_value is None:
        return mwparserfromhell.parse(
            f"{{{{{template_name}|{code_value}}}}}").nodes[0]
    return mwparserfromhell.parse(
        f"{{{{{template_name}|{code_value}|{label_value}}}}}").nodes[0]


def normalise_if_isbn(
    raw_value: str,
    groups: list[Group],
    convert_10_to_13: bool,
) -> str | None:
    key = isbn_equivalence_key(raw_value)
    if key is None:
        return None
    return try_normalise_template_value(raw_value, groups, convert_10_to_13)


def replace_booksource_links_with_isbn_templates(
    code: Any,
    groups: list[Group],
    convert_10_to_13: bool,
    template_preferred_map: dict[str, str] | None = None,
) -> int:
    changed = 0
    wikilinks = list(code.filter_wikilinks())

    for wikilink in wikilinks:
        link_isbn_raw = extract_booksource_isbn_from_title(wikilink.title)
        if link_isbn_raw is None:
            continue

        normalised_link_isbn = normalise_if_isbn(
            link_isbn_raw,
            groups,
            convert_10_to_13,
        )
        # If the title part is not a valid ISBN, do not touch this link.
        if normalised_link_isbn is None:
            continue

        if wikilink.text is None:
            continue

        label_raw = str(wikilink.text).strip()
        if not label_raw:
            continue

        label_isbn_raw = split_isbn_prefixed_label(label_raw)

        # choose preferred template name: prefer explicit 'isbn' from mapping,
        # otherwise pick any mapping value, otherwise default to 'ISBN'.
        preferred_template = "ISBN"
        if template_preferred_map:
            if (pt := template_preferred_map.get("isbn")):
                preferred_template = pt
            elif vals := list(template_preferred_map.values()):
                preferred_template = vals[0]

        if label_isbn_raw is not None:
            label_isbn_normalised = normalise_if_isbn(
                label_isbn_raw,
                groups,
                convert_10_to_13,
            )
            if (label_isbn_normalised is not None
                    and are_semantically_equal_isbns(link_isbn_raw,
                                                     label_isbn_raw)):
                replacement = build_isbn_template_node(normalised_link_isbn,
                                                       None,
                                                       preferred_template)
            else:
                replacement = build_isbn_template_node(
                    normalised_link_isbn,
                    label_isbn_normalised
                    if label_isbn_normalised is not None else label_raw,
                    preferred_template,
                )
        else:
            label_isbn_normalised = normalise_if_isbn(
                label_raw,
                groups,
                convert_10_to_13,
            )
            replacement = build_isbn_template_node(
                normalised_link_isbn,
                label_isbn_normalised
                if label_isbn_normalised is not None else label_raw,
                preferred_template,
            )

        code.replace(wikilink, replacement)
        changed += 1

    return changed


def normalise_isbn_templates(
    text: str,
    xml_path: Path,
    convert_10_to_13: bool = False,
    rehyphenate_equal_label: bool = False,
    template_preferred_map: dict[str, str] | None = None,
) -> tuple[str, int]:
    groups = load_groups(xml_path)
    changed = 0
    template_name_aliases = parse_template_name_aliases(template_preferred_map)

    code = mwparserfromhell.parse(text)
    changed += normalise_cite_book_isbn_templates(
        code,
        groups,
        convert_10_to_13,
        template_name_aliases,
    )
    changed += replace_booksource_links_with_isbn_templates(
        code,
        groups,
        convert_10_to_13,
        template_preferred_map,
    )
    templates_found = list(
        code.filter_templates(matches=lambda t: canonicalise_title_fragment(
            str(t.name)) in template_name_aliases))

    for template in templates_found:
        if not template.has("1"):
            continue

        param1 = template.get("1")
        code_str = str(param1.value).strip()

        normalised_1 = try_normalise_template_value(
            code_str,
            groups,
            convert_10_to_13,
        )
        if normalised_1 is None:
            continue

        output_label = get_template_label_value(
            template,
            groups,
            convert_10_to_13,
        )

        equal_isbn = are_semantically_equal_isbns(code_str, output_label)
        if rehyphenate_equal_label and equal_isbn:
            changed += 1
            # Only rename to the user's preferred ISBNT template if they
            # explicitly provided one; otherwise keep the original template
            # name.
            if template_preferred_map:
                preferred_isbnt = template_preferred_map.get("isbnt")
            else:
                preferred_isbnt = None
            if preferred_isbnt:
                template.name = preferred_isbnt
            template.get("1").value = normalised_1
            if template.has("2"):
                template.remove("2")
            continue

        output_code = normalised_1

        original_code = code_str
        original_label = str(
            template.get("2").value).strip() if template.has("2") else None

        if output_code == original_code and output_label == original_label:
            continue

        changed += 1
        template.get("1").value = output_code
        update_template_label(template, output_label)

    return str(code), changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite ISBN templates in wikitext "
        "using ISBN range XML rules.")
    parser.add_argument(
        "--xml",
        default="RangeMessage.xml",
        help="Path to ISBN range XML file.",
    )
    parser.add_argument(
        "--text-file",
        required=True,
        help="Path to wikitext file to rewrite ISBN templates.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Write output back to --text-file instead of printing.",
    )
    parser.add_argument(
        "-to13",
        "--to13",
        action="store_true",
        help="Convert ISBN-10 to ISBN-13 before output.",
    )
    parser.add_argument(
        "--rehyphenate-equal-label",
        action="store_true",
        help=(
            "When template parameter 1 and 2 are semantically the same ISBN, "
            "replace the template with {{ISBNT|$1}} and keep parameter 1 "
            "hyphenated."),
    )
    parser.add_argument(
        "-format",
        action="store_true",
        help="Compatibility flag; formatting is always enabled.",
    )

    args = parser.parse_args()

    xml_path = Path(args.xml)
    try:
        input_text = Path(args.text_file).read_text(encoding="utf-8")
        output_text, changed = normalise_isbn_templates(
            input_text,
            xml_path,
            convert_10_to_13=args.to13,
            rehyphenate_equal_label=args.rehyphenate_equal_label,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.in_place:
        Path(args.text_file).write_text(output_text, encoding="utf-8")
    else:
        print(output_text)

    print(f"Template replacements: {changed}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
