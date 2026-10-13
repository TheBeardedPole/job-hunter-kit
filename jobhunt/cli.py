"""jobhunt — fetch, triage and tailor CVs.

  jobhunt run [--no-tailor] [--sources a,b]   full pipeline (what the timer runs)
  jobhunt status                              counts + what's ready / queued
  jobhunt tailor <job-id | URL>               make a CV for one job now (any URL works)
  jobhunt mark <job-id> applied|skipped|queued
  jobhunt open                                open the dashboard
"""
import argparse
import re
import shutil
import subprocess
import sys
import traceback
import webbrowser

import yaml

from . import backends, config, llm, render, setup, sources, store


def log(*a):
    print("[jobhunt]", *a, flush=True)


def cfg():
    return config.load()


def fetch_all(db, c, only=None):
    total = []
    for name, f in sources.FETCHERS.items():
        if not c["sources"].get(name) or (only and name not in only):
            continue
        try:
            js = f(c)
        except Exception:
            log(f"{name} crashed:\n{traceback.format_exc()}")
            js = []
        log(f"{name}: {len(js)} listings")
        total += js
    new = store.ingest(db, total, c)
    log(f"{len(total)} fetched, {new} new candidates after prefilter")


def score_pending(db, c):
    lim = c["limits"]
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM jobs WHERE status='candidate' ORDER BY posted DESC LIMIT ?",
        (lim["max_scored_per_run"],))]
    for i in range(0, len(rows), lim["score_batch"]):
        batch = rows[i:i + lim["score_batch"]]
        try:
            res = llm.score_batch(batch, c)
        except Exception as ex:
            log(f"scoring batch failed, will retry next run: {ex}")
            continue
        for j in batch:
            r = res.get(j["id"])
            if not r:
                continue
            ok = r["score"] >= c["min_score"].get(j["loc_class"], 101)
            store.update(db, j["id"], score=r["score"], reason=r["reason"], angle=r["angle"],
                         language=r["language"], status="queued" if ok else "rejected")
        log(f"scored {min(i + lim['score_batch'], len(rows))}/{len(rows)}")


def tailor_one(db, j, c):
    j = dict(j)
    log(f"tailoring: {j['title']} @ {j['company']} ({j['score']})")
    j["description"] = sources.full_description(j) or j["description"]
    store.update(db, j["id"], description=j["description"])
    cv = llm.tailor(j, c)
    d = render.write_job(j, cv, c)
    store.update(db, j["id"], status="ready", out_dir=str(d.relative_to(store.ROOT)), language=cv["language"])
    log(f"  -> {d}")
    return j


def tailor_queue(db, c):
    done = []
    rows = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY score DESC LIMIT ?",
                      (c["limits"]["max_tailored_per_run"],)).fetchall()
    for r in rows:
        try:
            done.append(tailor_one(db, r, c))
        except Exception as ex:
            log(f"tailoring failed for {r['id']}, stays queued: {ex}")
    return done


def notify(done, index):
    if not done or not shutil.which("notify-send"):
        return
    top = "\n".join(f"{j['score']} · {j['title']} @ {j['company']}" for j in done[:5])
    try:
        p = subprocess.run(["notify-send", "-a", "Job Hunter", "-i", "mail-send", "-u", "normal",
                            "-A", "open=Open dashboard",
                            f"{len(done)} tailored CV{'s' if len(done) > 1 else ''} ready to send", top],
                           capture_output=True, text=True, timeout=1800)
        if p.stdout.strip() == "open":
            webbrowser.open(index.as_uri())
    except subprocess.TimeoutExpired:
        pass


def cmd_run(a):
    c = cfg()
    db = store.connect()
    fetch_all(db, c, a.sources.split(",") if a.sources else None)
    score_pending(db, c)
    done = [] if a.no_tailor else tailor_queue(db, c)
    index = render.dashboard(db)
    log(f"done: {len(done)} new CVs. Dashboard: {index}")
    if not a.quiet:
        notify(done, index)


