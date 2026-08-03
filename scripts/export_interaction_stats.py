"""Export local LineHelper interaction analytics without touching RAG memory."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from linehelper.analytics.interaction_store import InteractionStore  # noqa: E402
from linehelper.analytics.statistics import aggregate_interaction_stats  # noqa: E402


DEFAULT_DB = PROJECT_ROOT / "data" / "analytics" / "linehelper_interactions.db"


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    store = InteractionStore(args.db)
    if not args.db.exists():
        print(f"Analytics DB not found: {args.db}", file=sys.stderr)
        return 1
    store.ensure_schema()
    from_utc, to_utc = _date_range(args)
    cleanup = None
    if args.cleanup_expired:
        cleanup = store.cleanup_expired(retention_days=args.retention_days)

    interactions = store.fetch_rows(
        "interactions",
        from_utc=from_utc,
        to_utc=to_utc,
    )
    interaction_ids = {str(item["interaction_id"]) for item in interactions}
    feedback = [
        item
        for item in store.fetch_rows(
            "interaction_feedback",
            from_utc=from_utc,
            to_utc=to_utc,
        )
        if str(item["interaction_id"]) in interaction_ids
    ]
    sources = [
        item
        for item in store.fetch_rows("interaction_sources")
        if str(item["interaction_id"]) in interaction_ids
    ]
    summary = aggregate_interaction_stats(interactions, feedback, sources)
    summary["range"] = {
        "from_utc": from_utc.isoformat() if from_utc else None,
        "to_utc_exclusive": to_utc.isoformat() if to_utc else None,
    }
    if cleanup is not None:
        summary["cleanup"] = {
            "performed": cleanup.performed,
            "deleted_interactions": cleanup.deleted_interactions,
            "cutoff_utc": (
                cleanup.cutoff_utc.isoformat() if cleanup.cutoff_utc else None
            ),
            "error": cleanup.error,
        }

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output_csv_dir:
        _write_csv_exports(
            args.output_csv_dir,
            interactions=interactions,
            feedback=feedback,
            sources=sources,
            summary=summary,
        )
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-csv-dir", type=Path)
    parser.add_argument("--cleanup-expired", action="store_true")
    parser.add_argument("--retention-days", type=int, default=90)
    return parser.parse_args(argv)


def _date_range(args: argparse.Namespace) -> tuple[datetime | None, datetime | None]:
    to_utc = _parse_date(args.to_date, end=True) if args.to_date else None
    if args.from_date:
        from_utc = _parse_date(args.from_date, end=False)
    elif args.days > 0:
        from_utc = datetime.now(timezone.utc) - timedelta(days=args.days)
    else:
        from_utc = None
    return from_utc, to_utc


def _parse_date(value: str, *, end: bool) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    if end and len(value) == 10:
        parsed += timedelta(days=1)
    return parsed


def _write_csv_exports(
    output_dir: Path,
    *,
    interactions: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(output_dir / "interactions.csv", interactions)
    _write_rows(output_dir / "feedback.csv", feedback)
    _write_rows(output_dir / "sources.csv", sources)
    _write_rows(
        output_dir / "summary.csv",
        [
            {
                "metric": key,
                "value": (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                ),
            }
            for key, value in summary.items()
        ],
    )


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
