"""Web search for Kitsune. Free via DuckDuckGo, no API key needed.

The local model has no internet of its own, so the app searches the web
itself and hands the results to the model with the question.
"""

import re
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests


class WebError(Exception):
    """Raised when web search fails."""


def _clean_html(text):
    text = re.sub(r"<[^>]+>", "", text)
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


def web_search(query, max_results=5):
    """Search DuckDuckGo and return a list of {title, url, snippet} dicts."""
    query = (query or "").strip()
    if not query:
        raise WebError("A search query is required.")
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
    except requests.exceptions.ConnectionError:
        raise WebError(
            "Could not reach DuckDuckGo. Check your internet connection."
        )
    except requests.exceptions.Timeout:
        raise WebError("The web search timed out.")
    if resp.status_code != 200:
        raise WebError("DuckDuckGo returned HTTP %s." % resp.status_code)

    titles = re.findall(
        r'class="result__a" href="([^"]+)">(.*?)</a>', resp.text, re.S
    )
    snippets = re.findall(
        r'result__snippet" href="[^"]+">(.*?)</a>', resp.text, re.S
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