def cmd_status(a):
    db = store.connect()
    for k, v in db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status ORDER BY 2 DESC"):
        print(f"{k:10} {v}")
    for st in ("ready", "queued"):
        rows = db.execute(f"SELECT * FROM jobs WHERE status=? ORDER BY score DESC", (st,)).fetchall()
        if rows:
            print(f"\n{st.upper()}")
            for r in rows:
                print(f"  {r['id']}  {r['score'] or '–':>3}  {r['loc_class'] or '':8} {r['title']} @ {r['company']}")
                print(f"  {'':12}  {r['url']}")


def find(db, key):
    r = db.execute("SELECT * FROM jobs WHERE id=? OR id LIKE ? OR url=?", (key, key + "%", key)).fetchone()
    if not r:
        sys.exit(f"no job matching {key}")
    return r


def cmd_mark(a):
    db = store.connect()
    r = find(db, a.id)
    store.update(db, r["id"], status=a.status)
    render.dashboard(db)
    print(f"{r['title']} @ {r['company']} -> {a.status}")


def cmd_tailor(a):
    c = cfg()
    db = store.connect()
    if a.target.startswith("http") and not db.execute("SELECT 1 FROM jobs WHERE url=?", (a.target,)).fetchone():
        stub = sources.job(source="manual", ext_id=a.target, url=a.target)
        text = sources.full_description(stub)
        meta = backends.complete(
            "Extract the job title, company name and location from this job posting, and whether it is "
            "remote, hybrid or onsite. `language` is the posting's language as a two-letter code.\n\n" + text[:8000],
            {"type": "object", "properties": {
                "title": {"type": "string"}, "company": {"type": "string"}, "location": {"type": "string"},
                "work_mode": {"type": "string", "enum": ["remote", "hybrid", "onsite"]},
                "language": {"type": "string"}},
             "required": ["title", "company", "location", "work_mode", "language"]},
            c["llm"]["score"])
        stub.update(title=meta["title"], company=meta["company"], location=meta["location"],
                    work_mode=meta["work_mode"], description=text)
        jid = store.job_id(stub)
        store.ingest(db, [stub], c)
        store.update(db, jid, status="queued", loc_class="manual", score=None,
                     reason="added manually", angle="", language=meta["language"].lower()[:2])
        r = find(db, jid)
    else:
        r = find(db, a.target)
    tailor_one(db, r, c)
    print(render.dashboard(db))


def cmd_open(a):
    webbrowser.open(render.dashboard(store.connect()).as_uri())


def main():
    ap = argparse.ArgumentParser(prog="jobhunt", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--no-tailor", action="store_true")
    r.add_argument("--sources", help="comma-separated subset, e.g. remotive,jobicy")
    r.add_argument("--quiet", action="store_true", help="no desktop notification")
    r.set_defaults(f=cmd_run)
    sp.add_parser("status").set_defaults(f=cmd_status)
    m = sp.add_parser("mark")
    m.add_argument("id")
    m.add_argument("status", choices=["applied", "skipped", "queued"])
    m.set_defaults(f=cmd_mark)
    t = sp.add_parser("tailor")
    t.add_argument("target", help="job id from status/dashboard, or any job posting URL")
    t.set_defaults(f=cmd_tailor)
    sp.add_parser("open").set_defaults(f=cmd_open)
    i = sp.add_parser("init", help="build profile.yaml and settings.yaml from your existing CV")
    for flag in ("--cv", "--country", "--city", "--hybrid", "--languages", "--backend", "--model", "--base-url",
                 "--api-key-env"):
        i.add_argument(flag)
    i.add_argument("--force", action="store_true")
    i.set_defaults(f=setup.cmd_init)
    d = sp.add_parser("doctor", help="check that everything needed is installed")
    d.add_argument("--live", action="store_true", help="also make one small real AI call")
    d.set_defaults(f=lambda a: sys.exit(setup.cmd_doctor(a)))
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
