#!/usr/bin/env python3
"""
Forex Factory CORS Proxy Server
Run:  python3 server.py
Open: http://localhost:8080/forex-news-dashboard.html
"""
import gzip, http.server, json, os, re, shutil, ssl, time
import subprocess, sys, urllib.error, urllib.parse, urllib.request, zlib
from datetime import datetime, timedelta
from html.parser import HTMLParser

PORT = 8080
CURL = shutil.which("curl") is not None

# ── Request headers ───────────────────────────────────────────────────────────
# Use Chrome/146 (matches what the user's browser sends per the screenshot)
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/146.0.0.0 Safari/537.36")

TV_URL = "https://economic-calendar.tradingview.com/events"

# ISO country codes TradingView uses → FF currency codes
_TV_COUNTRY_MAP = {
    "US": "USD", "EU": "EUR", "EMU": "EUR",
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR", "NL": "EUR",
    "GB": "GBP",
}
# TV returns integer impact: 1=Low, 2=Medium, 3=High (0 or None = unrated)
_TV_IMPACT_MAP = {"1": "Low", "2": "Medium", "3": "High"}

# ── Curated whitelists per currency ──────────────────────────────────────────
# Only events matching these patterns will be shown for each currency.
# FF/TV use slightly different names, so each entry covers both variants.

_USD_WHITELIST = re.compile(
    r'unemployment claims|initial jobless claims|'
    r'core cpi|core consumer price|'
    r'\bcpi\b.*(m/?m|y/?y|mom|yoy)|consumer price index|'
    r'non.?farm (employment|payroll)|nonfarm payroll|nfp\b|payrolls\b|'
    r'core ppi|core producer price|'
    r'adp.*(non.?farm|employment|payroll)|adp employment change\b|'
    r'\bppi\b.*(m/?m|y/?y|mom|yoy)|producer price index|'
    r'federal funds rate|fed funds rate|interest rate decision|fomc|'
    r'average hourly earnings|'
    r'ism manufacturing pmi|ism.*manufacturing|'
    r'fed chair|powell speaks|fomc statement|fed press conf|'
    r'ism services pmi|ism.*services|ism non.?farm|'
    r'jolts job openings|job openings.*labor|'
    r'core retail sales|retail sales.*(m/?m|y/?y|mom|yoy)',
    re.IGNORECASE
)

_EUR_WHITELIST = re.compile(
    r'german flash (manufacturing|services) pmi|'   # German Flash Manufacturing/Services PMI
    r'main refinancing rate|refinancing rate|'       # Main Refinancing Rate
    r'monetary policy statement|'                    # Monetary Policy Statement
    r'ecb press conf|ecb.*press conference',         # ECB Press Conference
    re.IGNORECASE
)

_GBP_WHITELIST = re.compile(
    r'cpi.*(y/?y|m/?m|yoy|mom)|consumer price index|'         # UK CPI y/y, m/m
    r'boe gov.*speaks|bailey speaks|governor.*speaks|'         # BOE Gov Bailey Speaks
    r'gdp.*(m/?m|q/?q|y/?y|mom|qoq|yoy|growth|flash|prel)|'  # UK GDP m/m
    r'claimant count|'                                         # Claimant Count Change
    r'retail sales.*(m/?m|y/?y|mom|yoy)|'                     # Retail Sales m/m
    r'official bank rate|bank rate\b|'                        # Official Bank Rate
    r'mpc.*(vote|decision|minutes|bank rate)|'                 # MPC Official Bank Rate Votes
    r'monetary policy (summary|report|statement)|'             # Monetary Policy Summary/Report
    r'boe.*(monetary policy|rate|decision)|bank of england.*(rate|decision|statement)|'
    r'annual budget|budget release|autumn statement|spring statement|'  # Budget
    r'government spending review|spending review|'             # Spending Review
    r'parliament.*brexit|brexit.*vote|'                        # Brexit Vote
    r'eu membership.*(court|ruling|vote)|'                     # EU Membership rulings
    r'government.*confidence.*vote|confidence.*vote|no.confidence',  # Confidence Vote
    re.IGNORECASE
)

_WHITELIST_BY_CCY = {
    'USD': _USD_WHITELIST,
    'EUR': _EUR_WHITELIST,
    'GBP': _GBP_WHITELIST,
}

def _is_whitelisted(title, currency):
    """Return True if the event title matches the curated whitelist for that currency."""
    wl = _WHITELIST_BY_CCY.get(currency.upper())
    if wl is None:
        return False
    return bool(wl.search(title))

# Legacy keyword list — kept for reference but no longer used for filtering.
_HIGH_KEYWORDS = re.compile(r'.*', re.IGNORECASE)  # matches everything (unused)

# Events to always exclude — regional/state releases, weekly noise, duplicates.
_BLOCKLIST = re.compile(
    r'hesse\s+cpi|bavaria\s+cpi|brandenburg\s+cpi|saxony\s+cpi|north.?rhine|'
    r'baden.?w|berlin\s+cpi|nrw\s+cpi|'              # German state CPIs
    r'harmonised inflation rate|hicp\b|'              # HICP duplicates (headline CPI covers it)
    r'adp\s+weekly|adp\s+employment\s+change\s+weekly|'  # weekly ADP (not the main monthly)
    r'api\s+weekly\s+statistical',                    # API oil bulletin
    re.IGNORECASE
)

