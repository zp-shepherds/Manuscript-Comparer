#!/usr/bin/env python3
"""Extract continuous verse/unit text from a TEI XML transcription.

Usage:
    python extract_tei_text.py input.xml -o output.txt

This script is intentionally commented for coursework clarity.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List


# TEI tags that represent layout/editorial structure rather than lexical words.
SKIP_TAGS = {
    "lb",  # line break
    "pb",  # page break
    "cb",  # column break
    "note",
    "fw",
    "gap",
    "add",
    "del",
    "supplied",
    "choice",
    "orig",
    "reg",
    "sic",
    "corr",
    "unclear",
    "pc",  # punctuation container in many TEI files
}


def local_name(tag: str) -> str:
    """Return the local tag name whether or not a namespace is present."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def normalize_space(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip edges."""
    return " ".join(text.split())


def normalize_token(text: str) -> str:
    """Remove internal whitespace so line-wrapped words stay a single token."""
    return "".join(text.split())


def format_identifier(raw: str) -> str:
    """Convert common TEI identifiers to a readable 'Book C:V' style."""
    raw = raw.strip()

    # Example handled: Rom.1.1 -> Rom 1:1
    match = re.fullmatch(r"([1-3]?[A-Za-z]+)[._\-](\d+)[._\-:](\d+)", raw)
    if match:
        book, chapter, verse = match.groups()
        return f"{book} {int(chapter)}:{int(verse)}"

    # Example handled: epistle_001_001 -> epistle 1:1
    match = re.fullmatch(r"([A-Za-z]+)_(\d+)_(\d+)", raw)
    if match:
        book, chapter, verse = match.groups()
        return f"{book} {int(chapter)}:{int(verse)}"

    # Fall back to the original identifier if no known pattern matches.
    return raw


def is_special_unit(identifier: str, label: str) -> bool:
    """Return True when an identifier refers to a named paratextual unit."""
    normalized = identifier.strip().lower()
    return normalized == label or normalized.endswith(f".{label}") or normalized.endswith(f"_{label}")


def choose_units(root: ET.Element) -> List[ET.Element]:
    """Pick text units in document order.

    TEI transcriptions commonly mark scripture units as <ab n="...">.
    If no such elements exist, we fall back to <div n="...">.
    """
    ab_units = [el for el in root.iter() if local_name(el.tag) == "ab" and el.get("n")]
    if ab_units:
        return ab_units

    return [el for el in root.iter() if local_name(el.tag) == "div" and el.get("n")]


def extract_words(unit: ET.Element) -> List[str]:
    """Extract lexical words from one TEI unit while skipping structural tags."""
    words: List[str] = []

    for el in unit.iter():
        name = local_name(el.tag)

        # Skip the unit container itself and structural/editorial nodes.
        if el is unit or name in SKIP_TAGS:
            continue

        # In TEI transcriptions, lexical tokens are usually in <w> elements.
        if name == "w":
            token = normalize_token("".join(el.itertext()))
            if token:
                words.append(token)

    return words


def infer_book_identifier(root: ET.Element, units: List[ET.Element]) -> str:
    """Infer the book identifier used for synthetic inscriptio/subscriptio lines."""
    for el in root.iter():
        if local_name(el.tag) == "div" and el.get("type") == "book" and el.get("n"):
            return el.get("n", "").strip()

    for unit in units:
        identifier = unit.get("n", "").strip()
        if not identifier:
            continue
        if "." in identifier:
            return identifier.split(".", 1)[0]
        if "_" in identifier:
            return identifier.split("_", 1)[0]
        return identifier

    return "book"


def build_output_lines(root: ET.Element) -> List[str]:
    """Build output lines formatted as '<identifier> <continuous text>'."""
    units = choose_units(root)
    lines: List[str] = []
    has_inscriptio = False
    has_subscriptio = False

    for unit in units:
        raw_identifier = unit.get("n", "")
        if not raw_identifier:
            continue

        words = extract_words(unit)
        if is_special_unit(raw_identifier, "inscriptio"):
            has_inscriptio = bool(words)
        elif is_special_unit(raw_identifier, "subscriptio"):
            has_subscriptio = bool(words)

        if not words:
            continue

        identifier = format_identifier(raw_identifier)
        lines.append(f"{identifier} {' '.join(words)}")

    book_identifier = infer_book_identifier(root, units)

    if not has_inscriptio:
        lines.insert(0, f"{format_identifier(f'{book_identifier}.inscriptio')} n/a")

    if not has_subscriptio:
        lines.append(f"{format_identifier(f'{book_identifier}.subscriptio')} n/a")

    return lines


def discover_xml_files() -> List[Path]:
    """Find nearby XML files so interactive users can choose from a short menu."""
    search_dirs = [Path.cwd(), Path(__file__).resolve().parent]
    discovered: List[Path] = []
    seen: set[Path] = set()

    for directory in search_dirs:
        for candidate in sorted(directory.glob("*.xml")):
            resolved = candidate.resolve()
            if resolved not in seen:
                discovered.append(resolved)
                seen.add(resolved)

    return discovered


def prompt_for_xml_file() -> Path:
    """Ask the user which XML file to use when no command-line argument is given."""
    xml_files = discover_xml_files()

    if xml_files:
        print("Choose an XML file to process:")
        for index, path in enumerate(xml_files, start=1):
            print(f"  {index}. {path.name}")
        print("Or type a full file path.")

    while True:
        prompt = "XML file number or path: " if xml_files else "Enter the XML file path: "
        raw_value = input(prompt).strip()

        if not raw_value:
            print("Please enter a file number or XML file path.")
            continue

        if xml_files and raw_value.isdigit():
            choice = int(raw_value)
            if 1 <= choice <= len(xml_files):
                return xml_files[choice - 1]
            print("That number is not in the list.")
            continue

        chosen_path = Path(raw_value).expanduser()
        if chosen_path.exists() and chosen_path.is_file():
            return chosen_path

        print(f"Could not find a file at: {chosen_path}")


def prompt_for_output_file(xml_path: Path) -> Path:
    """Ask for an output path, while offering a sensible default."""
    default_output = xml_path.with_name(f"{xml_path.stem}_continuous.txt")
    raw_value = input(
        f"Output text file [{default_output.name}]: "
    ).strip()

    if not raw_value:
        return default_output

    return Path(raw_value).expanduser()


def parse_args() -> argparse.Namespace:
    """Define and parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Extract continuous text from TEI XML with verse/unit identifiers."
    )
    parser.add_argument(
        "xml_file",
        nargs="?",
        help="Path to the TEI XML file to parse",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output .txt path (default: <input_stem>_continuous.txt)",
    )
    return parser.parse_args()


def main() -> int:
    """Entrypoint for command-line execution."""
    args = parse_args()

    xml_path = Path(args.xml_file).expanduser() if args.xml_file else prompt_for_xml_file()
    if not xml_path.exists():
        print(f"Error: file not found: {xml_path}", file=sys.stderr)
        return 1

    if args.output:
        output_path = Path(args.output).expanduser()
    else:
        output_path = prompt_for_output_file(xml_path) if args.xml_file is None else xml_path.with_name(
            f"{xml_path.stem}_continuous.txt"
        )

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except ET.ParseError as exc:
        print(f"Error: could not parse XML ({exc})", file=sys.stderr)
        return 1

    lines = build_output_lines(root)
    output_text = "\n".join(lines)

    output_path.write_text(output_text, encoding="utf-8")
    print(f"Wrote {len(lines)} units to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
