"""
yahoo_session.py
=================
Shared helper for querying Yahoo Finance's unofficial chart API.

Why this exists: a bare urllib.request.Request with just a User-Agent
header (the old approach used across this repo) started returning
401 Unauthorized consistently as of ~Aug 18 2026. This is a
widely-documented, ongoing issue with Yahoo's query1/query2 endpoints
tightening enforcement against unauthenticated/cloud-IP traffic
(GitHub Actions runners included) - not something specific to this
repo's code. The fix used here mirrors what the yfinance library
does: seed a session cookie, then fetch a "crumb" token and attach it
to every chart request, retrying once with a fresh crumb on a 401.

This does not guarantee Yahoo won't tighten things further in future,
since it's an unofficial/unlicensed API with no SLA - see this repo's
README for a note on that risk and possible fallback providers.
"""

import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import json

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))
_crumb = None


def _get_crumb():
    global _crumb
    if _crumb:
        return _crumb
    seed_req = urllib.request.Request("https://fc.yahoo.com", headers={"User-Agent": "Mozilla/5.0"})
    try:
        _opener.open(seed_req, timeout=10)
    except Exception:
        pass  # best-effort cookie seed; crumb fetch below may still succeed
    crumb_req = urllib.request.Request(
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with _opener.open(crumb_req, timeout=10) as resp:
        _crumb = resp.read().decode().strip()
    return _crumb


def yahoo_json(url):
    """GET a Yahoo chart API URL with crumb+cookie auth. Retries once
    with a fresh crumb if the first attempt comes back 401."""
    global _crumb
    for attempt in (1, 2):
        crumb = _get_crumb()
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}crumb={urllib.parse.quote(crumb)}" if crumb else url
        req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with _opener.open(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                _crumb = None
                continue
            raise
