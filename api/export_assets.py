"""
Download the bulk asset export for an organization.

Endpoint: GET /api/public/assets/export

The response may be JSON, CSV text, or binary; output encoding is inferred
from Content-Type unless --out is specified explicitly.

Run from repository root:
  python api/export_assets.py <org_id>
  python api/export_assets.py <org_id> --out output/my_export.csv
  python api/export_assets.py <org_id> --param format=csv
"""
import argparse
import json
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

from lib.api_client import api_get, load_config


def _safe_org_slug(org_id: str) -> str:
    s = str(org_id).strip()
    if re.fullmatch(r"[\w.\-]+", s):
        return s
    return re.sub(r"[^\w.\-]+", "_", s) or "org"


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
        params[key.strip()] = value
    return params


def main():
    parser = argparse.ArgumentParser(
        description="GET /api/public/assets/export — bulk asset export for an organization.",
    )
    parser.add_argument("org_id", help="Organization ID")
    parser.add_argument("--out", metavar="PATH",
                        help="Output file path (default: output/assets_export_org_<org_id>.<ext>)")
    parser.add_argument("--param", dest="params_kv", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="Extra query parameter (repeatable).")
    parser.add_argument("--accept", default="*/*", metavar="VALUE",
                        help='Accept header value (default: "*/*")')
    args = parser.parse_args()

    org_id = args.org_id.strip()
    if not org_id:
        print("org_id must be non-empty", file=sys.stderr)
        sys.exit(1)

    air_host, api_token = load_config()

    try:
        extra = _parse_param_pairs(args.params_kv)
        params = {"filter[organizationIds]": org_id, **extra}
        id_slug = _safe_org_slug(org_id)

        print(f"GET {air_host}/api/public/assets/export  (org={org_id})")
        resp = api_get(air_host, api_token, "/api/public/assets/export",
                       params=params,
                       extra_headers={"Accept": args.accept})
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        ct = resp.headers.get("Content-Type", "")
        suffix = _suffix_from_content_type(ct)

        out_path = args.out or os.path.join(
            _PROJECT_ROOT, "output", f"assets_export_org_{id_slug}{suffix}")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

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

        print(f"Wrote: {out_path}  ({ct or 'unknown content-type'})")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    main()
