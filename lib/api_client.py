import os
import sys
import time
import warnings
import requests
from dotenv import load_dotenv

warnings.filterwarnings('ignore', message='urllib3 v2 only supports OpenSSL 1.1.1+')

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
BACKOFF_FACTOR = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def load_config():
    """Load API host and token from .env, searching up from cwd to find it."""
    load_dotenv()

    air_host = os.getenv("BINALYZE_AIR_HOST") or os.getenv("AIR_HOST")
    api_token = os.getenv("BINALYZE_API_TOKEN") or os.getenv("AIR_API_TOKEN")

    if not air_host or not api_token:
        print(
            "Set BINALYZE_AIR_HOST and BINALYZE_API_TOKEN in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    return air_host.rstrip("/"), api_token


def _headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url, retries=MAX_RETRIES, **kwargs):
    """
    Execute an HTTP request with exponential backoff on retryable errors.

    Retries on 429 (rate limit), 5xx (server errors), and connection errors.
    Respects Retry-After header when present.
    """
    backoff = INITIAL_BACKOFF
    last_exc = None

    for attempt in range(retries + 1):
        try:
            resp = method(url, **kwargs)

            if resp.status_code not in RETRYABLE_STATUS_CODES:
                return resp

            if attempt == retries:
                return resp

            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff
            else:
                wait = backoff

            print(f"\n  HTTP {resp.status_code}, retrying in {wait:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(wait)
            backoff *= BACKOFF_FACTOR

        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt == retries:
                raise
            print(f"\n  Connection error, retrying in {backoff:.1f}s "
                  f"(attempt {attempt + 1}/{retries})...", file=sys.stderr, flush=True)
            time.sleep(backoff)
            backoff *= BACKOFF_FACTOR

    raise last_exc


def api_get(air_host, api_token, path, params=None, timeout=DEFAULT_TIMEOUT,
            retries=MAX_RETRIES, extra_headers=None):
    url = f"{air_host}{path}"
    headers = dict(_headers(api_token))
    if extra_headers:
        headers.update(extra_headers)
    return _request_with_retry(
        requests.get, url,
        headers=headers, params=params, timeout=timeout,
        retries=retries,
    )


def api_post(air_host, api_token, path, body=None, params=None,
             timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES):
    url = f"{air_host}{path}"
    return _request_with_retry(
        requests.post, url,
        headers=_headers(api_token), json=body or {}, params=params, timeout=timeout,
        retries=retries,
    )