def _tv_impact(ev, currency=""):
    """
    Return 'High' if this event passes the curated whitelist for its currency,
    empty string otherwise. Blocklisted events are always excluded.
    """
    title = str(ev.get("title") or ev.get("name") or ev.get("event") or "")
    if _BLOCKLIST.search(title):
        return ""
    if _is_whitelisted(title, currency):
        return "High"
    return ""

TV_HDRS = {
    "User-Agent":      _UA,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin":          "https://www.tradingview.com",
    "Referer":         "https://www.tradingview.com/economic-calendar/",
    "Sec-Fetch-Dest":  "empty",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Site":  "same-site",
}

CDN_HDRS = {
    "User-Agent":          _UA,
    "Accept":              "*/*",
    "Accept-Language":     "en-US,en;q=0.9",
    "Accept-Encoding":     "gzip, deflate",
    "Cache-Control":       "no-cache",
    "Pragma":              "no-cache",
    "Referer":             "https://www.forexfactory.com/",
    "Origin":              "https://www.forexfactory.com",
    "Sec-Ch-Ua":           '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "Sec-Ch-Ua-Mobile":    "?0",
    "Sec-Ch-Ua-Platform":  '"macOS"',
    "Sec-Fetch-Dest":      "empty",
    "Sec-Fetch-Mode":      "cors",
    "Sec-Fetch-Site":      "same-site",
    "Connection":          "keep-alive",
}

HTML_HDRS = {
    "User-Agent":          _UA,
    "Accept":              "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":     "en-US,en;q=0.9",
    "Accept-Encoding":     "gzip, deflate",
    "Cache-Control":       "no-cache",
    "Sec-Ch-Ua":           '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "Sec-Ch-Ua-Mobile":    "?0",
    "Sec-Ch-Ua-Platform":  '"macOS"',
    "Sec-Fetch-Dest":      "document",
    "Sec-Fetch-Mode":      "navigate",
    "Sec-Fetch-Site":      "none",
    "Connection":          "keep-alive",
}

MONTHS = ['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec']


# ── Low-level fetch ───────────────────────────────────────────────────────────

def _decompress(body, enc):
    if enc == 'gzip':   return gzip.decompress(body)
    if enc == 'deflate':
        try:    return zlib.decompress(body)
        except: return zlib.decompress(body, -zlib.MAX_WBITS)
    return body

def _urllib(url, headers):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.status, _decompress(r.read(), r.headers.get('Content-Encoding', ''))

def _curl(url, headers, is_json=False):
    cmd = ['curl', '-s', '-L', '--compressed', '--max-time', '20']
    if is_json:
        cmd += ['--http1.1']  # CDN sometimes behaves better with HTTP/1.1
    for k, v in headers.items():
        cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"curl exit {r.returncode}: {r.stderr.decode()[:120]}")
    return 200, r.stdout

def fetch(url, headers, is_json=False):
    """Try urllib; fall back to curl on any error."""
    try:
        return _urllib(url, headers)
    except urllib.error.HTTPError as e:
        print(f"  urllib HTTP {e.code}")
        if not CURL: raise
    except Exception as e:
        print(f"  urllib error: {e}")
        if not CURL: raise
    print(f"  → retrying with curl…")
    return _curl(url, headers, is_json)


# ── Week helpers ──────────────────────────────────────────────────────────────

def week_bounds(offset=0):
    """
    Return Mon–Fri bounds for the trading week (+offset weeks).
    Matches Forex Factory's definition of "thisweek": the upcoming Mon–Fri.
    On Saturday/Sunday the FF feed has already rolled to next Mon–Fri,
    so we advance the anchor by 1 week on weekends.
    """
    today = datetime.now()
    dow = today.weekday()  # Mon=0 … Sun=6
    # Days since last Monday
    days_to_mon = dow  # Mon→0, Tue→1, … Sun→6
    # On Sat(5) or Sun(6) FF "thisweek" is already the NEXT Mon–Fri
    weekend_shift = 1 if dow >= 5 else 0
    mon = today.replace(hour=0, minute=0, second=0, microsecond=0) \
          - timedelta(days=days_to_mon) + timedelta(weeks=offset + weekend_shift)
    fri = mon + timedelta(days=4)
    return mon, fri

def ff_range_str(start, end):
    return (f"{MONTHS[start.month-1]}{start.day}.{start.year}"
            f"-{MONTHS[end.month-1]}{end.day}.{end.year}")

def et_offset(dt):
    y = dt.year
    dst_s = datetime(y, 3,  8 + (6 - datetime(y, 3,  1).weekday()) % 7)
    dst_e = datetime(y, 11, 1 + (6 - datetime(y, 11, 1).weekday()) % 7)
    return 4 if dst_s <= dt < dst_e else 5


# ── HTML scraper ──────────────────────────────────────────────────────────────

