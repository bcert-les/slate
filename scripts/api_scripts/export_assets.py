"""
Download GET /api/public/assets/export (bulk export for the console export flow).

Query parameters are passed through (defaults include filter[organizationIds] for
the given org). The response may be JSON, CSV text, or binary; this script picks
an output encoding from Content-Type unless --out is set explicitly.

Examples:

    python3 scripts/api_scripts/export_assets.py 362
    python3 scripts/api_scripts/export_assets.py 362 --out output/my_export.csv
    python3 scripts/api_scripts/export_assets.py 362 --param format=csv
"""
import argparse
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))

from lib.api_client import api_get, load_config


def _safe_org_slug(org_id: str) -> str:
    s = str(org_id).strip()
    if re.fullmatch(r"[\w.\-]+", s):
        return s
    return re.sub(r"[^\w.\-]+", "_", s) or "org"


def _safe_org_name_for_file(name: str, max_len: int = 50) -> str:
    if not name or not str(name).strip():
        return ""
    s = str(name).strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^0-9A-Za-z._\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s or ""


def fetch_organization(air_host, api_token, org_id):
    resp = api_get(air_host, api_token, f"/api/public/organizations/{org_id}")
    if not resp.ok:
        raise RuntimeError(
            f"Could not fetch organization {org_id}: HTTP {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    return data.get("result", data) if isinstance(data, dict) else {}


def _suffix_from_content_type(ct: str) -> str:
    if not ct:
        return ".bin"
    ct_lower = ct.split(";")[0].strip().lower()
    if "json" in ct_lower:
        return ".json"
    if "csv" in ct_lower or ct_lower == "text/plain":
        return ".csv"
    if "zip" in ct_lower:
        return ".zip"
    return ".bin"


def _parse_param_pairs(pairs):
    params = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise ValueError(f"Invalid --param (expected key=value): {raw!r}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --param (empty key): {raw!r}")
        params[key] = value
    return params


def _ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Download GET /api/public/assets/export for an organization.",
    )
    parser.add_argument("org_id", help="Organization ID (default query filter[organizationIds])")
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="Output file path (default under output/ with extension from Content-Type)",
    )
    parser.add_argument(
        "--param",
        dest="params_kv",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Extra query parameter (repeatable). Merged with default filter[organizationIds]",
    )
    parser.add_argument(
        "--accept",
        default="*/*",
        metavar="VALUE",
        help='Value for Accept header (default: "*/*")',
    )
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()

    try:
        extra = _parse_param_pairs(args.params_kv)
        params = {"filter[organizationIds]": org_id, **extra}

        org = fetch_organization(air_host, api_token, org_id)
        org_name = org.get("name") or ""
        name_slug = _safe_org_name_for_file(org_name)
        id_slug = _safe_org_slug(org_id)
        if name_slug:
            stem = f"assets_export_org_{name_slug}_{id_slug}"
        else:
            stem = f"assets_export_org_{id_slug}"

        resp = api_get(
            air_host,
            api_token,
            "/api/public/assets/export",
            params=params,
            extra_headers={"Accept": args.accept},
        )
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        ct = resp.headers.get("Content-Type", "")
        suffix = _suffix_from_content_type(ct)

        if args.out:
            out_path = args.out
        else:
            out_dir = os.path.join(_PROJECT_ROOT, "output")
            out_path = os.path.join(out_dir, f"{stem}{suffix}")

        _ensure_parent_dir(out_path)

        if "json" in ct.lower():
            try:
                data = resp.json()
            except ValueError:
                data = None
            if data is not None:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                with open(out_path, "wb") as f:
                    f.write(resp.content)
        elif "csv" in ct.lower() or ct.lower().startswith("text/"):
            text = resp.content.decode(resp.encoding or "utf-8", errors="replace")
            with open(out_path, "w", encoding="utf-8", newline="") as f:
                f.write(text)
        else:
            with open(out_path, "wb") as f:
                f.write(resp.content)

        print(f"Wrote: {out_path} ({ct or 'unknown content-type'})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
