import requests

from config import BASE_URL


def pull(bucket, start, end):
    try:
        r = requests.get(
            f"{BASE_URL}/buckets/{bucket}/events",
            params={"start": start, "end": end, "limit": 5000},
            timeout=30,
        )
        raw = r.json()
    except requests.RequestException as e:
        print(f"[WARN] ActivityWatch unavailable for bucket={bucket}: {e}")
        return []
    except ValueError as e:
        print(f"[WARN] bucket={bucket}: invalid JSON ({e})")
        return []

    if not isinstance(raw, list):
        # API sometimes returns {"message": ...} on error — iterating a dict yields string keys.
        print(
            f"[WARN] bucket={bucket}: expected event list, got {type(raw).__name__} "
            f"(HTTP {getattr(r, 'status_code', '?')})"
        )
        return []
    return raw