class _FFParser(HTMLParser):
    def __init__(self, year):
        super().__init__()
        self.year     = year
        self.events   = []
        self._row     = None
        self._field   = None
        self._txt     = False
        self._last_dt = None

    @staticmethod
    def _td_field(cls):
        for word in cls.split():
            core = word.replace('calendar__', '')
            if core in ('date','time','currency','impact','event',
                        'actual','forecast','previous'):
                return core
            if '--' in core:
                part = core.split('--')[-1]
                if part in ('date','time','currency','impact','event',
                            'actual','forecast','previous'):
                    return part
        return None

    def handle_starttag(self, tag, attrs):
        a   = dict(attrs)
        cls = a.get('class', '')

        if tag == 'tr':
            if 'calendar__row' in cls:
                self._row   = {}
                self._field = None
                self._txt   = False
            return

        if self._row is None:
            return

        if tag == 'td':
            self._field = self._td_field(cls)
            self._txt   = False
            return

        if tag in ('span', 'a'):
            if self._field is None: return
            if self._field == 'impact':
                if   'impact-red'    in cls: self._row['impact'] = 'High'
                elif 'impact-orange' in cls: self._row['impact'] = 'Medium'
                elif 'impact-yellow' in cls: self._row['impact'] = 'Low'
                elif 'impact-gray'   in cls: self._row['impact'] = 'Holiday'
            elif self._field == 'event':
                if 'event-title' in cls or 'event__title' in cls:
                    self._txt = True
                elif tag == 'a' and not cls:
                    self._txt = True  # bare <a> inside event cell
            elif self._field in ('date','time','currency',
                                  'actual','forecast','previous'):
                self._txt = True

    def handle_data(self, data):
        if not self._txt or self._row is None: return
        data = data.strip()
        if not data: return
        f = self._field
        if f == 'date':
            prev = self._row.get('_date', '')
            combined = (prev + ' ' + data).strip() if prev else data
            self._row['_date'] = combined
            self._last_dt = combined
        elif f == 'time':
            if data.lower() not in ('all day', 'tentative'):
                self._row.setdefault('time', data)
        elif f == 'currency':  self._row['country'] = data.upper()
        elif f == 'event':     self._row.setdefault('title', data)
        elif f in ('actual','forecast','previous'): self._row.setdefault(f, data)

    def handle_endtag(self, tag):
        if tag in ('span', 'a'):
            self._txt = False
        if tag == 'tr' and self._row is not None:
            row       = self._row
            self._row = None
            self._txt = False
            date_str  = row.get('_date') or self._last_dt
            if row.get('title') and row.get('country') and date_str:
                row['_date'] = date_str
                self.events.append(row)


def _build_iso(date_str, year, time_str):
    try:
        dt = datetime.strptime(f"{date_str} {year}", "%b %d %Y")
    except Exception:
        return None
    if not time_str:
        return dt.strftime('%Y-%m-%dT00:00:00+00:00')
    m = re.match(r'(\d+):(\d+)\s*(am|pm)', time_str.strip().lower())
    if not m:
        return dt.strftime('%Y-%m-%dT00:00:00+00:00')
    h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == 'pm' and h != 12: h += 12
    if ap == 'am' and h == 12: h = 0
    off = et_offset(dt)
    return dt.strftime(f'%Y-%m-%dT{h:02d}:{mn:02d}:00-0{off}:00')


def scrape_html(html_bytes, year):
    text   = html_bytes.decode('utf-8', errors='replace')

    # Detect bot-challenge page (Cloudflare, etc.)
    if 'cf-browser-verification' in text or 'Just a moment' in text:
        print("  [!] Cloudflare challenge page detected — cannot scrape without real browser")
        return []

    parser = _FFParser(year)
    parser.feed(text)
    result = []
    for ev in parser.events:
        iso = _build_iso(ev.get('_date',''), year, ev.get('time', None))
        if not iso: continue
        result.append({
            'title':    ev.get('title',    ''),
            'country':  ev.get('country',  ''),
            'date':     iso,
            'impact':   ev.get('impact',   ''),
            'forecast': ev.get('forecast', ''),
            'previous': ev.get('previous', ''),
            'actual':   ev.get('actual',   ''),
        })
    return result


# ── UTC date helper ───────────────────────────────────────────────────────────

