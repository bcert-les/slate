"""
Filter top-level fields in JSON produced by api/list_assets.py.

Reads the standard export shape: { "organizationId", "count", "assets": [...] }.
Writes the same shape with each asset reduced to selected keys, plus optional CSV
aligned to --include column order (or sorted union if only --exclude is used).

Run from repository root:
  python tools/filter_assets_output.py output/assets_org_362.json \
    --include _id,name,ipAddress,platform,os \
    --json-out output/assets_core.json \
    --csv-out output/assets_core.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys


def _parse_fields(s: str | None) -> list[str]:
    if not s or not str(s).strip():
        return []
    return [p.strip() for p in str(s).split(",") if p.strip()]


def _cell_csv(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def load_enumerate_assets_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Root JSON value must be an object")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError('Expected key "assets" with a JSON array')
    return data


def filter_asset(
    row: dict,
    include: list[str],
    exclude: set[str],
) -> dict:
    if include:
        out = {k: row.get(k) for k in include}
    else:
        out = dict(row)
    for k in exclude:
        out.pop(k, None)
    return out


def filter_assets(
    assets: list,
    include: list[str],
    exclude: list[str],
) -> list[dict]:
    ex = set(exclude)
    out: list[dict] = []
    for row in assets:
        if not isinstance(row, dict):
            continue
        out.append(filter_asset(row, include, ex))
    return out


def write_json(path: str, source: dict, filtered_assets: list[dict]) -> None:
    payload = {
        "organizationId": source.get("organizationId"),
        "count": len(filtered_assets),
        "assets": filtered_assets,
    }
    if "organizationName" in source:
        payload["organizationName"] = source["organizationName"]
    _ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def write_csv(path: str, filtered_assets: list[dict], include: list[str]) -> None:
    _ensure_parent_dir(path)
    if not filtered_assets:
        with open(path, "w", encoding="utf-8", newline="") as f:
            pass
        return

    if include:
        fieldnames = list(include)
    else:
        keys: set[str] = set()
        for row in filtered_assets:
            keys.update(row.keys())
        fieldnames = sorted(keys)

    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in filtered_assets:
            writer.writerow({k: _cell_csv(row.get(k)) for k in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter enumerate_assets.py JSON exports by top-level asset fields.",
    )
    parser.add_argument(
        "input_json",
        help="Path to JSON from enumerate_assets.py (organizationId + assets[])",
    )
    parser.add_argument(
        "--include",
        metavar="FIELDS",
        help="Comma-separated top-level asset keys to keep (whitelist). "
        "If omitted, all keys are kept before --exclude.",
    )
    parser.add_argument(
        "--exclude",
        metavar="FIELDS",
        help="Comma-separated top-level asset keys to remove after include (if any).",
    )
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        help="Write filtered JSON here (recommended)",
    )
    parser.add_argument(
        "--csv-out",
        metavar="PATH",
        help="Write filtered CSV here",
    )
    args = parser.parse_args()

    include = _parse_fields(args.include)
    exclude = _parse_fields(args.exclude)

    if not include and not exclude:
        print("Specify at least one of --include or --exclude.", file=sys.stderr)
        sys.exit(1)
    if not args.json_out and not args.csv_out:
        print("Specify at least one of --json-out or --csv-out.", file=sys.stderr)
        sys.exit(1)

    try:
        source = load_enumerate_assets_json(args.input_json)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Error reading input: {e}", file=sys.stderr)
        sys.exit(2)

    assets = source.get("assets") or []
    filtered = filter_assets(assets, include, exclude)

    if args.json_out:
        write_json(args.json_out, source, filtered)
        print(f"Wrote JSON: {args.json_out} ({len(filtered)} assets)")
    if args.csv_out:
        write_csv(args.csv_out, filtered, include)
        print(f"Wrote CSV:  {args.csv_out}")


if __name__ == "__main__":
    main()
