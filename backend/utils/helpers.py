from urllib.parse import urlparse


def get_domain(url):
    if not url:
        return None
    try:
        return urlparse(url).netloc
    except Exception:
        return None


def extract_domain(url):
    if not url or "APP_CONTEXT" in str(url):
        return None
    return get_domain(url)
