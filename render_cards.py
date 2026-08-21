#!/usr/bin/env python3
"""
Self-hosted GitHub profile card renderer for @khushi235.

Fetches public data from the GitHub REST + GraphQL APIs and renders a set of
animated, diamond-themed SVG cards into ./assets. Designed to be run by a
GitHub Action on a schedule so the profile updates itself forever
(fire-and-forget). Uses only the Python standard library so there is never a
dependency install step that can break.

Every card is rendered inside its own try/except: if the network or the API
fails, the previously committed SVG is left untouched instead of being
replaced by a broken file. That is what makes it fail-safe.
"""

import os
import sys
import json
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta, date

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
USER = os.environ.get("GH_USER", "khushi235").strip() or "khushi235"
TOKEN = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
os.makedirs(OUT, exist_ok=True)

# --------------------------------------------------------------------------- #
# Diamond / jewellery theme
# --------------------------------------------------------------------------- #
BG0 = "#0a0d16"   # deep velvet (jewellery-box black)
BG1 = "#121a2e"   # panel
BG2 = "#1b2440"   # panel highlight
ICE = "#8fe3ff"   # diamond ice (primary)
ICE2 = "#dcf5ff"  # bright ice
PLAT = "#eef1f8"  # platinum text
MUT = "#95a2c0"   # muted text
SAPP = "#5b8cff"  # sapphire
EMER = "#37e0a6"  # emerald
RUBY = "#ff6b93"  # ruby / rose
AMET = "#b98cff"  # amethyst
GOLD = "#ffd479"  # champagne gold
SILV = "#c9d3ea"  # platinum/silver

JEWELS = [ICE, GOLD, SAPP, EMER, AMET, RUBY, SILV]

# On-theme colours for common languages (fall back to the jewel cycle).
LANG_COLORS = {
    "JavaScript": GOLD, "TypeScript": SAPP, "HTML": RUBY, "CSS": ICE,
    "SCSS": RUBY, "Less": SAPP, "Python": EMER, "Java": AMET, "C": SILV,
    "C++": AMET, "C#": EMER, "Shell": EMER, "EJS": GOLD, "Vue": EMER,
    "PHP": SAPP, "Ruby": RUBY, "Go": ICE, "Rust": GOLD, "Dart": SAPP,
    "Kotlin": AMET, "Swift": RUBY, "Handlebars": GOLD, "Pug": RUBY,
    "Other": SILV,
}
# Languages whose byte counts wildly skew the picture (embedded output, etc.).
IGNORE_LANGS = {"Jupyter Notebook"}

