from .api_client import api_get

MAX_ITERATIONS = 1000


def paginate_get(air_host, api_token, path, params=None, page_size=100, verbose=True):
    """
    Fetch all entities from a paginated GET endpoint.

    Handles the standard Binalyze response shape:
        { success, result: { entities: [...], nextPage?, totalPageCount?, currentPage? } }

    Falls back to flat list or top-level { entities } shapes.
    Returns a flat list of all entities across all pages.
    """
    base_params = dict(params or {})
    all_entities = []
    page = 1
    seen_pages = set()

    while len(seen_pages) < MAX_ITERATIONS:
        if page in seen_pages:
            if verbose:
                print(f"\nDetected loop at page {page}, stopping.")
            break
        seen_pages.add(page)

        request_params = {**base_params, "page": page, "pageSize": page_size}
        if verbose:
            print(f"Fetching page {page}...", end=" ", flush=True)

        resp = api_get(air_host, api_token, path, params=request_params)
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

        if verbose:
            print("OK")

        data = resp.json()

        result = data.get("result") if isinstance(data, dict) else None
        if result and isinstance(result, dict) and "entities" in result:
            entities = result.get("entities") or []
            if not entities:
                break

            all_entities.extend(entities)

            total_pages = result.get("totalPageCount")
            current_page = result.get("currentPage", page)

            if total_pages and current_page >= total_pages:
                break

            next_page = result.get("nextPage")
            if next_page and next_page != page:
                page = next_page
                continue
            elif total_pages and page < total_pages:
                page += 1
                continue
            else:
                break

        elif isinstance(data, list):
            all_entities.extend(data)
            break
        elif isinstance(data, dict) and "entities" in data:
            all_entities.extend(data["entities"])
            break
        else:
            raise ValueError(f"Unexpected response format: {list(data.keys()) if isinstance(data, dict) else type(data)}")

    return all_entities
