"""Job board fetchers, for boards that publish a feed. Each returns a list of normalised job dicts:

    source, ext_id, url, title, company, location, work_mode, salary, posted, description

work_mode is 'remote' | 'hybrid' | 'onsite' | None. A failing source logs and
returns [] so one broken site never sinks the whole run.
"""
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9,pl;q=0.8"})
TIMEOUT = 25


def log(*a):
    print("[sources]", *a, flush=True)


def html_to_text(s, limit=None):
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s).strip()
    return s[:limit] if limit else s


def iso(ts):
    """Accept epoch seconds/ms or ISO-ish strings; return ISO date string or ''."""
    if ts in (None, "", 0):
        return ""
    try:
        if isinstance(ts, (int, float)) or str(ts).isdigit():
            t = float(ts)
            if t > 1e12:
                t /= 1000
            return datetime.fromtimestamp(t, timezone.utc).isoformat()
        return str(ts)
    except Exception:
        return ""


def job(**kw):
    base = dict(source="", ext_id="", url="", title="", company="", location="",
                work_mode=None, salary="", posted="", description="")
    base.update(kw)
    base["title"] = html.unescape(base["title"] or "").strip()
    base["company"] = html.unescape(base["company"] or "").strip()
    return base


def all_queries(cfg):
    """`queries` may be a plain list or a {language: [terms]} mapping; either way, de-duplicated."""
    q = cfg.get("queries") or []
    flat = [t for terms in q.values() for t in terms] if isinstance(q, dict) else list(q)
    seen, out = set(), []
    for t in flat:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def opts(cfg, name):
    """Per-source options: `sources.<name>` may be true/false or a mapping of options."""
    v = (cfg.get("sources") or {}).get(name)
    return v if isinstance(v, dict) else {}


# ---------------------------------------------------------------- page helpers
def _next_data(h):
    """The JSON blob many modern sites embed in the page; handy when reading a single listing by URL."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
    return json.loads(m.group(1)) if m else None


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


# ---------------------------------------------------------------- boards with a public feed
def remotive(cfg):
    try:
        d = S.get("https://remotive.com/api/remote-jobs", timeout=TIMEOUT).json()
    except Exception as e:
        log("remotive", e)
        return []
    return [job(source="remotive", ext_id=str(j["id"]), url=j["url"], title=j["title"],
                company=j["company_name"], location=j.get("candidate_required_location", ""),
                work_mode="remote", salary=j.get("salary", ""), posted=j.get("publication_date", ""),
                description=html_to_text(j.get("description"), 6000)) for j in d.get("jobs", [])]


def remoteok(cfg):
    try:
        d = json.loads(S.get("https://remoteok.com/api", timeout=TIMEOUT).content)
    except Exception as e:
        log("remoteok", e)
        return []
    out = []
    for j in d[1:]:
        sal = ""
        if str(j.get("salary_min", "0")) not in ("0", ""):
            sal = f"${j['salary_min']}–{j.get('salary_max')} /yr"
        out.append(job(source="remoteok", ext_id=str(j.get("id")), url=j.get("url", ""),
                       title=j.get("position"), company=j.get("company"), location=j.get("location", ""),
                       work_mode="remote", salary=sal, posted=j.get("date", ""),
                       description=html_to_text(j.get("description"), 6000)))
    return out


def himalayas(cfg):
    out, seen = [], set()
    for q in all_queries(cfg):
        try:
            d = S.get(f"https://himalayas.app/jobs/api/search?q={quote(q)}&limit=50", timeout=TIMEOUT).json()
        except Exception as e:
            log("himalayas", q, e)
            continue
        for j in d.get("jobs", []):
            if j.get("guid") in seen:
                continue
            seen.add(j.get("guid"))
            locs = j.get("locationRestrictions") or []
            locs = ", ".join(l if isinstance(l, str) else l.get("name", "") for l in locs) or "Worldwide"
            sal = ""
            if j.get("minSalary"):
                sal = f"{j['minSalary']}–{j.get('maxSalary')} {j.get('currency') or ''} /{j.get('salaryPeriod', '')}"
            out.append(job(source="himalayas", ext_id=j.get("guid", ""),
                           url=j.get("applicationLink") or j.get("guid", ""), title=j.get("title"),
                           company=j.get("companyName"), location=locs, work_mode="remote", salary=sal,
                           posted=iso(j.get("pubDate")), description=html_to_text(j.get("description"), 6000)))
        time.sleep(0.5)
    return out


def jobicy(cfg):
    out, seen = [], set()
    for q in all_queries(cfg):
        try:
            d = S.get(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={quote(q)}", timeout=TIMEOUT).json()
        except Exception as e:
            log("jobicy", q, e)
            continue
        for j in d.get("jobs", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            sal = ""
            if j.get("salaryMin"):
                sal = f"{j['salaryMin']}–{j.get('salaryMax')} {j.get('salaryCurrency', '')} /{j.get('salaryPeriod', '')}"
            out.append(job(source="jobicy", ext_id=str(j["id"]), url=j["url"], title=j.get("jobTitle"),
                           company=j.get("companyName"), location=j.get("jobGeo", ""), work_mode="remote",
                           salary=sal, posted=j.get("pubDate", ""),
                           description=html_to_text(j.get("jobDescription"), 6000)))
        time.sleep(0.5)
    return out


def workingnomads(cfg):
    try:
        d = S.get("https://www.workingnomads.com/api/exposed_jobs/", timeout=TIMEOUT).json()
    except Exception as e:
        log("workingnomads", e)
        return []
    out = []
    for j in d:
        out.append(job(source="workingnomads", ext_id=j["url"], url=j["url"], title=j.get("title"),
                       company=j.get("company_name"), location=j.get("location", ""), work_mode="remote",
                       posted=j.get("pub_date", ""), description=html_to_text(j.get("description"), 6000)))
    return out


def weworkremotely(cfg):
    out = []
    for cat in ("remote-jobs",):  # the all-categories feed; the title rules narrow it down
        try:
            r = S.get(f"https://weworkremotely.com/{cat}.rss", timeout=TIMEOUT)
            root = ET.fromstring(r.content)
        except Exception as e:
            log("weworkremotely", cat, e)
            continue
        for it in root.iter("item"):
            t = it.findtext("title") or ""
            company, _, title = t.partition(": ")
            link = it.findtext("link") or ""
            out.append(job(source="weworkremotely", ext_id=link, url=link, title=title or t,
                           company=company, location=it.findtext("region") or "",
                           work_mode="remote", posted=it.findtext("pubDate") or "",
                           description=html_to_text(it.findtext("description"), 6000)))
    return out


FETCHERS = {
    "remotive": remotive, "remoteok": remoteok, "himalayas": himalayas,
    "jobicy": jobicy, "workingnomads": workingnomads, "weworkremotely": weworkremotely,
}


# ---------------------------------------------------------------- full description
def full_description(j):
    """The full text of one listing. Feed boards already supply it; for a URL you pasted, read that page."""
    try:
        if j["source"] == "manual" or len(j.get("description") or "") < 800:
            r = S.get(j["url"], timeout=TIMEOUT)
            nd = _next_data(r.text)
            if nd:
                return html_to_text(json.dumps(_find_key(nd, "sections") or nd, ensure_ascii=False))[:15000]
            body = re.search(r"(?is)<main.*?</main>", r.text)
            return html_to_text(body.group(0) if body else r.text)[:15000]
    except Exception as e:
        log("full_description", j.get("url"), e)
    return j.get("description") or ""
