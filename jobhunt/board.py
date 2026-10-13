"""The dashboard: a static page (out/index.html) and, with `jobhunt board`, the same page served
on this computer only, with buttons to set a status and rate an offer.

The server listens on 127.0.0.1 and nothing else. Every change needs a token that is printed into
the page it serves, and the Host header must be this machine, so another website open in your
browser cannot press the buttons for you.
"""
import mimetypes
import queue
import secrets
import threading
from datetime import date
from html import escape as e
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import feedback, store
from .store import ROOT

OUT = ROOT / "out"
PALETTE = dict(bg="#F4F5F7", card="#fff", ink="#16191F", muted="#5B6472", a="#1F4E79", b="#B45309", line="#DFE3E8",
               dbg="#12161D", dcard="#1A2029", dink="#EEF1F5", dmuted="#9AA4B2", da="#7CB7F0", db="#F0A860", dline="#2A313C")
SETTABLE = ["ready", "sent", "responded", "interview", "offer", "rejected", "skipped"]


def _links(r, prefix):
    out = [f'<a href="{e(r["url"])}" target="_blank" rel="noopener">Listing ↗</a>']
    if r["out_dir"]:
        d = ROOT / r["out_dir"]
        rel = lambda p: e(prefix + quote(str(p.relative_to(OUT))))
        pdf = next(d.glob("*.pdf"), None) if d.exists() else None
        if pdf:
            out.append(f'<a href="{rel(pdf)}">CV (PDF)</a>')
        for name, label in (("cv.html", "CV (HTML)"), ("cover_note.txt", "Cover note"), ("job.md", "Notes")):
            if (d / name).exists():
                out.append(f'<a href="{rel(d / name)}">{label}</a>')
    return " · ".join(out)


NO_CV_YET = ("below_bar", "queued")


def _controls(r, token, busy=None, can_tailor=False):
    if not token:
        make = f'<code>jobhunt tailor {r["id"]}</code> ' if r["status"] in NO_CV_YET else ""
        return (f'{make}<code>jobhunt mark {r["id"]} sent</code> <code>jobhunt rate {r["id"]} 4</code>'
                '<span class="hint"> or run <b>jobhunt board</b> for buttons</span>')
    hid = f'<input type="hidden" name="token" value="{token}"><input type="hidden" name="id" value="{r["id"]}">'
    state = (busy or {}).get(r["id"], "")
    make = ""
    if state == "working":
        make = ('<p class="working" role="status">Writing the CV now. This takes a minute or two; '
                'the page refreshes by itself.</p>')
    elif can_tailor and r["status"] in NO_CV_YET:
        failed = (f'<p class="failed" role="status">Couldn\'t write it just now ({e(state[8:])}). '
                  'It stays queued for the next run, or try again.</p>') if state.startswith("failed: ") else ""
        label = "Try again" if failed else ("Write it now" if r["status"] == "queued" else "Make a CV")
        make = f'{failed}<form method="post" action="/tailor" class="ctl">{hid}<button class="go">{label}</button></form>'
    status = "".join(
        f'<button name="status" value="{s}" class="{"on" if r["status"] == s else ""}">{feedback.LABELS[s]}</button>'
        for s in SETTABLE if not (s == "ready" and not r["out_dir"]))
    stars = "".join(
        f'<button name="rating" value="{n}" class="{"on" if r["rating"] == n else ""}" '
        f'aria-label="Rate {n} out of 5">{n}</button>' for n in range(1, 6))
    return (f'{make}<form method="post" action="/mark" class="ctl">{hid}<span>Status</span>{status}</form>'
            f'<form method="post" action="/rate" class="ctl">{hid}<span>How much do you like it?</span>{stars}'
            f'<input name="note" maxlength="200" placeholder="why? (optional)" value="{e(r["note"] or "")}" '
            f'aria-label="Why, optional"></form>')


def _card(r, token, prefix, busy=None, can_tailor=False):
    rating = f'<span class="rated" title="your rating">{"●" * r["rating"]}{"○" * (5 - r["rating"])}</span>' if r["rating"] else ""
    note = f'<p class="note">Your note: {e(r["note"])}</p>' if r["note"] else ""
    return f"""<article id="j-{r['id']}" class="{e(r['status'])}"><div class="top"><span class="score">{r['score'] if r['score'] is not None else '–'}</span>
<div><h3>{e(r['title'])}</h3><div class="co">{e(r['company'])} · {e(r['location'] or '')} · {e(r['loc_class'] or '')}/{e(r['work_mode'] or '')}</div></div>
<span class="tag">{e(feedback.LABELS.get(r['status'], r['status']))} {rating}</span></div>
<p>{e(r['reason'] or '')}</p><p class="sal">{e(r['salary'] or '')}</p>{note}<p class="links">{_links(r, prefix)}</p>
{_controls(r, token, busy, can_tailor)}</article>"""


