"""Web search for Kitsune. Free via DuckDuckGo, no API key needed.

The local model has no internet of its own, so the app searches the web
itself and hands the results to the model with the question.
"""

import re
import time
from html import unescape
from urllib.parse import parse_qs, urlparse

try:
    # Impersonates a real Chrome browser at the TLS level, so
    # DuckDuckGo's bot filter treats these like normal searches.
    from curl_cffi import requests as _http
    _IMPERSONATE = "chrome120"
except ImportError:
    import requests as _http
    _IMPERSONATE = None


class WebError(Exception):
    """Raised when web search fails."""


def _clean_html(text):
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return unescape(text).strip()


def _real_url(href):
    # DuckDuckGo sometimes wraps links in a redirect: //duckduckgo.com/l/?uddg=<url>
    if "uddg=" in href:
        try:
            qs = parse_qs(urlparse(href).query)
            return qs.get("uddg", [href])[0]
        except Exception:
            return href
    if href.startswith("//"):
        return "https:" + href
    return href


def _fetch_results(query):
    """Try the DuckDuckGo endpoints, return raw HTML of the first one
    that answers with actual results, or None."""
    variants = [
        ("POST", "https://html.duckduckgo.com/html/", {"data": {"q": query}}),
        ("GET", "https://html.duckduckgo.com/html/", {"params": {"q": query}}),
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }
    for method, url, kw in variants:
        for _ in range(2):
            try:
                if _IMPERSONATE:
                    resp = _http.request(
                        method, url, headers=headers,
                        impersonate=_IMPERSONATE, timeout=20, **kw
                    )
                else:
                    resp = _http.request(
                        method, url, headers=headers, timeout=20, **kw
                    )
            except Exception:
                time.sleep(1)
                continue
            # DuckDuckGo sometimes answers 202 with a bot-check page
            # instead of results. Anything without results is a miss.
            if resp.status_code == 200 and "result__a" in resp.text:
                return resp.text
            time.sleep(1)
    return None


def web_search(query, max_results=5):
    """Search DuckDuckGo and return a list of {title, url, snippet} dicts."""
    query = (query or "").strip()
    if not query:
        raise WebError("A search query is required.")

    html = _fetch_results(query)

    if html is None:
        raise WebError(
            "DuckDuckGo is not returning results right now. "
            "Try again in a bit."
        )

    titles = re.findall(
        r'class="result__a" href="([^"]+)">(.*?)</a>', html, re.S
    )
    snippets = re.findall(
        r'result__snippet" href="[^"]+">(.*?)</a>', html, re.S
    )

    results = []
    for i, (href, title) in enumerate(titles[:max_results]):
        snippet = _clean_html(snippets[i]) if i < len(snippets) else ""
        results.append(
            {
                "title": _clean_html(title),
                "url": _real_url(href),
                "snippet": snippet,
            }
        )
    if not results:
        raise WebError("No search results found.")
    return results


_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")


def web_fetch(url, max_chars=3000):
    """Fetch a page and return its visible text, truncated.

    Returns an empty string when the page cannot be fetched."""
    try:
        if _IMPERSONATE:
            resp = _http.get(url, headers={"User-Agent": _USER_AGENT},
                             impersonate=_IMPERSONATE, timeout=15)
        else:
            resp = _http.get(url, headers={"User-Agent": _USER_AGENT},
                             timeout=15)
    except Exception:
        return ""
    if resp.status_code != 200:
        return ""
    return _clean_html(resp.text)[:max_chars].strip()
