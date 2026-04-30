#!/usr/bin/env python3
"""Local web app for comparing manuscript transcriptions.

This app scans locally stored manuscript files, groups them by Bible book, and
serves an interactive comparison page. It prefers extracted `.txt` files but
can still fall back to TEI XML files when needed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import threading
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List
from urllib.parse import parse_qs, urlparse

from extract_tei_text import build_output_lines


BOOK_NAMES = {
    "Matt": "Matthew",
    "Mark": "Mark",
    "Luke": "Luke",
    "John": "John",
    "Acts": "Acts",
    "Rom": "Romans",
    "1Cor": "1 Corinthians",
    "2Cor": "2 Corinthians",
    "Gal": "Galatians",
    "Eph": "Ephesians",
    "Phil": "Philippians",
    "Col": "Colossians",
    "1Thess": "1 Thessalonians",
    "2Thess": "2 Thessalonians",
    "1Tim": "1 Timothy",
    "2Tim": "2 Timothy",
    "Titus": "Titus",
    "Phlm": "Philemon",
    "Heb": "Hebrews",
    "Jas": "James",
    "1Pet": "1 Peter",
    "2Pet": "2 Peter",
    "1John": "1 John",
    "2John": "2 John",
    "3John": "3 John",
    "Jude": "Jude",
    "Rev": "Revelation",
}

NT_VERSE_COUNTS = {
    "Matt": [25, 23, 17, 25, 48, 34, 29, 34, 38, 42, 30, 50, 58, 36, 39, 28, 27, 35, 30, 34, 46, 46, 39, 51, 46, 75, 66, 20],
    "Mark": [45, 28, 35, 41, 43, 56, 37, 38, 50, 52, 33, 44, 37, 72, 47, 20],
    "Luke": [80, 52, 38, 44, 39, 49, 50, 56, 62, 42, 54, 59, 35, 35, 32, 31, 37, 43, 48, 47, 38, 71, 56, 53],
    "John": [51, 25, 36, 54, 47, 71, 53, 59, 41, 42, 57, 50, 38, 31, 27, 33, 26, 40, 42, 31, 25],
    "Acts": [26, 47, 26, 37, 42, 15, 60, 40, 43, 48, 30, 25, 52, 28, 41, 40, 34, 28, 41, 38, 40, 30, 35, 27, 27, 32, 44, 31],
    "Rom": [32, 29, 31, 25, 21, 23, 25, 39, 33, 21, 36, 21, 14, 23, 33, 27],
    "1Cor": [31, 16, 23, 21, 13, 20, 40, 13, 27, 33, 34, 31, 13, 40, 58, 24],
    "2Cor": [24, 17, 18, 18, 21, 18, 16, 24, 15, 18, 33, 21, 14],
    "Gal": [24, 21, 29, 31, 26, 18],
    "Eph": [23, 22, 21, 32, 33, 24],
    "Phil": [30, 30, 21, 23],
    "Col": [29, 23, 25, 18],
    "1Thess": [10, 20, 13, 18, 28],
    "2Thess": [12, 17, 18],
    "1Tim": [20, 15, 16, 16, 25, 21],
    "2Tim": [18, 26, 17, 22],
    "Titus": [16, 15, 15],
    "Phlm": [25],
    "Heb": [14, 18, 19, 16, 14, 20, 28, 13, 28, 39, 40, 29, 25],
    "Jas": [27, 26, 18, 17, 20],
    "1Pet": [25, 25, 22, 19, 14],
    "2Pet": [21, 22, 18],
    "1John": [10, 29, 24, 21, 21],
    "2John": [13],
    "3John": [15],
    "Jude": [25],
    "Rev": [20, 29, 22, 11, 14, 17, 17, 13, 21, 11, 19, 17, 18, 20, 8, 21, 18, 24, 21, 15, 27, 21],
}

WEB_DIR = Path(__file__).resolve().parent / "webapp"
DEFAULT_TXT_ROOT = Path(__file__).resolve().parent / "epistulae_data" / "txt"
DEFAULT_XML_ROOT = Path(__file__).resolve().parent / "epistulae_data" / "xml"
DEFAULT_BUNDLE_PATH = Path(__file__).resolve().parent / "epistulae_data" / "manuscripts.json.gz"
ALT_BUNDLE_PATH = DEFAULT_TXT_ROOT / "manuscripts.json.gz"


@dataclass(frozen=True)
class ManuscriptRecord:
    book: str
    manuscript_id: str
    file_path: Path
    filename: str


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def parse_xml_metadata(xml_path: Path) -> ManuscriptRecord | None:
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return None

    book = ""
    manuscript_id = ""
    for el in root.iter():
        if not book and local_name(el.tag) == "div" and el.get("type") == "book" and el.get("n"):
            book = el.get("n", "").strip()
        if not manuscript_id and local_name(el.tag) == "title" and el.get("n"):
            manuscript_id = el.get("n", "").strip()
        if book and manuscript_id:
            break

    if not book or not manuscript_id:
        return None

    return ManuscriptRecord(
        book=book,
        manuscript_id=manuscript_id,
        file_path=xml_path.resolve(),
        filename=xml_path.name,
    )


def parse_txt_metadata(txt_path: Path) -> ManuscriptRecord | None:
    manuscript_id = ""
    stem_parts = txt_path.stem.split("_")
    if len(stem_parts) >= 4:
        manuscript_id = stem_parts[-2].strip()

    try:
        with txt_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                label, _, book = parse_extracted_line(line)
                if not label or not book:
                    continue
                if manuscript_id and book in NT_VERSE_COUNTS:
                    return ManuscriptRecord(
                        book=book,
                        manuscript_id=manuscript_id,
                        file_path=txt_path.resolve(),
                        filename=txt_path.name,
                    )
                break
    except OSError:
        return None

    return None


def parse_extracted_line(line: str) -> tuple[str, str, str]:
    tokens = line.split()
    if not tokens:
        return "", "", ""

    first = tokens[0]
    if ".inscriptio" in first or ".subscriptio" in first:
        return first, " ".join(tokens[1:]), first.split(".", 1)[0]

    if len(tokens) > 1 and ":" in tokens[1]:
        return f"{tokens[0]} {tokens[1]}", " ".join(tokens[2:]), tokens[0]

    return first, " ".join(tokens[1:]), first


def canonical_labels_for_book(book: str) -> List[str]:
    counts = NT_VERSE_COUNTS.get(book, [])
    labels = [f"{book}.inscriptio"]
    for chapter_index, verse_count in enumerate(counts, start=1):
        for verse in range(1, verse_count + 1):
            labels.append(f"{book} {chapter_index}:{verse}")
    labels.append(f"{book}.subscriptio")
    return labels


def tokenize(text: str) -> List[str]:
    return [token for token in text.split() if token]


def lcs_ops(tokens_a: List[str], tokens_b: List[str]) -> List[dict]:
    m = len(tokens_a)
    n = len(tokens_b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if tokens_a[i - 1] == tokens_b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    ops: List[dict] = []
    i = m
    j = n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and tokens_a[i - 1] == tokens_b[j - 1]:
            ops.append({"type": "eq", "token": tokens_a[i - 1]})
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            ops.append({"type": "add", "token": tokens_b[j - 1]})
            j -= 1
        else:
            ops.append({"type": "del", "token": tokens_a[i - 1]})
            i -= 1

    ops.reverse()
    return ops


class LibraryIndex:
    def __init__(self, library_root: Path):
        self.library_root = library_root.resolve()
        self._records_by_book: Dict[str, List[ManuscriptRecord]] = {}
        self._bundle_lines_by_key: Dict[tuple[str, str], tuple[str, ...]] = {}
        self.refresh()

    def refresh(self) -> None:
        if self.library_root.is_file() and self.library_root.suffixes[-2:] == [".json", ".gz"]:
            self._load_bundle()
            return
        if self.library_root.is_file() and self.library_root.suffix.lower() == ".json":
            self._load_bundle()
            return

        records_by_book: Dict[str, Dict[str, ManuscriptRecord]] = {}
        self._bundle_lines_by_key = {}
        for source_path in sorted(self.library_root.rglob("*")):
            if not source_path.is_file():
                continue
            if "The Ai's output" in source_path.parts:
                continue
            if source_path.suffix.lower() == ".txt":
                record = parse_txt_metadata(source_path)
            elif source_path.suffix.lower() == ".xml":
                record = parse_xml_metadata(source_path)
            else:
                continue
            if record is None or record.book not in NT_VERSE_COUNTS:
                continue
            records_by_book.setdefault(record.book, {})
            existing = records_by_book[record.book].get(record.manuscript_id)
            if existing is None or len(str(record.file_path)) < len(str(existing.file_path)):
                records_by_book[record.book][record.manuscript_id] = record

        self._records_by_book = {
            book: sorted(book_records.values(), key=lambda rec: rec.manuscript_id)
            for book, book_records in records_by_book.items()
        }

    def _load_bundle(self) -> None:
        if self.library_root.suffixes[-2:] == [".json", ".gz"]:
            raw_text = gzip.decompress(self.library_root.read_bytes()).decode("utf-8")
        else:
            raw_text = self.library_root.read_text(encoding="utf-8")

        payload = json.loads(raw_text)
        records_by_book: Dict[str, Dict[str, ManuscriptRecord]] = {}
        bundle_lines_by_key: Dict[tuple[str, str], tuple[str, ...]] = {}

        for item in payload.get("manuscripts", []):
            book = item.get("book", "").strip()
            manuscript_id = item.get("manuscript_id", "").strip()
            filename = item.get("filename", "").strip() or f"{manuscript_id}_{book}.txt"
            lines = tuple(line.strip() for line in item.get("lines", []) if str(line).strip())
            if not book or not manuscript_id or book not in NT_VERSE_COUNTS or not lines:
                continue

            record = ManuscriptRecord(
                book=book,
                manuscript_id=manuscript_id,
                file_path=self.library_root,
                filename=filename,
            )
            records_by_book.setdefault(book, {})[manuscript_id] = record
            bundle_lines_by_key[(book, manuscript_id)] = lines

        self._records_by_book = {
            book: sorted(book_records.values(), key=lambda rec: rec.manuscript_id)
            for book, book_records in records_by_book.items()
        }
        self._bundle_lines_by_key = bundle_lines_by_key

    def books(self) -> List[dict]:
        return [
            {
                "code": book,
                "name": BOOK_NAMES.get(book, book),
                "manuscriptCount": len(records),
            }
            for book, records in sorted(self._records_by_book.items(), key=lambda item: BOOK_NAMES.get(item[0], item[0]))
        ]

    def manuscripts(self, book: str) -> List[dict]:
        return [
            {
                "id": record.manuscript_id,
                "label": record.manuscript_id,
                "filename": record.filename,
            }
            for record in self._records_by_book.get(book, [])
        ]

    def manuscript_record(self, book: str, manuscript_id: str) -> ManuscriptRecord | None:
        for record in self._records_by_book.get(book, []):
            if record.manuscript_id == manuscript_id:
                return record
        return None

    @lru_cache(maxsize=512)
    def extracted_lines(self, file_path: str) -> tuple[str, ...]:
        path = Path(file_path)
        if path.suffix.lower() == ".txt":
            return tuple(
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )

        root = ET.parse(file_path).getroot()
        return tuple(build_output_lines(root))

    def comparison_payload(self, book: str, manuscript_a: str, manuscript_b: str) -> dict:
        if book not in NT_VERSE_COUNTS:
            raise ValueError(f"Unsupported or unavailable book: {book}")

        record_a = self.manuscript_record(book, manuscript_a)
        record_b = self.manuscript_record(book, manuscript_b)
        if record_a is None or record_b is None:
            raise ValueError("One or both manuscripts are not available for that book.")

        bundle_key_a = (record_a.book, record_a.manuscript_id)
        bundle_key_b = (record_b.book, record_b.manuscript_id)
        if bundle_key_a in self._bundle_lines_by_key and bundle_key_b in self._bundle_lines_by_key:
            lines_a = list(self._bundle_lines_by_key[bundle_key_a])
            lines_b = list(self._bundle_lines_by_key[bundle_key_b])
        else:
            lines_a = list(self.extracted_lines(str(record_a.file_path)))
            lines_b = list(self.extracted_lines(str(record_b.file_path)))

        map_a = {}
        map_b = {}
        for line in lines_a:
            label, text, _ = parse_extracted_line(line)
            if label:
                map_a[label] = text
        for line in lines_b:
            label, text, _ = parse_extracted_line(line)
            if label:
                map_b[label] = text

        rows = []
        for idx, label in enumerate(canonical_labels_for_book(book), start=1):
            has_text_a = label in map_a
            has_text_b = label in map_b
            text_a = map_a.get(label, "")
            text_b = map_b.get(label, "")
            tokens_a = tokenize(text_a)
            tokens_b = tokenize(text_b)
            ops = lcs_ops(tokens_a, tokens_b)
            shared_words = sum(1 for op in ops if op["type"] == "eq")
            added_words = sum(1 for op in ops if op["type"] == "add")
            deleted_words = sum(1 for op in ops if op["type"] == "del")
            comparison_size = max(len(tokens_a), len(tokens_b))
            has_any_text = has_text_a or has_text_b
            has_both_text = has_text_a and has_text_b
            word_agreement = round((shared_words / comparison_size) * 100) if comparison_size else None

            rows.append(
                {
                    "line": idx,
                    "label": label,
                    "textA": text_a,
                    "textB": text_b,
                    "hasTextA": has_text_a,
                    "hasTextB": has_text_b,
                    "hasAnyText": has_any_text,
                    "hasBothText": has_both_text,
                    "isDiff": has_any_text and (text_a != text_b or has_text_a != has_text_b),
                    "ops": ops,
                    "sharedWords": shared_words,
                    "addedWords": added_words,
                    "deletedWords": deleted_words,
                    "totalWordsA": len(tokens_a),
                    "totalWordsB": len(tokens_b),
                    "comparisonSize": comparison_size,
                    "wordAgreement": word_agreement,
                }
            )

        comparable_rows = [row for row in rows if row["hasAnyText"]]
        word_comparable_rows = [row for row in rows if row["hasBothText"]]
        total_words_a = sum(row["totalWordsA"] for row in rows)
        total_words_b = sum(row["totalWordsB"] for row in rows)
        comparable_words_a = sum(row["totalWordsA"] for row in word_comparable_rows)
        comparable_words_b = sum(row["totalWordsB"] for row in word_comparable_rows)
        matching_words = sum(row["sharedWords"] for row in word_comparable_rows)
        different_words = sum(row["addedWords"] + row["deletedWords"] for row in word_comparable_rows)
        total_verses = len(comparable_rows)
        different_verses = sum(1 for row in comparable_rows if row["isDiff"])
        matching_verses = total_verses - different_verses
        word_agreement = (
            round((2 * matching_words / (comparable_words_a + comparable_words_b)) * 100)
            if comparable_words_a + comparable_words_b
            else 100
        )
        verse_agreement = round((matching_verses / total_verses) * 100) if total_verses else 0

        return {
            "book": book,
            "bookName": BOOK_NAMES.get(book, book),
            "manuscriptA": {
                "id": record_a.manuscript_id,
                "filename": record_a.filename,
            },
            "manuscriptB": {
                "id": record_b.manuscript_id,
                "filename": record_b.filename,
            },
            "rows": rows,
            "stats": {
                "totalWordsA": total_words_a,
                "totalWordsB": total_words_b,
                "matchingWords": matching_words,
                "differentWords": different_words,
                "wordAgreement": word_agreement,
                "totalVerses": total_verses,
                "matchingVerses": matching_verses,
                "differentVerses": different_verses,
                "verseAgreement": verse_agreement,
            },
        }


class AppHandler(BaseHTTPRequestHandler):
    library: LibraryIndex

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self.serve_file(WEB_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self.serve_file(WEB_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/api/books":
            self.send_json({"books": self.library.books()})
            return
        if parsed.path == "/api/manuscripts":
            query = parse_qs(parsed.query)
            book = query.get("book", [""])[0]
            self.send_json({"manuscripts": self.library.manuscripts(book)})
            return
        if parsed.path == "/api/compare":
            query = parse_qs(parsed.query)
            book = query.get("book", [""])[0]
            manuscript_a = query.get("a", [""])[0]
            manuscript_b = query.get("b", [""])[0]
            try:
                payload = self.library.comparison_payload(book, manuscript_a, manuscript_b)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            self.send_json(payload)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def parse_args() -> argparse.Namespace:
    default_port = int(os.environ.get("PORT", "8000"))
    parser = argparse.ArgumentParser(description="Run the manuscript comparison web app.")
    parser.add_argument(
        "--xml-root",
        default=str(
            ALT_BUNDLE_PATH
            if ALT_BUNDLE_PATH.exists()
            else DEFAULT_BUNDLE_PATH
            if DEFAULT_BUNDLE_PATH.exists()
            else DEFAULT_TXT_ROOT
            if DEFAULT_TXT_ROOT.exists()
            else DEFAULT_XML_ROOT
            if DEFAULT_XML_ROOT.exists()
            else Path(__file__).resolve().parent
        ),
        help="Folder or bundle file containing manuscript .txt or .xml files.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0" if os.environ.get("PORT") else "127.0.0.1",
        help="Host interface for the local server.",
    )
    parser.add_argument("--port", type=int, default=default_port, help="Port for the local server.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open the app in a browser.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    library_path = Path(args.xml_root)
    library = LibraryIndex(library_path)
    if not library.books():
        if not library_path.exists():
            print(f"Manuscript library path was not found: {library_path.resolve()}")
        else:
            print(f"No manuscript data was found in: {library.library_root}")
        print("Expected either a manuscript bundle (.json.gz), a folder of converted .txt files, or a folder of XML files.")
        return 1

    AppHandler.library = library
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Serving manuscript web app at {url}")
    print(f"Using manuscript library: {library.library_root}")
    print(f"Indexed books: {', '.join(book['name'] for book in library.books())}")

    if not args.no_browser and not os.environ.get("PORT"):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