def page(db, token=None, busy=None, can_tailor=False):
    """The dashboard HTML. With a token it is the interactive version served by `jobhunt board`."""
    prefix = "/out/" if token else ""
    busy = busy or {}
    working = any(v == "working" for v in busy.values())
    q = lambda where, order="score DESC": db.execute(f"SELECT * FROM jobs WHERE {where} ORDER BY {order}").fetchall()
    sections = [
        ("Ready to send", q("status='ready'", "updated DESC, score DESC"), True),
        ("In progress", q("status IN ('sent','responded','interview','offer')",
                          "CASE status WHEN 'offer' THEN 0 WHEN 'interview' THEN 1 WHEN 'responded' THEN 2 ELSE 3 END, status_at DESC"), True),
        ("Waiting for a CV", q("status='queued'"), True),
        ("Closed", q("status IN ('rejected','skipped')", "status_at DESC, updated DESC"), False),
        ("Worth a look: below your bar, no CV yet", q("status='below_bar' AND (score >= 55 OR rating IS NOT NULL)",
                                                       "score DESC, updated DESC LIMIT 30"), False),
    ]
    body = []
    for title, rows, open_ in sections:
        if not rows:
            continue
        cards = "".join(_card(r, token, prefix, busy, can_tailor) for r in rows)
        open_ = open_ or any(r["id"] in busy for r in rows)
        body.append(f'<details {"open" if open_ else ""}><summary>{title} <b>{len(rows)}</b></summary>{cards}</details>')
    f = feedback.funnel(db)
    c = f["counts"]
    stats = (f"{c.get('ready', 0)} ready · {f['sent_ever']} sent · {f['heard_back']} heard back · "
             f"{c.get('interview', 0) + c.get('offer', 0)} interview · {c.get('rejected', 0)} rejected")
    P = PALETTE
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Job Hunter</title>
{'<meta http-equiv="refresh" content="8">' if working else ''}
<style>
:root {{ --bg:{P['bg']}; --card:{P['card']}; --ink:{P['ink']}; --muted:{P['muted']}; --a:{P['a']}; --b:{P['b']}; --line:{P['line']}; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:{P['dbg']}; --card:{P['dcard']}; --ink:{P['dink']}; --muted:{P['dmuted']}; --a:{P['da']}; --b:{P['db']}; --line:{P['dline']}; }} }}
body {{ background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:880px; margin:0 auto; }} h1 {{ color:var(--a); margin:0; }}
.stats {{ color:var(--muted); font-size:13px; margin:4px 0 20px; }}
details {{ margin:0 0 18px; }} summary {{ cursor:pointer; font-weight:700; color:var(--muted); padding:6px 0; font-size:14px;
  text-transform:uppercase; letter-spacing:.06em; }} summary b {{ color:var(--ink); }}
article {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:8px 0 12px; }}
article.rejected, article.skipped {{ opacity:.6; }} article.queued {{ border-style:dashed; }}
article.interview, article.offer {{ border-color:var(--b); border-width:2px; }}
.top {{ display:flex; gap:12px; align-items:flex-start; }} .top > div {{ flex:1; min-width:0; }}
h3 {{ margin:0; font-size:16px; }} .co {{ color:var(--muted); font-size:13px; }}
.score {{ font-weight:800; font-size:20px; color:var(--b); min-width:36px; }}
.tag {{ font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--a); text-align:right; }}
.rated {{ display:block; letter-spacing:1px; color:var(--b); font-size:12px; }}
p {{ margin:6px 0 0; }} .sal, .note {{ color:var(--muted); font-size:13px; }} a {{ color:var(--a); }}
code {{ display:inline-block; margin:8px 6px 0 0; font-size:11px; color:var(--muted); }} .hint {{ font-size:11px; color:var(--muted); }}
.ctl {{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-top:10px; }}
.ctl span {{ font-size:12px; color:var(--muted); min-width:9.5em; }}
.ctl button {{ font:inherit; font-size:12px; min-height:32px; min-width:32px; padding:0 10px; border-radius:999px; cursor:pointer;
  border:1px solid var(--line); background:transparent; color:var(--ink); }}
