#!/usr/bin/env python3
"""Rewrite ISBN templates in wikitext using ISBN normalization rules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import mwparserfromhell

from isbn_normalise import Group, isbn_equivalence_key, load_groups, normalise_token


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


def normalise_isbn_templates(
    text: str,
    xml_path: Path,
    convert_10_to_13: bool = False,
    rehyphenate_equal_label: bool = False,
) -> tuple[str, int]:
    groups = load_groups(xml_path)
    changed = 0

    code = mwparserfromhell.parse(text)
    templates_found = list(
        code.filter_templates(
            matches=lambda t: str(t.name).strip().lower() == "isbn"))

    for template in templates_found:
        template.name = "ISBN"

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
        output_code = normalised_1
        if rehyphenate_equal_label and equal_isbn:
            output_code = normalised_1.replace("-", "")

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
            "set parameter 1 to non-hyphenated form and keep parameter 2 "
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