def _to_utc(date_str):
    """
    Parse any ISO-8601 date string and return a tz-naive UTC datetime.
    Handles: trailing Z, numeric offsets (+HH:MM / -HH:MM / -0H:00), plain dates.
    Returns None on failure.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        # Normalise Z → +00:00 so fromisoformat accepts it (Python < 3.11)
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        # Fix offset without colon: e.g. -0400 → -04:00
        s = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', s)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            # Convert to UTC by subtracting the UTC offset
            off = dt.utcoffset()
            return (dt.replace(tzinfo=None) - off)
        return dt
    except Exception:
        return None


# ── TradingView actual-value lookup ───────────────────────────────────────────

def _tv_actuals_lookup(start, end):
    """
    Fetch ALL USD/EUR/GBP events from TradingView for the date range and return
    a dict keyed by (YYYY-MM-DD, currency, utc_hour) → list of
    {"title": ..., "actual": ...} candidates.

    Multiple indicators frequently release at the same hour for the same currency
    (e.g. UK CPI y/y + CPI m/m + Core CPI + PPI all at 07:00 London). We keep all
    of them so the enrich step can pick the correct one by fuzzy title match
    instead of silently overwriting.
    """
    params = urllib.parse.urlencode({
        "from":      start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "to":        end.strftime("%Y-%m-%dT23:59:59.000Z"),
        "countries": "US,EU,EMU,DE,FR,IT,ES,GB",
    })
    url = f"{TV_URL}?{params}"
    print(f"[tv-actuals] fetching {url}")
    try:
        _, body = fetch(url, TV_HDRS)
        raw = json.loads(body)
        events = raw.get("result") or raw.get("events") or [] \
                 if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])

        lookup = {}
        total = 0
        for ev in events:
            tv_country = str(ev.get("country") or "").upper()
            currency   = _TV_COUNTRY_MAP.get(tv_country)
            if not currency:
                continue
            raw_actual = ev.get("actual")
            if raw_actual is None:
                continue
            actual = str(raw_actual).strip()
            if not actual or actual in ("None", "null", "undefined", ""):
                continue
            # Note: "0" can be a valid released value (e.g. 0.0% rate change),
            # so we no longer filter it out here.
            dt = _to_utc(ev.get("date") or ev.get("time") or "")
            if dt is None:
                continue
            title = str(ev.get("title") or ev.get("name") or ev.get("event") or "").strip()
            key = (dt.strftime("%Y-%m-%d"), currency, dt.hour)
            lookup.setdefault(key, []).append({"title": title, "actual": actual})
            total += 1

        print(f"[tv-actuals] {total} actual values across {len(lookup)} (date,ccy,hour) buckets")
        return lookup
    except Exception as e:
        print(f"[tv-actuals] error: {e}")
        return {}


# Direction/variant tokens that `_EH_STOPS` normally strips but which we DO
# need to distinguish same-hour siblings (CPI y/y vs CPI m/m, flash vs final).
_EH_DIRECTIONAL = {
    'mom', 'yoy', 'qoq', 'wda', 'sa', 'mm', 'nn', 'qq',
    'flash', 'prelim', 'preliminary', 'final', 'revised',
    'advance', 'advanced', 'second', 'third',
    # sub-type discriminators — manufacturing vs services PMI, etc.
    'manufacturing', 'services', 'composite',
}

# "Concept roots" that must not be confused with each other. If the FF title
# mentions one of these (after expansion) and the TV candidate mentions a
# *different* one, it's not a match — even if fuzzy matching says otherwise.
# This stops "CPI m/m" from binding to "PPI Input MoM".
# Each group lists DISCRIMINATING tokens only — filler words like "price" or
# "index" that appear across multiple families are deliberately excluded.
# That's why CPI/PPI/RPI are represented only by their acronym or the one
# word that distinguishes them ("consumer"/"producer"/"retail" head noun).
# NB the rule only rejects cross-family pairs when BOTH titles have a concept
# token — so unrelated indicators without a concept marker still fall through
# to the plain fuzzy match.
_EH_CONCEPT_GROUPS = [
    {'cpi', 'consumer', 'inflation'},          # consumer prices — headline (TV often uses "Inflation Rate")
    {'ppi', 'producer'},                       # producer prices
    {'rpi'},                                   # UK retail price index (acronym only)
    {'pce'},                                   # personal consumption expenditure
    {'gdp', 'gross', 'domestic'},
    {'nfp', 'nonfarm', 'payroll', 'payrolls', 'employment'},
    {'unemployment', 'claimant', 'jobless'},
    {'claims'},
    {'pmi', 'purchasing'},
    {'earnings', 'wage', 'wages'},
    {'trade'},
    {'durable'},
    {'housing', 'starts', 'permits'},
    {'confidence', 'sentiment'},
    {'industrial', 'manufacturing'},
    {'sales'},                                 # retail sales etc. (different from RPI)
]

# Core vs headline variants are distinct series — keep them separate.
_EH_CORE_TOKEN = 'core'

# FF short forms (m/m, y/y, q/q) ↔ TV long forms (MoM, YoY, QoQ).
# After stripping hyphens/slashes, m/m → mm and so on.
_EH_DIR_ALIASES = {
    'mm': 'mom',
    'yy': 'yoy',
    'qq': 'qoq',
    'monthly':   'mom',
    'annual':    'yoy',
    'annually':  'yoy',
    'yearly':    'yoy',
    'quarterly': 'qoq',
    'preliminary': 'prelim',
}


def _eh_all_tokens(s):
    """All lower-cased tokens in title s, with `_EH_DIR_ALIASES` applied so
    FF short forms and TV long forms collapse to the same token."""
    norm = re.sub(r'[-/]', '', (s or '').lower())
    toks = set(re.sub(r'[^a-z0-9\s]', ' ', norm).split())
    return {_EH_DIR_ALIASES.get(t, t) for t in toks}


def _eh_directional_tokens(s):
    """Subset of _eh_all_tokens that are direction/variant markers."""
    return _eh_all_tokens(s) & _EH_DIRECTIONAL


def _eh_concept(tokens):
    """Return the set of concept-group indices that appear in `tokens`."""
    return {i for i, grp in enumerate(_EH_CONCEPT_GROUPS) if tokens & grp}


def _score_candidate(ff_title, cand_title):
    """
    Score how well a TV candidate title matches an FF title. Higher is better.
    Returns 0 if the pair is not a plausible match at all.

      • base fuzzy match via _eh_match:                                +10
      • shared concept group (same indicator family, e.g. CPI vs CPI): +10
      • shared "core" token or both missing it:                        +4
      • different "core" status (one has "core", the other doesn't):   -8
      • every shared directional token (mom/yoy/flash/…):              +5
      • every directional token present on one side but not the other: -3

    Concept-group mismatch (e.g. CPI vs PPI) returns 0 outright — we never
    bind a value across different indicator families.
    """
    if not _eh_match(ff_title, cand_title):
        return 0

    ff_tok   = _eh_all_tokens(ff_title)
    cand_tok = _eh_all_tokens(cand_title)

    # Concept-group safety: if both titles name a concept group and those
    # groups are disjoint, this is a cross-family mismatch — drop to 0.
    ff_concepts   = _eh_concept(ff_tok)
    cand_concepts = _eh_concept(cand_tok)
    if ff_concepts and cand_concepts and not (ff_concepts & cand_concepts):
        return 0

    score = 10

    if ff_concepts & cand_concepts:
        score += 10

    # Core vs headline distinction
    ff_core   = _EH_CORE_TOKEN in ff_tok
    cand_core = _EH_CORE_TOKEN in cand_tok
    if ff_core == cand_core:
        score += 4
    else:
        score -= 8

    ff_dir   = ff_tok & _EH_DIRECTIONAL
    cand_dir = cand_tok & _EH_DIRECTIONAL
    score += 5 * len(ff_dir & cand_dir)
    score -= 3 * len(ff_dir ^ cand_dir)

    return score


# Minimum score required to confidently bind a TV candidate's actual to an FF
# event. 10 = bare fuzzy match via `_eh_match`; we require a small safety
# margin so pure-noise overlaps (e.g. an exchange-rate speech matching on the
# word "speech") don't bind.
_MIN_BIND_SCORE = 10

# Logging hook — flipped on by _enrich_actuals when it wants to trace a row.
_ENRICH_TRACE = False


def _pick_best_actual(ff_title, candidates, trace=False):
    """
    Pick the candidate whose title best matches ff_title.
    `candidates` is a list of {"title": ..., "actual": ...} dicts.
    Returns the chosen actual string, or None if no acceptable match exists.

    Rules:
      • Candidates with score < `_MIN_BIND_SCORE` are discarded.
      • If the top two qualifying candidates are within 3 points of each
        other AND they have different `core` status or different directional
        tokens, bail out — ambiguity is worse than a wrong guess. A tie
        between structurally identical titles (same core, same direction) is
        assumed to be the same release reported twice by TV.
    """
    if not candidates:
        return None
    scored = sorted(
        ((_score_candidate(ff_title, c["title"]), c) for c in candidates),
        key=lambda x: x[0],
        reverse=True,
    )
    if trace:
        print(f"    ff_title={ff_title!r}")
        for s, c in scored:
            print(f"      score={s:3d}  tv={c['title']!r}  actual={c['actual']!r}")

    top_score, top_cand = scored[0]
    if top_score < _MIN_BIND_SCORE:
        return None

    # Structural tie detection: same core-status + same directional set =
    # effectively the same release. Otherwise, close scores are ambiguous
    # and we refuse to guess.
    if len(scored) > 1 and scored[1][0] >= top_score - 3:
        top_tok    = _eh_all_tokens(top_cand["title"])
        second_tok = _eh_all_tokens(scored[1][1]["title"])
        top_core    = _EH_CORE_TOKEN in top_tok
        second_core = _EH_CORE_TOKEN in second_tok
        top_dir     = top_tok    & _EH_DIRECTIONAL
        second_dir  = second_tok & _EH_DIRECTIONAL
        if top_core != second_core or top_dir != second_dir:
            return None
    return top_cand["actual"]


def _enrich_actuals(ff_events):
    """
    Override actual values in FF CDN events using TradingView data.
    FF CDN sometimes returns stale or incorrect actuals — TV is more accurate.
    We always prefer TV actuals for past events when a confident match exists.
    Matches by (UTC date, currency, UTC hour) AND fuzzy title match so that
    multiple indicators released at the same hour don't get the same value.
    """
    now_utc = datetime.utcnow()

    # Collect all past events (whether or not CDN has a value — CDN can be wrong)
    past_events = [ev for ev in ff_events
                   if (_to_utc(ev.get("date", "")) or datetime.max) < now_utc]

    if not past_events:
        return ff_events

    print(f"[enrich] {len(past_events)} past events — fetching TV lookup to verify/fill actuals")
    start, end = week_bounds(0)
    lookup = _tv_actuals_lookup(start, end)

    if not lookup:
        return ff_events

    filled = 0
    skipped_no_bucket = 0
    skipped_no_match  = 0
    for ev in ff_events:
        dt = _to_utc(ev.get("date", ""))
        if dt is None or dt >= now_utc:
            continue
        if dt is None or dt >= now_utc:
            continue
        key = (dt.strftime("%Y-%m-%d"), str(ev.get("country") or "").upper(), dt.hour)
        candidates = lookup.get(key)
        if not candidates:
            skipped_no_bucket += 1
            continue
        actual = _pick_best_actual(
            str(ev.get("title") or ""),
            candidates,
            trace=_ENRICH_TRACE,
        )
        if actual is not None:
            old = ev.get("actual", "")
            ev["actual"] = actual
            if str(old) != str(actual):
                print(f"[enrich]   updated {ev.get('title')!r}: CDN={old!r} → TV={actual!r}")
            filled += 1
        else:
            skipped_no_match += 1

    print(f"[enrich] TV override: {filled} actuals set "
          f"(no-bucket: {skipped_no_bucket}, no-match: {skipped_no_match})")
    return ff_events


# ── TradingView fallback ──────────────────────────────────────────────────────

def _fetch_tradingview(start, end):
    """
    Fetch economic calendar from TradingView for the given date range.
    Maps TV format → FF format (country→currency code, impact 1/2/3→Low/Medium/High).
    Returns list of events or [] on any error.
    """
    params = urllib.parse.urlencode({
        "from":      start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "to":        end.strftime("%Y-%m-%dT23:59:59.000Z"),
        "countries": "US,EU,EMU,DE,FR,IT,ES,GB",
    })
    url = f"{TV_URL}?{params}"
    print(f"[tv] fetching {url}")
    try:
        _, body = fetch(url, TV_HDRS)
        raw = json.loads(body)
        # TV returns {"status":"ok","result":[...]} or just [...]
        if isinstance(raw, dict):
            events = raw.get("result") or raw.get("events") or []
        else:
            events = raw if isinstance(raw, list) else []

        def _s(v):
            """Coerce a TV field value to clean string, '' if absent/null."""
            if v is None: return ""
            s = str(v).strip()
            return "" if s in ("None", "null", "undefined") else s

        result = []
        for ev in events:
            # Country → currency code
            tv_country = str(ev.get("country") or "").upper()
            currency   = _TV_COUNTRY_MAP.get(tv_country)
            if not currency:
                continue  # skip countries we don't track

            impact = _tv_impact(ev, currency)
            if not impact:
                continue  # skip events not on the curated whitelist

            # Date — TV sends UTC ISO already
            date_val = _s(ev.get("date") or ev.get("time"))

            result.append({
                "title":    _s(ev.get("title") or ev.get("name") or ev.get("event")),
                "country":  currency,
                "date":     date_val,
                "impact":   impact,
                "forecast": _s(ev.get("forecast") or ev.get("estimate") or ev.get("consensus")),
                "previous": _s(ev.get("prev")     or ev.get("previous")),
                "actual":   _s(ev.get("actual")),
            })

        print(f"[tv] {len(result)} events for USD/EUR/GBP")
        for ev in result[:5]:
            print(f"  sample: country={ev.get('country')} impact={ev.get('impact')} date={ev.get('date','')[:16]} title={ev.get('title')}")
        return result
    except Exception as e:
        print(f"[tv] error: {e}")
        return []


# ── Main fetch logic ──────────────────────────────────────────────────────────

def get_ff_week(cdn_url, week_offset):
    """
    Fetch FF CDN JSON feed with up to 3 attempts (handles rate-limiting).
    ALWAYS returns a list (may be empty). Never raises.

    NOTE: forexfactory.com calendar is JavaScript-rendered — plain HTTP requests
    return an empty HTML shell with no calendar rows, so HTML scraping is not a
    viable fallback. The CDN (nfs.faireconomy.media) is the only reliable source.
    """
    label = "nextweek" if week_offset == 1 else "thisweek"

    CDN_RETRY_DELAYS = [0, 3, 6]  # seconds before each attempt

    for attempt, delay in enumerate(CDN_RETRY_DELAYS, start=1):
        if delay:
            print(f"[{label}] CDN attempt {attempt} — waiting {delay}s…")
            time.sleep(delay)

        print(f"[{label}] CDN attempt {attempt}: {cdn_url}")
        try:
            status, body = fetch(cdn_url, CDN_HDRS, is_json=True)
            stripped = body.lstrip()
            if stripped.startswith(b'[') or stripped.startswith(b'{'):
                data = json.loads(body)
                if isinstance(data, list) and data:
                    print(f"[{label}] CDN OK — {len(data)} events (attempt {attempt})")
                    # Apply curated whitelist — only keep events we care about
                    before = len(data)
                    data = [ev for ev in data
                            if not _BLOCKLIST.search(str(ev.get("title","")))
                            and _is_whitelisted(str(ev.get("title","")), str(ev.get("country","")))]
                    print(f"[{label}] whitelist: kept {len(data)} / {before} events")
                    if week_offset == 0:
                        data = _enrich_actuals(data)  # fill actual values FF CDN omits
                    return data
                if isinstance(data, list):
                    # CDN returned [] — could be rate-limit artefact or data not yet published.
                    # Retry up to the limit; if all attempts return [], the feed is genuinely empty.
                    print(f"[{label}] CDN returned [] on attempt {attempt}")
                    continue
            snippet = body[:200].decode('utf-8', errors='replace')
            print(f"[{label}] CDN non-JSON (attempt {attempt}): {snippet[:80]!r}")
        except urllib.error.HTTPError as e:
            print(f"[{label}] CDN HTTP {e.code} on attempt {attempt}")
        except Exception as e:
            print(f"[{label}] CDN error on attempt {attempt}: {e}")

    print(f"[{label}] FF CDN exhausted — trying TradingView economic calendar as fallback")
    start, end = week_bounds(week_offset)
    tv_events = _fetch_tradingview(start, end)
    if tv_events:
        print(f"[{label}] TradingView fallback: {len(tv_events)} events")
        return tv_events

    print(f"[{label}] all sources exhausted — returning []")
    return []


# ── Event-history fuzzy title matching ────────────────────────────────────────
# ForexFactory and TradingView use different names for the same indicators.
# e.g. FF "Non-Farm Employment Change" ↔ TV "Nonfarm Payrolls"
#      FF "CPI m/m"                    ↔ TV "Consumer Price Index MoM"
# We normalise both sides and check for significant token overlap.

_EH_STOPS = frozenset([
    'the','a','an','of','in','for','and','or','to','s','vs',
    'mom','yoy','qoq','wda','sa','mm','nn','qq','m','y','q',
    'final','prelim','preliminary','revised','advance','advanced',
    'flash','second','third','first','estimate','annualized',
    'seasonally','adjusted','monthly','annual','quarterly','yearly',
    'rate','change','index','indicator','gauge','growth',
])

# Acronym → expansion tokens (to bridge "CPI" ↔ "Consumer Price Index")
_EH_EXPAND = {
    # CPI — TradingView often labels headline CPI as "Inflation Rate" (UK, EU),
    # so we expand CPI to include both naming conventions.
    'cpi':       {'consumer', 'price', 'index', 'inflation'},
    'consumer':  {'cpi', 'inflation'},
    'inflation': {'cpi', 'consumer', 'price', 'index'},
    # PPI
    'ppi':       {'producer', 'price', 'index'},
    'producer':  {'ppi'},
    # RPI (UK)
    'rpi':       {'retail', 'price', 'index'},
    'pce':       {'personal', 'consumption', 'expenditure'},
    'gdp':       {'gross', 'domestic', 'product'},
    'pmi':       {'purchasing', 'managers'},
    'nfp':       {'nonfarm', 'payrolls'},
    'ism':       {'ism'},
    'zew':       {'zew'},
    'ifo':       {'ifo'},
    'boe':       {'bank', 'england'},
    'ecb':       {'european', 'central', 'bank'},
    'fomc':      {'federal', 'reserve', 'fomc'},
}

def _eh_tok(s):
    """Tokenise a title: remove hyphens/slashes, lowercase, strip stop words."""
    s = re.sub(r'[-/]', '', s.lower())
    tokens = re.sub(r'[^a-z0-9\s]', ' ', s).split()
    return set(t for t in tokens if t not in _EH_STOPS and len(t) >= 2)

def _eh_expand(tokens):
    exp = set(tokens)
    for t in tokens:
        if t in _EH_EXPAND:
            exp |= _EH_EXPAND[t]
    return exp

def _eh_match(query, candidate):
    """
    Fuzzy title match that bridges FF ↔ TV naming differences.
    Rules:
      • Direct overlap: any shared token of length ≥ 3, or 2+ shared tokens.
      • Acronym-expanded overlap: 2+ shared tokens after expanding known acronyms.
    """
    qt = _eh_tok(query)
    ct = _eh_tok(candidate)
    if not qt or not ct:
        return query.lower().strip() == candidate.lower().strip()
    direct = qt & ct
    if len(direct) >= 2 or any(len(t) >= 3 for t in direct):
        return True
    exp_shared = _eh_expand(qt) & _eh_expand(ct)
    return len(exp_shared) >= 2


# ── Event-history helper ──────────────────────────────────────────────────────

def _fetch_event_history(title, country, year):
    """
    Return all releases of a specific indicator (title + currency) for a given year.
    Fetches TradingView in 59-day chunks to cover the full year, deduplicates,
    then filters using fuzzy title matching to bridge FF ↔ TV naming differences.
    """
    today = datetime.utcnow()
    start = datetime(year, 1, 1)
    end   = min(datetime(year, 12, 31, 23, 59, 59), today)
    if start > today:
        return []

    country_norm = country.upper().strip()
    collected    = []

    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=59), end)
        events    = _fetch_tradingview(chunk_start, chunk_end)
        for ev in events:
            if (ev.get("country","").upper().strip() == country_norm and
                    _eh_match(title, ev.get("title", ""))):
                collected.append(ev)
        chunk_start = chunk_end + timedelta(days=1)

    # Sort and deduplicate by calendar date
    collected.sort(key=lambda e: e.get("date", ""))
    seen, result = set(), []
    for ev in collected:
        day_key = (ev.get("date", "")[:10], ev.get("title", ""))
        if day_key not in seen:
            seen.add(day_key)
            result.append(ev)

    print(f"[event-history] returning {len(result)} releases")
    return result


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/ping":
            self._send_json(200, {"ok": True})
            return

        if parsed.path == "/proxy":
            params = urllib.parse.parse_qs(parsed.query)
            url_list = params.get("url", [])
            if not url_list:
                self._send_json(400, {"error": "missing url param"}); return
            target = url_list[0]
            if not target.startswith("https://nfs.faireconomy.media/"):
                self._send_json(403, {"error": "URL not whitelisted"}); return

            offset = 1 if "nextweek" in target else 0
            events = get_ff_week(target, offset)   # always 200, never raises
            body   = json.dumps(events).encode()
            self.send_response(200)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return

        # /event-history?title=...&country=USD|EUR|GBP&year=2026
        # Fetches all releases of one specific indicator for a full calendar year.
        if parsed.path == "/event-history":
            params  = urllib.parse.parse_qs(parsed.query)
            title   = (params.get("title")   or [""])[0].strip()
            country = (params.get("country") or [""])[0].strip().upper()
            try:
                year = int((params.get("year") or [str(datetime.utcnow().year)])[0])
            except ValueError:
                year = datetime.utcnow().year
            if not title or not country:
                self._send_json(400, {"error": "title and country required"}); return
            if country not in ("USD", "EUR", "GBP"):
                self._send_json(400, {"error": "country must be USD, EUR, or GBP"}); return
            print(f"[event-history] '{title}' / {country} / {year}")
            events = _fetch_event_history(title, country, year)
            self._send_json(200, events)
            return

        # /debug?date=YYYY-MM-DD&country=GBP  (title optional)
        # Returns raw FF CDN rows + raw TV rows for the given date/currency so
        # we can inspect why an enriched actual value looks wrong. Read-only.
        if parsed.path == "/debug":
            params  = urllib.parse.parse_qs(parsed.query)
            date_s  = (params.get("date")    or [""])[0].strip()
            country = (params.get("country") or [""])[0].strip().upper()
            title_q = (params.get("title")   or [""])[0].strip().lower()
            try:
                date = datetime.strptime(date_s, "%Y-%m-%d")
            except ValueError:
                self._send_json(400, {"error": "date must be YYYY-MM-DD"}); return
            if country not in ("USD", "EUR", "GBP"):
                self._send_json(400, {"error": "country must be USD, EUR, or GBP"}); return

            out = {"date": date_s, "country": country, "ff_cdn": [], "tv": []}

            # 1. FF CDN raw
            cdn_url = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json"
                       if (datetime.utcnow() - date).days < 7
                       else "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            try:
                status, body = fetch(cdn_url, CDN_HDRS, is_json=True)
                data = json.loads(body) if body.lstrip().startswith(b'[') else []
                for ev in data:
                    if str(ev.get("country","")).upper() != country:
                        continue
                    if not str(ev.get("date","")).startswith(date_s):
                        continue
                    if title_q and title_q not in str(ev.get("title","")).lower():
                        continue
                    out["ff_cdn"].append(ev)
            except Exception as e:
                out["ff_cdn_error"] = str(e)

            # 2. TV raw for that day
            start_tv = date
            end_tv   = date + timedelta(days=1)
            tv_events = _fetch_tradingview(start_tv, end_tv)
            for ev in tv_events:
                if ev.get("country","").upper() != country:
                    continue
                if not str(ev.get("date","")).startswith(date_s):
                    continue
                if title_q and title_q not in str(ev.get("title","")).lower():
                    continue
                out["tv"].append(ev)

            # 3. What the enrichment pipeline would pick for each FF row
            lookup = _tv_actuals_lookup(start_tv, end_tv)
            out["enrich_preview"] = []
            for ev in out["ff_cdn"]:
                dt = _to_utc(ev.get("date",""))
                if dt is None:
                    continue
                key = (dt.strftime("%Y-%m-%d"), country, dt.hour)
                cands = lookup.get(key, [])
                scored = [
                    {"tv_title": c["title"], "actual": c["actual"],
                     "score": _score_candidate(ev.get("title",""), c["title"])}
                    for c in cands
                ]
                scored.sort(key=lambda x: x["score"], reverse=True)
                out["enrich_preview"].append({
                    "ff_title":   ev.get("title",""),
                    "ff_time":    ev.get("date",""),
                    "ff_actual":  ev.get("actual",""),
                    "ff_forecast":ev.get("forecast",""),
                    "ff_previous":ev.get("previous",""),
                    "bucket":     {"date": key[0], "ccy": key[1], "hour": key[2]},
                    "candidates": scored,
                    "winner":     _pick_best_actual(ev.get("title",""), cands),
                })
            self._send_json(200, out)
            return

        # /history?from=YYYY-MM-DD&to=YYYY-MM-DD
        # Fetches historical events from TradingView for any past date range.
        if parsed.path == "/history":
            params   = urllib.parse.parse_qs(parsed.query)
            from_str = (params.get("from") or [""])[0]
            to_str   = (params.get("to")   or [""])[0]
            try:
                start = datetime.strptime(from_str, "%Y-%m-%d")
                end   = datetime.strptime(to_str,   "%Y-%m-%d")
            except ValueError:
                self._send_json(400, {"error": "invalid date format, use YYYY-MM-DD"}); return
            if (end - start).days > 60:
                self._send_json(400, {"error": "range too large (max 60 days)"}); return
            print(f"[history] {from_str} → {to_str}")
            events = _fetch_tradingview(start, end)   # returns [] on any error
            self._send_json(200, events)
            return

        super().do_GET()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    s0, e0 = week_bounds(0)
    s1, e1 = week_bounds(1)
    print(f"\n  FX News Proxy  ·  port {PORT}")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  Dashboard  →  http://localhost:{PORT}/forex-news-dashboard.html")
    print(f"  This week  →  {ff_range_str(s0,e0)}")
    print(f"  Next week  →  {ff_range_str(s1,e1)}")
    print(f"  curl       →  {'✓' if CURL else '✗ not found (urllib only)'}")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  Each proxy request is logged below.\n")
    try:
        http.server.HTTPServer(("localhost", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)
    except OSError as e:
        if e.errno in (48, 98):
            print(f"\n  Port {PORT} already in use — stop the other server (Ctrl+C) first.\n")
        else: raise
