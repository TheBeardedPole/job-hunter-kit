"""SQLite job store + the deterministic prefilter.

Status flow:
  filtered   – failed title/location/age prefilter (never sent to Claude)
  candidate  – passed prefilter, waiting to be scored
  rejected   – scored below the threshold for its location class
  queued     – good match, waiting for a CV (tailoring is capped per run)
  ready      – tailored CV written; go apply
  applied / skipped – set by you via `jobhunt mark`
"""
import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jobs.db"

COLS = ["id", "source", "ext_id", "url", "title", "company", "location", "work_mode",
        "salary", "posted", "description", "dedupe_key", "first_seen", "status",
        "loc_class", "score", "reason", "angle", "language", "out_dir", "updated"]



def connect():
    DB.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute(f"""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, source TEXT, ext_id TEXT, url TEXT, title TEXT, company TEXT,
        location TEXT, work_mode TEXT, salary TEXT, posted TEXT, description TEXT,
        dedupe_key TEXT, first_seen TEXT, status TEXT, loc_class TEXT, score INTEGER,
        reason TEXT, angle TEXT, language TEXT, out_dir TEXT, updated TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_dedupe ON jobs(dedupe_key)")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")
    return db


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|\b(m/f/d|k/m|m/k|f/m/d|m/w/d)\b", " ", s)
    s = re.sub(r"\b(sp\.? z o\.?o\.?|s\.a\.|inc\.?|ltd\.?|gmbh|llc)\b", " ", s)
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", s).strip()


def job_id(j):
    return hashlib.sha1(f"{j['source']}|{j['ext_id'] or j['url']}".encode()).hexdigest()[:12]


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


# ---------------------------------------------------------------- prefilter
def _hit(text, terms):
    for t in terms:
        if t.startswith("re:"):
            if re.search(t[3:], text, re.I):
                return t
        elif t.lower() in text:
            return t
    return None


def classify(j, cfg):
    """Return (status, loc_class, reason) for a freshly fetched job."""
    title = (j["title"] or "").lower()
    if not title:
        return "filtered", None, "no title"
    forced = _hit(title, cfg.get("title_force_include") or [])
    if not forced:
        if not _hit(title, cfg["title_include"]):
            return "filtered", None, "title not in target roles"
        ex = _hit(title, cfg["title_exclude"])
        if ex:
            return "filtered", None, f"title excluded ({ex})"

    d = parse_date(j["posted"])
    if d:
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if d < datetime.now(timezone.utc) - timedelta(days=cfg["limits"]["max_age_days"]):
            return "filtered", None, "too old"

    loc = (j["location"] or "").lower()
    mode = j["work_mode"]
    you = cfg["you"]
    if any(n.lower() in loc for n in you.get("home_city") or []):
        return "candidate", "home", ""
    if mode == "remote":
        if not loc.strip() or _hit(loc, [a if len(a) > 3 else rf"re:\b{re.escape(a)}\b" for a in cfg["remote_allow"]]):
            return "candidate", "remote", ""
        return "filtered", None, f"remote but not open to {you['country']} ({j['location'][:60]})"
    for city in you.get("hybrid_cities") or []:
        if any(n.lower() in loc for n in city["names"]):
            if mode == "hybrid":
                return "candidate", "hybrid", ""
            return "filtered", None, f"{city.get('label') or city['names'][0]} but not hybrid"
    return "filtered", None, f"location ({j['location'][:60]}, {mode})"


def ingest(db, jobs, cfg):
    """Insert unseen jobs, classify them, dedupe across platforms. Returns count of new candidates."""
    new_c = 0
    for j in jobs:
        jid = job_id(j)
        if db.execute("SELECT 1 FROM jobs WHERE id=?", (jid,)).fetchone():
            continue
        key = f"{norm(j['company'])}|{norm(j['title'])}"
        status, loc_class, reason = classify(j, cfg)
        if status == "candidate" and db.execute(
                "SELECT 1 FROM jobs WHERE dedupe_key=? AND status!='filtered'", (key,)).fetchone():
            status, reason = "filtered", "duplicate of a listing on another platform"
        row = dict(j, id=jid, dedupe_key=key, first_seen=now(), status=status,
                   loc_class=loc_class, reason=reason, updated=now())
        cols = [c for c in COLS if c in row]
        db.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [row[c] for c in cols])
        new_c += status == "candidate"
    db.commit()
    return new_c


def update(db, jid, **kw):
    kw["updated"] = now()
    db.execute(f"UPDATE jobs SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", [*kw.values(), jid])
    db.commit()
