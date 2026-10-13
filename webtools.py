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


def search_task_context(backend, task, today, max_results=5, fetch_pages=3):
    """Search the web for a task and return formatted context text.

    Rewrites the task into a sharp query, searches DuckDuckGo, and reads
    the top pages. Returns an empty string when the search fails, so
    callers can proceed without web context.
    """
    search_query = task
    try:
        rewrite_messages = [
            {"role": "system",
             "content": "You rewrite questions as short web search queries."},
            {"role": "user",
             "content": (
                 "Today is %s. Rewrite the following question as a short web "
                 "search query (under 12 words). Resolve relative dates like "
                 "'next week' or 'tonight' into actual dates. "
                 "Output only the query, nothing else.\n\nQuestion: %s"
                 % (today, task)
             )},
        ]
        rewritten = "".join(backend.stream(rewrite_messages)).strip().strip('"')
        if rewritten:
            search_query = rewritten
    except Exception:
        pass

    try:
        results = web_search(search_query, max_results=max_results)
    except WebError:
        return ""

    context_parts = [
        "[%d] %s\n%s\n%s" % (i + 1, r["title"], r["url"], r["snippet"])
        for i, r in enumerate(results)
    ]
    # Read the top pages so the model works from real content, not snippets.
    for r in results[:fetch_pages]:
        page_text = web_fetch(r["url"])
        if page_text:
            context_parts.append(
                "Page content from %s:\n%s" % (r["url"], page_text)
            )
    return "\n\n".join(context_parts)


def build_web_messages(backend, query, system_prompt, today):
    """Rewrite the query, search the web, read the top pages, and return
    the chat messages that get the model to answer from the results.

    Raises WebError when the search fails.
    """
    context = search_task_context(backend, query, today)
    if not context:
        raise WebError("No search results found.")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user",
         "content": (
             "Today is %s.\n\n"
             "Answer the question below using these web search results. "
             "Give the direct answer first with specific details "
             "(names, dates, times). When you state a fact from the results, "
             "link it inline as a markdown link to the source, like "
             "[ticket prices](https://example.com/tickets). "
             "Never use numbered citations like [1] or [2]. "
             "If the results do not contain the answer, say so plainly "
             "instead of guessing.\n\n"
             "Search results:\n%s\n\nQuestion: %s" % (today, context, query)
         )},
    ]