FONT = "'Segoe UI',Ubuntu,'Helvetica Neue',Arial,sans-serif"
ROUND = "'Segoe UI Rounded','SF Pro Rounded',ui-rounded,'Trebuchet MS',Verdana,'Segoe UI',system-ui,sans-serif"
MONO = "ui-monospace,'Cascadia Code','JetBrains Mono','Courier New',monospace"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def esc(s):
    """XML-escape a string for safe inclusion in SVG text."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def fmt(n):
    """Human-format an integer: 1234 -> 1.2k, 0 -> 0."""
    try:
        n = int(n)
    except Exception:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def largest_remainder(values, total, decimals=1):
    """Round each value to a percentage of total so the set sums to EXACTLY 100
    (largest remainder method) — no 99.9% / 100.1% drift on the card."""
    scale = 10 ** decimals
    if total <= 0:
        return [0.0] * len(values)
    raw = [v * 100 * scale / total for v in values]
    floors = [int(math.floor(x)) for x in raw]
    deficit = int(round(sum(raw))) - sum(floors)
    order = sorted(range(len(values)), key=lambda i: raw[i] - floors[i], reverse=True)
    for k in range(max(deficit, 0)):
        floors[order[k % len(order)]] += 1
    return [f / scale for f in floors]


def rng(seed):
    """Deterministic pseudo-random generator in [0, 1) (so cards are stable)."""
    s = seed & 0x7FFFFFFF
    while True:
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        yield s / 0x7FFFFFFF


# --------------------------------------------------------------------------- #
# HTTP with retries (stdlib only)
# --------------------------------------------------------------------------- #
def _http(url, data=None, headers=None, method="GET", tries=3):
    headers = headers or {}
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Auth / rate-limit problems will not fix themselves on retry.
            if e.code in (401, 403, 404):
                sys.stderr.write(f"[warn] HTTP {e.code} for {url}\n")
                return None
            sys.stderr.write(f"[warn] HTTP {e.code} for {url} (retry)\n")
        except Exception as e:  # noqa: BLE001 - network is best-effort
            sys.stderr.write(f"[warn] {type(e).__name__} for {url} (retry)\n")
        time.sleep(1.5 * (attempt + 1))
    return None


def _rest_headers():
    h = {"User-Agent": "khushi235-profile", "Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _gql_headers():
    return {
        "User-Agent": "khushi235-profile",
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def rest(path):
    return _http(API + path, headers=_rest_headers())


def rest_abs(url):
    return _http(url, headers=_rest_headers())


def rest_paged(path, per_page=100, max_pages=5):
    out = []
    for page in range(1, max_pages + 1):
        sep = "&" if "?" in path else "?"
        chunk = _http(f"{API}{path}{sep}per_page={per_page}&page={page}",
                      headers=_rest_headers())
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < per_page:
            break
    return out


def gql(query, variables):
    if not TOKEN:
        return None
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    return _http(API + "/graphql", data=payload, headers=_gql_headers(), method="POST")


# --------------------------------------------------------------------------- #
# Data fetch (fail-safe: always returns a fully-populated dict)
# --------------------------------------------------------------------------- #
CAL_QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    contributionsCollection(from:$from, to:$to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def lang_color(name, i):
    return LANG_COLORS.get(name, JEWELS[i % len(JEWELS)])


def compute_streaks(days):
    """Return (longest, current, long_range, cur_range) from {date: count}."""
    active = {d for d, c in days.items() if c > 0}
    if not active:
        return 0, 0, (None, None), (None, None)
    all_dates = sorted(days.keys())
    start = datetime.strptime(all_dates[0], "%Y-%m-%d").date()
    end = datetime.strptime(all_dates[-1], "%Y-%m-%d").date()

    longest = cur = 0
    long_start = long_end = run_start = None
    d = start
    while d <= end:
        if d.isoformat() in active:
            if cur == 0:
                run_start = d
            cur += 1
            if cur > longest:
                longest, long_start, long_end = cur, run_start, d
        else:
            cur = 0
        d += timedelta(days=1)

    today = date.today()
    probe = today if today.isoformat() in active else today - timedelta(days=1)
    cs = 0
    cs_start = cs_end = None
    if probe.isoformat() in active:
        cs_end = probe
        p = probe
        while p.isoformat() in active:
            cs += 1
            cs_start = p
            p -= timedelta(days=1)
    return longest, cs, (long_start, long_end), (cs_start, cs_end)


def weekly_series(days, weeks=52):
    today = date.today()
    start = today - timedelta(days=weeks * 7 - 1)
    buckets = [0] * weeks
    total_days = (today - start).days + 1
    for i in range(total_days):
        dd = start + timedelta(days=i)
        wi = i // 7
        if 0 <= wi < weeks:
            buckets[wi] += days.get(dd.isoformat(), 0)
    return buckets


def fetch():
    d = {
        "login": USER, "name": USER, "bio": "", "followers": 0, "following": 0,
        "repos": 0, "stars": 0, "forks": 0, "commits": 0, "prs": 0, "issues": 0,
        "reviews": 0, "contribs_year": 0, "contribs_all": 0, "cur_streak": 0,
        "long_streak": 0, "langs": [], "weekly": [], "years": 0.0, "created": "",
        "cur_range": (None, None), "long_range": (None, None),
    }

    u = rest(f"/users/{USER}")
    if u:
        d["name"] = u.get("name") or USER
        d["bio"] = u.get("bio") or ""
        d["followers"] = u.get("followers", 0)
        d["following"] = u.get("following", 0)
        d["repos"] = u.get("public_repos", 0)
        d["created"] = u.get("created_at", "") or ""

    # Stars + language bytes across owned (non-fork) repos.
    repos = rest_paged(f"/users/{USER}/repos?sort=pushed")
    lang_bytes = {}
    for r in repos or []:
        if r.get("fork"):
            continue
        d["stars"] += r.get("stargazers_count", 0)
        d["forks"] += r.get("forks_count", 0)
        lb = rest_abs(r["languages_url"]) if r.get("languages_url") else None
        if isinstance(lb, dict) and lb:
            for k, v in lb.items():
                if k in IGNORE_LANGS:
                    continue
                lang_bytes[k] = lang_bytes.get(k, 0) + v
        elif r.get("language") and r["language"] not in IGNORE_LANGS:
            lang_bytes[r["language"]] = lang_bytes.get(r["language"], 0) + 1

    total = sum(lang_bytes.values()) or 1
    ranked = sorted(lang_bytes.items(), key=lambda x: -x[1])
    if len(ranked) > 5:
        head = ranked[:5]
        other_bytes = total - sum(b for _, b in head)
        entries = [(n, b) for n, b in head] + [("Other", other_bytes)]
    else:
        entries = [(n, b) for n, b in ranked]
    pcts = largest_remainder([b for _, b in entries], total)
    combined = sorted(((entries[i][0], pcts[i]) for i in range(len(entries))),
                      key=lambda x: -x[1])
    d["langs"] = [(name, pct, lang_color(name, i))
                  for i, (name, pct) in enumerate(combined)]

    # Contribution history via GraphQL, one <=1yr window per calendar year.
    days = {}
    if TOKEN and d["created"]:
        created_year = int(d["created"][:4])
        now = datetime.now(timezone.utc)
        for y in range(created_year, now.year + 1):
            frm = max(d["created"], f"{y}-01-01T00:00:00Z")
            to = min(now.strftime("%Y-%m-%dT%H:%M:%SZ"), f"{y}-12-31T23:59:59Z")
            res = gql(CAL_QUERY, {"login": USER, "from": frm, "to": to})
            cc = (((res or {}).get("data") or {}).get("user") or {}).get(
                "contributionsCollection")
            if not cc:
                continue
            d["commits"] += cc.get("totalCommitContributions", 0)
            d["prs"] += cc.get("totalPullRequestContributions", 0)
            d["issues"] += cc.get("totalIssueContributions", 0)
            d["reviews"] += cc.get("totalPullRequestReviewContributions", 0)
            for wk in cc["contributionCalendar"]["weeks"]:
                for day in wk["contributionDays"]:
                    days[day["date"]] = day["contributionCount"]

    d["contribs_all"] = sum(days.values())
    cutoff = (date.today() - timedelta(days=365)).isoformat()
    d["contribs_year"] = sum(c for dt, c in days.items() if dt >= cutoff)

    ls, cs, lr, cr = compute_streaks(days)
    d["long_streak"], d["cur_streak"] = ls, cs
    d["long_range"], d["cur_range"] = lr, cr
    d["weekly"] = weekly_series(days, 52)

    if d["created"]:
        born = datetime.strptime(d["created"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        d["years"] = round((datetime.now(timezone.utc) - born).days / 365.25, 1)

    return d


# --------------------------------------------------------------------------- #
# SVG primitives
# --------------------------------------------------------------------------- #
def diamond(cx, cy, s, fill, extra=""):
    """A small faceted diamond/gem shape used as a bullet or accent."""
    return (f'<path d="M{cx:.1f},{cy - s:.1f} '
            f'L{cx + s * 0.82:.1f},{cy - s * 0.18:.1f} '
            f'L{cx:.1f},{cy + s:.1f} '
            f'L{cx - s * 0.82:.1f},{cy - s * 0.18:.1f} Z" fill="{fill}" {extra}/>')


def faceted_gem(cx, cy, s, jewel):
    """A brilliant-cut faceted gem (table + crown + pavilion) in a jewel tint."""
    tw, cw, ch, pd = s * 0.5, s, s * 0.5, s * 1.3

    def P(x, y):
        return f"{cx + x:.1f},{cy + y:.1f}"

    TL, TR = (-tw, -ch), (tw, -ch)
    GL, GR = (-cw, 0.0), (cw, 0.0)
    C, M, MT = (0.0, pd), (0.0, 0.0), (0.0, -ch)
    return (
        f'<g>'
        f'<polygon points="{P(*TL)} {P(*TR)} {P(*GR)} {P(*C)} {P(*GL)}" fill="{jewel}"/>'
        f'<polygon points="{P(*TL)} {P(*GL)} {P(*M)} {P(*MT)}" fill="#ffffff" opacity="0.34"/>'
        f'<polygon points="{P(*TR)} {P(*GR)} {P(*M)} {P(*MT)}" fill="#ffffff" opacity="0.15"/>'
        f'<polygon points="{P(*TL)} {P(*TR)} {P(*MT)}" fill="#ffffff" opacity="0.55"/>'
        f'<polygon points="{P(*GL)} {P(*M)} {P(*C)}" fill="#0a0d16" opacity="0.10"/>'
        f'<polygon points="{P(*GR)} {P(*M)} {P(*C)}" fill="#0a0d16" opacity="0.24"/>'
        f'<polygon points="{P(*TL)} {P(*TR)} {P(*GR)} {P(*C)} {P(*GL)}" fill="none" '
        f'stroke="#0a0d16" stroke-width="0.6" opacity="0.35"/>'
        f'</g>')


def sparkles(seed, w, h, n=10, pad=14):
    """A group of deterministic twinkling 4-point sparkles."""
    g = rng(seed)
    out = ['<g>']
    for i in range(n):
        x = pad + next(g) * (w - 2 * pad)
        y = pad + next(g) * (h - 2 * pad)
        r = 2 + next(g) * 3.5
        dur = 2.2 + next(g) * 2.8
        begin = next(g) * 3.0
        col = JEWELS[i % len(JEWELS)] if i % 3 == 0 else ICE2
        d = (f"M0,-{r:.1f} Q0,0 {r:.1f},0 Q0,0 0,{r:.1f} "
             f"Q0,0 -{r:.1f},0 Q0,0 0,-{r:.1f} Z")
        out.append(
            f'<path transform="translate({x:.1f} {y:.1f})" d="{d}" fill="{col}" opacity="0">'
            f'<animate attributeName="opacity" values="0;0.95;0" dur="{dur:.2f}s" '
            f'begin="{begin:.2f}s" repeatCount="indefinite"/>'
            f'<animateTransform attributeName="transform" type="scale" additive="sum" '
            f'values="0.4;1;0.4" dur="{dur:.2f}s" begin="{begin:.2f}s" repeatCount="indefinite"/>'
            f'</path>')
    out.append('</g>')
    return "".join(out)


def card_defs(idp):
    """Reusable gradient / filter defs, id-prefixed per card."""
    return f"""
  <defs>
    <linearGradient id="{idp}bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BG1}"/>
      <stop offset="0.55" stop-color="{BG0}"/>
      <stop offset="1" stop-color="{BG1}"/>
    </linearGradient>
    <linearGradient id="{idp}edge" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{ICE}"/>
      <stop offset="0.5" stop-color="{SAPP}"/>
      <stop offset="1" stop-color="{AMET}"/>
    </linearGradient>
    <linearGradient id="{idp}ice" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{ICE2}"/>
      <stop offset="1" stop-color="{ICE}"/>
    </linearGradient>
    <linearGradient id="{idp}shine" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0"/>
      <stop offset="0.5" stop-color="#ffffff" stop-opacity="0.55"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <filter id="{idp}glow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="2.4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="{idp}clip"><rect x="1" y="1" width="{{W}}" height="{{H}}" rx="18"/></clipPath>
  </defs>"""


def card_frame(idp, w, h, seed=7, spark=10):
    """Background, animated shimmer sweep, sparkles and jewelled border."""
    defs = card_defs(idp).replace("{W}", str(w - 2)).replace("{H}", str(h - 2))
    return f"""{defs}
  <rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="18" fill="url(#{idp}bg)"/>
  <g clip-path="url(#{idp}clip)">
    {sparkles(seed, w, h, spark)}
    <rect x="{-h}" y="0" width="{h * 1.4:.0f}" height="{h}" fill="url(#{idp}shine)"
          opacity="0.10" transform="skewX(-18)">
      <animateTransform attributeName="transform" type="translate" additive="sum"
        from="{-h} 0" to="{w + h} 0" dur="7s" repeatCount="indefinite"/>
    </rect>
  </g>
  <rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" rx="17" fill="none"
        stroke="url(#{idp}edge)" stroke-width="1.5" opacity="0.9"/>"""


def svg_open(w, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" font-family="{FONT}">')


def title_row(idp, x, y, text, accent=ICE):
    gem = diamond(x + 7, y - 5, 8, f"url(#{idp}ice)", f'filter="url(#{idp}glow)"')
    return (f'{gem}'
            f'<text x="{x + 24}" y="{y}" font-size="17.5" font-weight="700" '
            f'fill="{PLAT}" font-family="{ROUND}">{esc(text)}</text>'
            f'<text x="{x + 24}" y="{y}" font-size="17.5" font-weight="700" '
            f'fill="{accent}" font-family="{ROUND}" opacity="0.0">{esc(text)}'
            f'<animate attributeName="opacity" values="0;0.5;0" dur="4s" repeatCount="indefinite"/></text>')


def fade_in(delay, rise=0.5, total=2.4):
    """Entrance fades are intentionally a no-op. Some image/webview renderers
    freeze SVG animation at t=0, which would permanently hide any content that
    starts at opacity 0. Keeping content visible at t=0 guarantees it shows in
    every renderer; motion comes from decorative (t=0-safe) animations instead."""
    return ""


# --------------------------------------------------------------------------- #
# Dynamic cards
# --------------------------------------------------------------------------- #
def render_stats(d):
    w, h, idp = 480, 232, "s"
    rows = [
        ("Total Stars Earned", fmt(d["stars"]), GOLD),
        ("Total Commits", fmt(d["commits"]), ICE),
        ("Total Pull Requests", fmt(d["prs"]), SAPP),
        ("Total Issues", fmt(d["issues"]), AMET),
        ("Followers", fmt(d["followers"]), RUBY),
        ("Public Repositories", fmt(d["repos"]), EMER),
    ]
    parts = [svg_open(w, h), card_frame(idp, w, h, seed=11, spark=11)]
    parts.append(title_row(idp, 22, 40, "GitHub Facets"))
    parts.append(f'<line x1="22" y1="52" x2="{w - 22}" y2="52" stroke="{BG2}" stroke-width="1"/>')
    y0 = 78
    for i, (label, val, col) in enumerate(rows):
        y = y0 + i * 26
        parts.append(
            f'<g>{fade_in(0.15 + i * 0.12)}'
            f'{diamond(30, y - 4, 5.5, col)}'
            f'<text x="46" y="{y}" font-size="14.5" fill="{MUT}">{esc(label)}</text>'
            f'<text x="{w - 28}" y="{y}" font-size="15" font-weight="700" '
            f'fill="{PLAT}" text-anchor="end">{esc(val)}</text>'
            f'</g>')
    parts.append('</svg>')
    return "".join(parts)


def render_langs(d):
    w, h, idp = 480, 232, "l"
    langs = d["langs"] or [("No data yet", 100.0, MUT)]
    parts = [svg_open(w, h), card_frame(idp, w, h, seed=23, spark=10)]
    parts.append(title_row(idp, 22, 40, "Languages in the Setting"))
    parts.append(f'<line x1="22" y1="52" x2="{w - 22}" y2="52" stroke="{BG2}" stroke-width="1"/>')

    cx, cy, R, sw = 118, 148, 52, 20
    C = 2 * math.pi * R
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" '
                 f'stroke="{BG2}" stroke-width="{sw}"/>')
    start = 0.0
    for i, (name, pct, col) in enumerate(langs):
        frac = max(pct, 0) / 100.0
        seg = max(frac * C - 3, 0.5)
        off = -start * C
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{col}" '
            f'stroke-width="{sw}" stroke-linecap="round" '
            f'stroke-dasharray="{seg:.1f} {C - seg:.1f}" '
            f'stroke-dashoffset="{off:.1f}" transform="rotate(-90 {cx} {cy})">'
            f'{fade_in(0.2 + i * 0.14)}</circle>')
        start += frac
    parts.append(diamond(cx, cy, 15, f"url(#{idp}ice)", f'filter="url(#{idp}glow)"'))

    lx, ly = 208, 96
    for i, (name, pct, col) in enumerate(langs):
        y = ly + i * 22
        parts.append(
            f'<g>{fade_in(0.3 + i * 0.12)}'
            f'{diamond(lx + 6, y - 4, 5.5, col)}'
            f'<text x="{lx + 22}" y="{y}" font-size="13.5" fill="{PLAT}">{esc(name)}</text>'
            f'<text x="{w - 28}" y="{y}" font-size="13.5" font-weight="700" '
            f'fill="{col}" text-anchor="end">{pct:g}%</text></g>')
    parts.append('</svg>')
    return "".join(parts)


def render_overview(d):
    """Stats and languages composed into ONE image so they always sit side by
    side (scaling together) instead of wrapping on the narrow profile column."""
    cardw, gap, h = 480, 16, 232

    def inner(svg):
        return svg[svg.index('>') + 1:svg.rindex('</svg>')]

    parts = [svg_open(cardw * 2 + gap, h)]
    parts.append(f'<g>{inner(render_stats(d))}</g>')
    parts.append(f'<g transform="translate({cardw + gap},0)">{inner(render_langs(d))}</g>')
    parts.append('</svg>')
    return "".join(parts)


def _range_label(rng_tuple):
    a, b = rng_tuple
    if not a or not b:
        return "\u2014"
    fa = a.strftime("%b %d, %Y")
    fb = b.strftime("%b %d, %Y")
    return fa if fa == fb else f"{a.strftime('%b %d')} \u2013 {b.strftime('%b %d, %Y')}"


def render_streak(d):
    w, h, idp = 900, 200, "k"
    parts = [svg_open(w, h), card_frame(idp, w, h, seed=31, spark=9)]
    cols = [
        ("Total Contributions", fmt(d["contribs_all"]), _range_label(
            (datetime.strptime(d["created"][:10], "%Y-%m-%d").date()
             if d["created"] else None, date.today())) if d["created"] else "All time", ICE),
        ("Current Streak", str(d["cur_streak"]), _range_label(d["cur_range"]), GOLD),
        ("Longest Streak", str(d["long_streak"]), _range_label(d["long_range"]), AMET),
    ]
    third = w / 3
    for i, (label, val, sub, col) in enumerate(cols):
        cx = third * i + third / 2
        if i == 1:
            # Centre highlight: gem ring around the current streak.
            parts.append(
                f'<circle cx="{cx:.0f}" cy="92" r="40" fill="none" stroke="{BG2}" stroke-width="5"/>'
                f'<circle cx="{cx:.0f}" cy="92" r="40" fill="none" stroke="{col}" stroke-width="5" '
                f'stroke-linecap="round" stroke-dasharray="200 251" transform="rotate(-90 {cx:.0f} 92)">'
                f'<animateTransform attributeName="transform" type="rotate" '
                f'from="-90 {cx:.0f} 92" to="270 {cx:.0f} 92" dur="9s" repeatCount="indefinite"/>'
                f'</circle>')
            parts.append(f'<text x="{cx:.0f}" y="102" font-size="34" font-weight="800" '
                         f'fill="{PLAT}" text-anchor="middle" filter="url(#{idp}glow)">{esc(val)}</text>')
            parts.append(diamond(cx, 40, 9, f"url(#{idp}ice)", f'filter="url(#{idp}glow)"'))
        else:
            parts.append(f'<text x="{cx:.0f}" y="98" font-size="30" font-weight="800" '
                         f'fill="{col}" text-anchor="middle">{esc(val)}</text>')
            parts.append(diamond(cx, 46, 7, col))
        parts.append(f'<text x="{cx:.0f}" y="150" font-size="13.5" font-weight="700" '
                     f'fill="{PLAT}" text-anchor="middle">{esc(label)}</text>')
        parts.append(f'<text x="{cx:.0f}" y="170" font-size="10.5" '
                     f'fill="{MUT}" text-anchor="middle">{esc(sub)}</text>')
    # Divider gems between columns.
    for i in (1, 2):
        parts.append(diamond(third * i, 92, 5, SILV, 'opacity="0.5"'))
    parts.append('</svg>')
    return "".join(parts)


def render_activity(d):
    w, h, idp = 900, 240, "a"
    weekly = d["weekly"] or [0] * 52
    n = len(weekly)
    mx = max(weekly) or 1
    x0, x1 = 40, w - 40
    y0, y1 = 70, h - 46
    step = (x1 - x0) / max(n - 1, 1)

    def px(i):
        return x0 + i * step

    def py(v):
        return y1 - (v / mx) * (y1 - y0)

    pts = [(px(i), py(v)) for i, v in enumerate(weekly)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (f"M{pts[0][0]:.1f},{y1:.1f} L" +
            " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) +
            f" L{pts[-1][0]:.1f},{y1:.1f} Z")

    parts = [svg_open(w, h), card_frame(idp, w, h, seed=41, spark=14)]
    parts.append(f"""
  <defs>
    <linearGradient id="{idp}fill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{ICE}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="{ICE}" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="{idp}stroke" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{SAPP}"/>
      <stop offset="0.5" stop-color="{ICE}"/>
      <stop offset="1" stop-color="{AMET}"/>
    </linearGradient>
  </defs>""")
    parts.append(title_row(idp, 24, 42, "Contribution Brilliance \u2014 last 52 weeks"))
    # baseline + soft gridlines
    for gy in range(1, 4):
        yy = y0 + (y1 - y0) * gy / 4
        parts.append(f'<line x1="{x0}" y1="{yy:.0f}" x2="{x1}" y2="{yy:.0f}" '
                     f'stroke="{BG2}" stroke-width="1" opacity="0.5"/>')
    parts.append(f'<path d="{area}" fill="url(#{idp}fill)"/>')
    parts.append(
        f'<path d="{line}" fill="none" stroke="url(#{idp}stroke)" stroke-width="3" '
        f'stroke-linejoin="round" stroke-linecap="round" filter="url(#{idp}glow)"/>')
    # peak gem marker (or a friendly hint when the year is empty)
    peak_i = max(range(n), key=lambda i: weekly[i])
    if weekly[peak_i] > 0:
        pxk, pyk = pts[peak_i]
        parts.append(f'<g>{fade_in(2.2, rise=0.5, total=3.0)}{diamond(pxk, pyk, 7, GOLD)}'
                     f'<text x="{pxk:.0f}" y="{pyk - 14:.0f}" font-size="11" fill="{GOLD}" '
                     f'text-anchor="middle" font-weight="700">{weekly[peak_i]}</text></g>')
    else:
        parts.append(f'<text x="{w / 2:.0f}" y="{(y0 + y1) / 2:.0f}" font-size="14" '
                     f'fill="{MUT}" text-anchor="middle" font-style="italic">'
                     f'\u2726 new commits will sparkle here \u2726</text>')
    parts.append(f'<text x="{x0}" y="{h - 18}" font-size="11" fill="{MUT}">1 year ago</text>')
    parts.append(f'<text x="{x1}" y="{h - 18}" font-size="11" fill="{MUT}" '
                 f'text-anchor="end">this week</text>')
    parts.append(f'<text x="{w / 2:.0f}" y="{h - 18}" font-size="11" fill="{MUT}" '
                 f'text-anchor="middle">{fmt(d["contribs_year"])} contributions in the last year</text>')
    parts.append('</svg>')
    return "".join(parts)


def render_trophies(d):
    w, h, idp = 900, 150, "t"
    items = [
        ("Years on GitHub", f'{d["years"]:g}', ICE),
        ("Repositories", fmt(d["repos"]), EMER),
        ("Stars", fmt(d["stars"]), GOLD),
        ("Followers", fmt(d["followers"]), RUBY),
        ("Longest Streak", str(d["long_streak"]), AMET),
        ("Commits", fmt(d["commits"]), SAPP),
    ]
    parts = [svg_open(w, h), card_frame(idp, w, h, seed=53, spark=12)]
    pad = 30
    cw = (w - 2 * pad) / len(items)
    for i, (label, val, col) in enumerate(items):
        cx = pad + cw * i + cw / 2
        cy = 62
        parts.append(f'<g>{fade_in(0.2 + i * 0.12)}')
        # faceted medallion
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy}" r="30" fill="{BG2}" '
                     f'stroke="{col}" stroke-width="1.6"/>')
        parts.append(diamond(cx, cy - 2, 15, col, 'opacity="0.28"'))
        parts.append(f'<text x="{cx:.0f}" y="{cy + 6}" font-size="19" font-weight="800" '
                     f'fill="{PLAT}" text-anchor="middle">{esc(val)}</text>')
        parts.append(f'<text x="{cx:.0f}" y="{cy + 52}" font-size="11.5" '
                     f'fill="{MUT}" text-anchor="middle">{esc(label)}</text>')
        parts.append('</g>')
        if i:
            parts.append(diamond(pad + cw * i, cy, 4, SILV, 'opacity="0.4"'))
    parts.append('</svg>')
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Static (data-independent) animated cards
# --------------------------------------------------------------------------- #
def render_header(d):
    w, h, idp = 1000, 300, "h"
    name = d.get("name") or "Khushi Shukla"
    cx = w / 2
    parts = [svg_open(w, h)]
    parts.append(f"""
  <defs>
    <linearGradient id="{idp}sky" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0a0d16"/>
      <stop offset="0.45" stop-color="#111b38"/>
      <stop offset="0.75" stop-color="#0d1428"/>
      <stop offset="1" stop-color="#0a0d16"/>
    </linearGradient>
    <linearGradient id="{idp}plat" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{ICE2}"/>
      <stop offset="0.32" stop-color="#ffffff"/>
      <stop offset="0.5" stop-color="{ICE}"/>
      <stop offset="0.68" stop-color="#ffffff"/>
      <stop offset="1" stop-color="{SILV}"/>
    </linearGradient>
    <linearGradient id="{idp}band" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{SAPP}"/><stop offset="0.5" stop-color="{ICE2}"/>
      <stop offset="1" stop-color="{AMET}"/>
    </linearGradient>
    <radialGradient id="{idp}halo" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{ICE}" stop-opacity="0.32"/>
      <stop offset="1" stop-color="{ICE}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="{idp}shine" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#fff" stop-opacity="0"/>
      <stop offset="0.5" stop-color="#fff" stop-opacity="0.6"/>
      <stop offset="1" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>
    <filter id="{idp}glow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="2.6" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="{idp}clip"><rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="22"/></clipPath>
  </defs>
  <rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="22" fill="url(#{idp}sky)"/>
  <g clip-path="url(#{idp}clip)">""")
    # Low-poly gem facets in the corners for subtle texture.
    facets = [
        (0, 0, 210, 0, 0, 150, SAPP, 0.06), (0, 0, 120, 0, 0, 80, ICE, 0.05),
        (w, 0, w - 230, 0, w, 160, AMET, 0.06), (w, 0, w - 120, 0, w, 82, ICE, 0.05),
        (0, h, 190, h, 0, h - 130, AMET, 0.05), (w, h, w - 210, h, w, h - 150, SAPP, 0.06),
    ]
    for (x1, y1, x2, y2, x3, y3, col, op) in facets:
        parts.append(f'<polygon points="{x1},{y1} {x2},{y2} {x3},{y3}" '
                     f'fill="{col}" opacity="{op}"/>')
    parts.append(sparkles(99, w, h, 30))
    parts.append(f'<rect x="{-h}" y="0" width="{h * 1.4:.0f}" height="{h}" '
                 f'fill="url(#{idp}shine)" opacity="0.07" transform="skewX(-18)">'
                 f'<animateTransform attributeName="transform" type="translate" additive="sum" '
                 f'from="{-h} 0" to="{w + h} 0" dur="8s" repeatCount="indefinite"/></rect>')
    parts.append('</g>')

    # --- Tiara: graduated gems on a gentle arc, set on a platinum band ---
    sizes = [10, 14, 18, 26, 18, 14, 10]
    jewels = [SAPP, ICE2, AMET, ICE2, AMET, ICE2, SAPP]
    xs = [cx + (i - 3) * 54 for i in range(7)]
    lift = [0, 8, 13, 17, 13, 8, 0]
    ys = [92 - lift[i] for i in range(7)]
    band = f'M{xs[0]:.0f},{ys[0] + 4} Q{cx:.0f},50 {xs[6]:.0f},{ys[6] + 4}'
    parts.append(f'<path d="{band}" fill="none" stroke="url(#{idp}band)" '
                 f'stroke-width="2.5" opacity="0.55"/>')
    parts.append(f'<path d="{band}" fill="none" stroke="#ffffff" '
                 f'stroke-width="1" opacity="0.30"/>')
    parts.append(f'<g filter="url(#{idp}glow)">'
                 f'<animateTransform attributeName="transform" type="translate" '
                 f'values="0 0;0 -4;0 0" dur="5s" repeatCount="indefinite"/>')
    for i in range(7):
        parts.append(faceted_gem(xs[i], ys[i], sizes[i], jewels[i]))
    parts.append('</g>')
    for i in (1, 3, 5):
        parts.append(
            f'<path transform="translate({xs[i]:.0f} {ys[i] - 2:.0f})" '
            f'd="M0,-6 Q0,0 6,0 Q0,0 0,6 Q0,0 -6,0 Q0,0 0,-6 Z" fill="#fff" opacity="0">'
            f'<animate attributeName="opacity" values="0;1;0" dur="2.8s" '
            f'begin="{i * 0.4:.1f}s" repeatCount="indefinite"/></path>')

    # --- Name with a soft halo ---
    parts.append(f'<ellipse cx="{cx:.0f}" cy="186" rx="300" ry="46" fill="url(#{idp}halo)"/>')
    parts.append(f'<text x="{cx:.0f}" y="202" font-size="54" font-weight="800" '
                 f'font-family="{ROUND}" fill="url(#{idp}plat)" text-anchor="middle" '
                 f'letter-spacing="2" filter="url(#{idp}glow)">{esc(name)}</text>')

    # --- Ornamental divider + subtitle ---
    dy = 230
    parts.append(f'<line x1="{cx - 212:.0f}" y1="{dy}" x2="{cx - 26:.0f}" y2="{dy}" '
                 f'stroke="url(#{idp}band)" stroke-width="1.4" opacity="0.7"/>')
    parts.append(f'<line x1="{cx + 26:.0f}" y1="{dy}" x2="{cx + 212:.0f}" y2="{dy}" '
                 f'stroke="url(#{idp}band)" stroke-width="1.4" opacity="0.7"/>')
    parts.append(diamond(cx - 212, dy, 3.5, SAPP))
    parts.append(diamond(cx + 212, dy, 3.5, SAPP))
    parts.append(faceted_gem(cx, dy, 9, ICE2))
    parts.append(f'<text x="{cx:.0f}" y="266" font-size="15" font-family="{FONT}" '
                 f'fill="{ICE}" text-anchor="middle" letter-spacing="5" '
                 f'opacity="0.92">FULL-STACK WEB DEVELOPER</text>')

    # --- Double jewelled border with corner gems ---
    parts.append(f'<rect x="1.5" y="1.5" width="{w - 3}" height="{h - 3}" rx="21" '
                 f'fill="none" stroke="url(#{idp}band)" stroke-width="1.5" opacity="0.85"/>')
    parts.append(f'<rect x="9" y="9" width="{w - 18}" height="{h - 18}" rx="16" '
                 f'fill="none" stroke="{SILV}" stroke-width="1" opacity="0.18"/>')
    for (gxn, gyn) in [(26, 26), (w - 26, 26), (26, h - 26), (w - 26, h - 26)]:
        parts.append(faceted_gem(gxn, gyn, 7, ICE2))
    parts.append('</svg>')
    return "".join(parts)


def render_subtitle(d):
    phrases = [
        "Full-Stack Web Developer",
        "Diamond-Industry Alumna",
        "Educator at Heart",
        "Turning ideas into gems",
        "New York \u2022 NYIT",
    ]
    fs = 24
    adv = fs * 0.60
    x0, base = 52, 40
    maxw = max(len(p) for p in phrases) * adv
    w = int(x0 + maxw + 34)
    h = 60
    n = len(phrases)
    slot = 1.0 / n
    per = 3.4
    T = per * n
    r = (h - 2) / 2
    parts = [svg_open(w, h)]
    parts.append(f"""<defs>
    <linearGradient id="subbg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BG1}"/><stop offset="0.5" stop-color="{BG0}"/>
      <stop offset="1" stop-color="{BG1}"/>
    </linearGradient>
    <linearGradient id="subedge" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{ICE}"/><stop offset="0.5" stop-color="{SAPP}"/>
      <stop offset="1" stop-color="{AMET}"/>
    </linearGradient>
  </defs>""")
    parts.append(f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="{r:.0f}" fill="url(#subbg)"/>')
    parts.append(f'<rect x="1.5" y="1.5" width="{w-3}" height="{h-3}" rx="{r:.0f}" fill="none" '
                 f'stroke="url(#subedge)" stroke-width="1.2" opacity="0.85"/>')
    parts.append(diamond(30, base - 8, 8, ICE))
    for i, p in enumerate(phrases):
        tw = len(p) * adv
        t0 = i * slot
        t_type = t0 + slot * 0.34
        t_hold = t0 + slot * 0.86
        t_end = t0 + slot * 1.0
        kt = [0, t0, t_type, t_hold, t_end, 1]
        vals = [0, 0, tw, tw, 0, 0]
        cid = f"clip{i}"
        kt_s = ";".join(f"{k:.4f}" for k in kt)
        vals_s = ";".join(f"{v:.1f}" for v in vals)
        parts.append(f'<clipPath id="{cid}"><rect x="{x0}" y="0" height="{h}" width="{tw if i == 0 else 0:.0f}">'
                     f'<animate attributeName="width" values="{vals_s}" keyTimes="{kt_s}" '
                     f'dur="{T:.1f}s" repeatCount="indefinite"/></rect></clipPath>')
        parts.append(
            f'<g clip-path="url(#{cid})">'
            f'<text x="{x0}" y="{base}" font-size="{fs}" font-weight="700" '
            f'font-family="{MONO}" fill="{ICE2}">{esc(p)}</text>'
            f'<rect x="{x0 + tw:.1f}" y="{base - fs + 4}" width="3" height="{fs}" fill="{GOLD}">'
            f'<animate attributeName="opacity" values="1;0;1" dur="0.9s" repeatCount="indefinite"/>'
            f'</rect></g>')
    parts.append('</svg>')
    return "".join(parts)


def render_footer(d):
    w, h, idp = 1000, 130, "f"
    parts = [svg_open(w, h)]
    parts.append(f"""
  <defs>
    <linearGradient id="{idp}bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BG1}"/><stop offset="0.5" stop-color="{BG0}"/>
      <stop offset="1" stop-color="{BG1}"/>
    </linearGradient>
    <linearGradient id="{idp}wave" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{SAPP}"/><stop offset="0.5" stop-color="{ICE}"/>
      <stop offset="1" stop-color="{AMET}"/>
    </linearGradient>
    <filter id="{idp}glow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="2.4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <clipPath id="{idp}clip"><rect x="1" y="1" width="{w-2}" height="{h-2}" rx="20"/></clipPath>
  </defs>""")
    parts.append(f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="20" fill="url(#{idp}bg)"/>')
    parts.append(f'<g clip-path="url(#{idp}clip)">')
    parts.append(sparkles(77, w, h, 16))
    # two layered shimmering waves
    parts.append(
        f'<path fill="url(#{idp}wave)" opacity="0.20" '
        f'd="M0,60 C150,20 350,100 500,60 C650,20 850,100 1000,60 L1000,130 L0,130 Z">'
        f'<animate attributeName="d" dur="8s" repeatCount="indefinite" '
        f'values="M0,60 C150,20 350,100 500,60 C650,20 850,100 1000,60 L1000,130 L0,130 Z;'
        f'M0,60 C150,100 350,20 500,60 C650,100 850,20 1000,60 L1000,130 L0,130 Z;'
        f'M0,60 C150,20 350,100 500,60 C650,20 850,100 1000,60 L1000,130 L0,130 Z"/></path>')
    parts.append(
        f'<path fill="url(#{idp}wave)" opacity="0.35" '
        f'd="M0,80 C200,50 300,110 500,80 C700,50 800,110 1000,80 L1000,130 L0,130 Z">'
        f'<animate attributeName="d" dur="6s" repeatCount="indefinite" '
        f'values="M0,80 C200,50 300,110 500,80 C700,50 800,110 1000,80 L1000,130 L0,130 Z;'
        f'M0,80 C200,110 300,50 500,80 C700,110 800,50 1000,80 L1000,130 L0,130 Z;'
        f'M0,80 C200,50 300,110 500,80 C700,50 800,110 1000,80 L1000,130 L0,130 Z"/></path>')
    parts.append('</g>')
    parts.append(diamond(500, 46, 11, ICE2, f'filter="url(#{idp}glow)"'))
    parts.append(f'<text x="500" y="42" font-size="17" font-family="{ROUND}" '
                 f'fill="{PLAT}" text-anchor="middle" font-weight="700">'
                 f'Thanks for visiting \u2014 let\u2019s build something brilliant \u2728</text>')
    parts.append(f'<rect x="1.5" y="1.5" width="{w-3}" height="{h-3}" rx="19" fill="none" '
                 f'stroke="url(#{idp}wave)" stroke-width="1.3" opacity="0.7"/>')
    parts.append('</svg>')
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
CARDS = [
    ("header.svg", render_header),
    ("subtitle.svg", render_subtitle),
    ("overview.svg", render_overview),
    ("streak.svg", render_streak),
    ("activity.svg", render_activity),
    ("trophies.svg", render_trophies),
    ("footer.svg", render_footer),
]


def write_svg(name, content):
    if not content or "<svg" not in content:
        print(f"[skip] {name}: empty/invalid render, keeping existing file")
        return False
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"[ok]   {name} ({len(content)} bytes)")
    return True


def main():
    print(f"Rendering profile cards for @{USER} (token: {'yes' if TOKEN else 'no'})")
    try:
        data = fetch()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[error] fetch failed: {e}\n")
        data = None
    if data is None:
        # Absolute fallback so static cards still render.
        data = {"login": USER, "name": "Khushi Shukla", "followers": 0,
                "following": 0, "repos": 0, "stars": 0, "commits": 0, "prs": 0,
                "issues": 0, "contribs_year": 0, "contribs_all": 0,
                "cur_streak": 0, "long_streak": 0, "langs": [], "weekly": [],
                "years": 0.0, "created": "", "cur_range": (None, None),
                "long_range": (None, None)}

    ok = 0
    for name, fn in CARDS:
        try:
            if write_svg(name, fn(data)):
                ok += 1
        except Exception as e:  # noqa: BLE001 - never let one card break the rest
            sys.stderr.write(f"[error] {name}: {type(e).__name__}: {e}\n")
    print(f"Done: {ok}/{len(CARDS)} cards written.")


if __name__ == "__main__":
    main()