.ctl button:hover {{ border-color:var(--a); }} .ctl button.on {{ background:var(--a); border-color:var(--a); color:var(--bg); font-weight:700; }}
.ctl button.go {{ background:var(--b); border-color:var(--b); color:#fff; font-weight:700; padding:0 14px; }}
.working, .failed {{ font-size:13px; margin-top:10px; padding:8px 10px; border-radius:8px; border:1px dashed var(--b); }}
.failed {{ border-color:var(--muted); color:var(--muted); }}
.ctl input[name=note] {{ font:inherit; font-size:12px; min-height:32px; flex:1; min-width:140px; padding:0 10px; border-radius:8px;
  border:1px solid var(--line); background:transparent; color:var(--ink); }}
</style></head><body><main><h1>Job Hunter</h1>
<div class="stats">Updated {date.today().isoformat()} · {stats}</div>
{''.join(body) or '<p>No matches yet.</p>'}</main></body></html>"""


def write_static(db):
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(page(db))
    return OUT / "index.html"


# ---------------------------------------------------------------- local server
def set_status(db, jid, status):
    store.update(db, jid, status=status, status_at=store.now())


def set_rating(db, jid, rating, note=None):
    kw = {"rating": rating}
    if note is not None:
        kw["note"] = note.strip()[:200] or None
    store.update(db, jid, **kw)


def serve(port=8765, tailor=None):
    """Serve the board. `tailor(job_id)` writes one CV (it is given by the CLI, which knows the settings);
    without it the Make-a-CV button is not shown."""
    token = secrets.token_urlsafe(24)
    busy = {}            # job id -> "working" | "failed: why"
    work = queue.Queue()

    def worker():
        while True:
            jid = work.get()
            try:
                tailor(jid)
                busy.pop(jid, None)
            except BaseException as ex:      # a failed CV must never take the board down
                busy[jid] = f"failed: {str(ex)[:160] or type(ex).__name__}"

    if tailor:
        threading.Thread(target=worker, daemon=True).start()

    hosts = set()   # filled in once the port is known (port 0 means "any free port")

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", ctype="text/html; charset=utf-8", extra=()):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _ok_host(self):
            return self.headers.get("Host", "") in hosts

        def do_GET(self):
            if not self._ok_host():
                return self._send(403, b"forbidden")
            path = urlparse(self.path).path
            if path == "/":
                db = store.connect()
                return self._send(200, page(db, token, dict(busy), bool(tailor)).encode())
            if path.startswith("/out/"):
                f = (OUT / unquote(path[5:])).resolve()
                if OUT.resolve() in f.parents and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or f.suffix == ".md":
                        ctype = ("text/html" if f.suffix == ".html" else "text/plain") + "; charset=utf-8"
                    return self._send(200, f.read_bytes(), ctype)
            self._send(404, b"not found")

        def do_POST(self):
            if not self._ok_host():
                return self._send(403, b"forbidden")
            n = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(min(n, 10000)).decode("utf-8", "replace"),
                                                 keep_blank_values=True).items()}
            if not secrets.compare_digest(form.get("token", ""), token):
                return self._send(403, b"forbidden")
            db = store.connect()
            row = db.execute("SELECT id FROM jobs WHERE id=?", (form.get("id", ""),)).fetchone()
            path = urlparse(self.path).path
            if row and path == "/tailor" and tailor:
                if busy.get(row["id"]) != "working":
                    # Queued first, so the next scheduled run picks it up if this attempt fails.
                    store.update(db, row["id"], status="queued")
                    busy[row["id"]] = "working"
                    work.put(row["id"])
            elif row and path == "/mark" and form.get("status") in SETTABLE:
                set_status(db, row["id"], form["status"])
            elif row and path == "/rate" and form.get("rating") in list("12345"):
                set_rating(db, row["id"], int(form["rating"]), form.get("note"))
            elif row and path == "/rate" and "note" in form:      # Enter pressed in the note box
                store.update(db, row["id"], note=form["note"].strip()[:200] or None)
            else:
                return self._send(400, b"bad request")
            write_static(db)
            self._send(303, extra=[("Location", f"/#j-{row['id']}")])

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    port = srv.server_address[1]
    hosts.update({f"127.0.0.1:{port}", f"localhost:{port}"})
    return srv, f"http://127.0.0.1:{port}/"
