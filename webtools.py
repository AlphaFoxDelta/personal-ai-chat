"""Web search for Kitsune. Free via DuckDuckGo, no API key needed.

The local model has no internet of its own, so the app searches the web
itself and hands the results to the model with the question.
"""

import os
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


def google_search(query, max_results=5):
    """Search via the Google Custom Search JSON API.

    Needs GOOGLE_API_KEY and GOOGLE_CX in the environment. Free tier is
    100 queries/day. Unlike scraping, this works from any network.
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    cx = os.environ.get("GOOGLE_CX", "").strip()
    if not api_key or not cx:
        raise WebError("Google search is not configured.")
    params = {
        "key": api_key,
        "cx": cx,
        "q": query,
        "num": max(1, min(int(max_results), 10)),
    }
    try:
        resp = _http.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params, timeout=20,
        )
    except Exception as exc:
        raise WebError("Google search failed: %s" % exc)
    if resp.status_code != 200:
        raise WebError(
            "Google search returned HTTP %s." % resp.status_code
        )
    try:
        data = resp.json()
    except Exception:
        raise WebError("Google search returned unreadable data.")
    results = []
    for item in (data.get("items") or [])[:max_results]:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
            }
        )
    if not results:
        raise WebError("No search results found.")
    return results


def tavily_search(query, max_results=5):
    """Search via the Tavily API (built for AI apps).

    Needs TAVILY_API_KEY in the environment. Free tier is 1,000
    searches/month with no credit card. Works from any network.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise WebError("Tavily search is not configured.")
    try:
        resp = _http.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "max_results": max(1, min(int(max_results), 10)),
                "search_depth": "basic",
                "include_answer": False,
            },
            timeout=20,
        )
    except Exception as exc:
        raise WebError("Tavily search failed: %s" % exc)
    if resp.status_code == 401:
        raise WebError(
            "Tavily rejected the API key (HTTP 401). Check the key."
        )
    if resp.status_code != 200:
        raise WebError(
            "Tavily search returned HTTP %s." % resp.status_code
        )
    try:
        data = resp.json()
    except Exception:
        raise WebError("Tavily search returned unreadable data.")
    results = []
    for item in (data.get("results") or [])[:max_results]:
        snippet = item.get("content", "") or ""
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": snippet[:500],
            }
        )
    if not results:
        raise WebError("No search results found.")
    return results


def _tavily_configured():
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def _google_configured():
    return bool(
        os.environ.get("GOOGLE_API_KEY", "").strip()
        and os.environ.get("GOOGLE_CX", "").strip()
    )


def _provider_chain():
    """Backends to try in order. Tavily first (reliable API), then Google
    (for whoever has a working whole-web engine), then DuckDuckGo."""
    chain = []
    if _tavily_configured():
        chain.append(("tavily", tavily_search))
    if _google_configured():
        chain.append(("google", google_search))
    chain.append(("duckduckgo", _ddg_search))
    return chain


def web_search(query, max_results=5):
    """Search the web and return a list of {title, url, snippet} dicts.

    Tries each configured backend in order (Tavily, Google, DuckDuckGo)
    and uses the first one that returns results."""
    query = (query or "").strip()
    if not query:
        raise WebError("A search query is required.")
    errors = []
    for name, search_fn in _provider_chain():
        try:
            return search_fn(query, max_results)
        except WebError as exc:
            errors.append("%s: %s" % (name, exc))
    raise WebError("Web search failed (%s)." % "; ".join(errors))


def _ddg_search(query, max_results=5):
    """Search DuckDuckGo and return a list of {title, url, snippet} dicts."""
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


def web_fetch(url, max_chars=5000):
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


def search_with_results(backend, task, today, history=None,
                      max_results=5, fetch_pages=3):
    """Rewrite the task into a search query, search, and read the top pages.

    Returns (results, context): results is the list of
    {title, url, snippet} dicts, context is the formatted text handed to
    the model. Returns ([], "") when the search fails so callers can
    proceed without web context.
    """
    search_query = task
    try:
        hist_text = "\n".join(
            "%s: %s" % (m.get("role"), (m.get("content") or "")[:300])
            for m in (history or [])[-6:]
        )
        rewrite_messages = [
            {"role": "system",
             "content": "You rewrite questions as short web search queries."},
            {"role": "user",
             "content": (
                 "Today is %s. Rewrite the following question as a short web "
                 "search query (under 12 words). Use the conversation history "
                 "to resolve follow-ups, pronouns, and implied subjects. "
                 "Resolve relative dates like 'next week' or 'tonight' into "
                 "actual dates. Output only the query, nothing else.\n\n"
                 "History:\n%s\n\nQuestion: %s"
                 % (today, hist_text or "(none)", task)
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
        return [], ""

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
    return results, "\n\n".join(context_parts)


def search_task_context(backend, task, today, max_results=5, fetch_pages=3):
    """Search the web for a task and return formatted context text.

    Returns an empty string when the search fails, so callers can proceed
    without web context.
    """
    _results, context = search_with_results(
        backend, task, today, max_results=max_results, fetch_pages=fetch_pages
    )
    return context


def linkify_citations(answer, results):
    """Replace [N] citation markers with markdown links to the Nth result.

    The model sometimes mimics the [1], [2] labels from the search context
    instead of writing inline links. This makes those markers clickable.
    Handles both [N] and the CJK-bracket variant [N] some models emit.
    Markers with no matching result are removed.
    """
    def repl(match):
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(results):
            title = results[idx]["title"].replace("[", "").replace("]", "")
            return "[%s](%s)" % (title, results[idx]["url"])
        return ""
    answer = re.sub(r"\[(\d+)\]", repl, answer)
    return re.sub(r"\u3010(\d+)\u3011", repl, answer)


def build_web_messages(backend, query, system_prompt, today, history=None):
    """Rewrite the query, search the web, read the top pages, and return
    the chat messages that get the model to answer from the results.

    history is recent conversation (list of {role, content}) so follow-up
    questions resolve against what was already discussed.

    Returns (messages, results). Raises WebError when the search fails.
    """
    history = history or []
    results, context = search_with_results(
        backend, query, today, history=history
    )
    if not context:
        raise WebError("No search results found.")

    messages = [{"role": "system", "content": system_prompt}]
    messages += history[-6:]
    messages.append(
        {"role": "user",
         "content": (
             "Today is %s.\n\n"
             "Answer the question below using these web search results. "
             "Give the direct answer first with specific details "
             "(names, dates, times). When you state a fact from the results, "
             "link it inline as a markdown link to the source, like "
             "[ticket prices](https://example.com/tickets). "
             "Never use numbered citations like [1] or [2]. "
             "When the question asks for a list, schedule, standings, or "
             "anything with multiple items, include EVERY item from the "
             "results. Never trail off with 'and others', 'more not "
             "specified', or any other summary shortcut. "
             "If the results do not contain the answer, say so plainly "
             "instead of guessing.\n\n"
             "Search results:\n%s\n\nQuestion: %s" % (today, context, query)
         )},
    )
    return messages, results
