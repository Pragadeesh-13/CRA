"""Import and search an operator-provided resolved-ticket CSV with SQLite FTS5."""

import argparse
import csv
import os
import re
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

CORPUS_SOURCE = "local_resolved_ticket_corpus"
_COLUMNS = ("ticket_id", "issue_description", "category", "status", "resolution_notes")
_STOP_WORDS = frozenset("a an and are can could did do for had has have how i in is it me my of on or our please the this to was we were what with you your".split())


def import_corpus(source: Path, destination: Path, *, replace: bool = False) -> int:
    """Build a complete index before publishing it, leaving any existing index intact on failure."""
    if source.resolve() == destination.resolve():
        raise ValueError("The source CSV and destination index must be different files.")
    if destination.exists() and not replace:
        raise FileExistsError("The index already exists. Use --replace to rebuild it.")
    fd, temporary_name = tempfile.mkstemp(prefix=".precedents-", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    count = 0
    try:
        with source.open(encoding="utf-8-sig", newline="") as stream, closing(sqlite3.connect(temporary)) as db:
            rows = csv.DictReader(stream, strict=True)
            columns = Counter(rows.fieldnames or [])
            duplicates = sorted(name for name, count in columns.items() if count > 1)
            if duplicates:
                raise ValueError(f"Duplicate CSV columns: {', '.join(duplicates)}")
            missing = set(_COLUMNS) - columns.keys()
            if missing:
                raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
            try:
                db.execute("CREATE VIRTUAL TABLE cases_fts USING fts5(issue_description, tokenize='unicode61')")
            except sqlite3.OperationalError as exc:
                if "no such module" in str(exc):
                    raise ValueError("This Python build requires SQLite with FTS5 support.") from exc
                raise
            db.execute("""
                CREATE TABLE cases (
                    ticket_id INTEGER PRIMARY KEY, issue_description TEXT NOT NULL,
                    category TEXT NOT NULL, status TEXT NOT NULL, resolution_notes TEXT NOT NULL
                )
            """)
            with db:
                for row in rows:
                    if None in row or None in row.values():
                        raise ValueError(f"CSV line {rows.line_num} has a different number of fields than the header.")
                    ticket_id = (row["ticket_id"] or "").strip()
                    description = row["issue_description"] or ""
                    category = (row["category"] or "").strip()
                    status = (row["status"] or "").strip()
                    notes = row["resolution_notes"] or ""
                    if status.casefold() not in {"resolved", "closed"} or not notes.strip():
                        continue
                    if (
                        not re.fullmatch(r"[0-9]{1,19}", ticket_id) or not 0 < int(ticket_id) < 2**63
                        or not description.strip() or not category
                    ):
                        raise ValueError(f"Invalid resolved ticket record at CSV line {rows.line_num}.")
                    try:
                        db.execute("INSERT INTO cases VALUES (?, ?, ?, ?, ?)", (ticket_id, description, category, status, notes))
                    except sqlite3.IntegrityError as exc:
                        raise ValueError(f"Duplicate ticket ID at CSV line {rows.line_num}.") from exc
                    count += 1
                db.execute("INSERT INTO cases_fts(rowid, issue_description) SELECT ticket_id, issue_description FROM cases")
        if replace:
            temporary.replace(destination)
        else:
            os.link(temporary, destination)
        return count
    finally:
        temporary.unlink(missing_ok=True)


def search_corpus(index: Path, complaint: str) -> list[dict]:
    # Quote individual words so complaint text remains data even when it contains FTS operators.
    terms = dict.fromkeys(word for word in re.findall(r"[^\W_]+", complaint[:4000].casefold())
                          if len(word) > 1 and word not in _STOP_WORDS)
    query = " OR ".join(f'"{term}"' for term in list(terms)[:24])
    if not query:
        return []
    with closing(sqlite3.connect(f"{index.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("""
            SELECT ticket_id, cases.issue_description AS description, category, status, resolution_notes
            FROM cases_fts JOIN cases ON cases.ticket_id = cases_fts.rowid
            WHERE cases_fts MATCH ?
            ORDER BY bm25(cases_fts), ticket_id LIMIT 3
        """, (query,))
        cases = []
        for row in rows:
            case = dict(row)
            # Browsers cannot represent every SQLite INTEGER as a JSON number.
            if case["ticket_id"] > 2**53 - 1:
                case["ticket_id"] = str(case["ticket_id"])
            cases.append({**case, "resolution": row["resolution_notes"], "source": CORPUS_SOURCE})
        return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Resolved-ticket CSV in the dataset_details.md format")
    parser.add_argument("index", type=Path, help="Destination SQLite file")
    parser.add_argument("--replace", action="store_true", help="Replace an existing index only after a successful rebuild")
    args = parser.parse_args()
    try:
        count = import_corpus(args.csv, args.index, replace=args.replace)
    except (OSError, sqlite3.Error, ValueError, csv.Error) as exc:
        parser.exit(1, f"Import failed: {exc}\n")
    print(f"Indexed {count} resolved tickets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
