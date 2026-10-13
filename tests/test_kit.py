"""Run with:  python3 tests/test_kit.py   (no network, no AI calls; stand-ins for every backend)."""
import json
import os
import stat
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import yaml

from jobhunt import backends as b, config, store

cfg = config.merge(config.DEFAULTS, yaml.safe_load((ROOT / "settings.example.yaml").read_text()))
cfg["remote_allow"] = [*cfg["remote_allow"], cfg["you"]["country"].lower()]

# ---- rules
def cls(title, location, mode, source="remotive"):
    return store.classify(dict(title=title, location=location, work_mode=mode, source=source, posted=""), cfg)[:2]

assert cls("UX Writer", "Poland", "remote") == ("candidate", "remote")
assert cls("Content Writer", "Szczecin", "onsite", "manual") == ("candidate", "home")
assert cls("Content Writer", "Warszawa, Mokotów", "hybrid", "manual") == ("candidate", "hybrid")
assert cls("Content Writer", "Warszawa", "onsite", "manual")[0] == "filtered"
assert cls("Content Writer", "Kraków", "onsite", "manual")[0] == "filtered"
assert cls("Senior Backend Engineer", "Szczecin", "onsite", "manual")[0] == "filtered"
assert cls("Content Writer", "USA", "remote", "jobicy")[0] == "filtered"
assert cls("Content Writer", "Europe", "remote", "jobicy") == ("candidate", "remote")
assert cls("Content Writer", "Poland, Germany", "remote", "himalayas") == ("candidate", "remote")
assert cls("Content Writer", "Neuchâtel", "remote", "remoteok")[0] == "filtered"   # "eu" must match as a word only
assert cls("Content Designer", "", "remote", "remotive") == ("candidate", "remote")
from jobhunt import sources
assert sorted(sources.FETCHERS) == ["himalayas", "jobicy", "remoteok", "remotive", "weworkremotely", "workingnomads"]

# ---- answer checking
S = {"type": "object", "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100},
                                      "lang": {"type": "string", "enum": ["en", "pl"]}}, "required": ["score", "lang"]}
assert b.validate(b.coerce(b.extract_json('Sure!\n```json\n{"score": "140", "lang": "en"}\n```'), S), S) == []
assert b.coerce({"score": "140", "lang": "en"}, S)["score"] == 100
assert b.validate({"score": 1, "lang": "de"}, S) and b.validate({"lang": "en"}, S) and b.validate([], S)
assert b.extract_json('noise {"a": {"b": 1}} trailing') == {"a": {"b": 1}}
st = b.strict(S)
assert st["additionalProperties"] is False and "maximum" not in st["properties"]["score"]

# ---- retry: a backend that answers badly once, then well
calls = []
def flaky(prompt, schema, spec, timeout, cwd):
    calls.append(prompt)
    return "I think it's good" if len(calls) == 1 else '{"score": 7, "lang": "pl"}'
b.BACKENDS["flaky"] = flaky
assert b.complete("x", S, {"backend": "flaky"}) == {"score": 7, "lang": "pl"}
assert len(calls) == 2 and "rejected" in calls[1]
b.BACKENDS["never"] = lambda *a: "nope"
try:
    b.complete("x", S, {"backend": "never"}, attempts=2); raise SystemExit("should have failed")
except b.BackendError:
    pass

# ---- openai-compatible against a local stand-in that refuses strict schemas (like some local servers)
seen = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append((self.path, self.headers.get("Authorization"), body.get("response_format", {}).get("type")))
        if body.get("response_format", {}).get("type") == "json_schema":
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"unsupported"}'); return
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"choices": [{"message": {"content": '{"score": 55, "lang": "en"}'}}]}).encode())
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["KIT_TEST_KEY"] = "secret"
spec = {"backend": "openai-compatible", "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "api_key_env": "KIT_TEST_KEY"}
assert b.complete("x", S, spec) == {"score": 55, "lang": "en"}
assert [x[2] for x in seen] == ["json_schema", "json_object"] and seen[0][:2] == ("/v1/chat/completions", "Bearer secret")
try:
    b.complete("x", S, dict(spec, api_key_env="KIT_MISSING_KEY")); raise SystemExit("should have failed")
except b.BackendError as ex:
    assert "KIT_MISSING_KEY" in str(ex)
srv.shutdown()

# ---- codex-cli and gemini-cli against stand-in programs that check the options they are given
tmp = Path(tempfile.mkdtemp())
def fake(name, script):
    p = tmp / name; p.write_text("#!/usr/bin/env python3\n" + script); p.chmod(p.stat().st_mode | stat.S_IEXEC); return str(p)
codex = fake("codex", '''
import sys, json
a = sys.argv[1:]
assert a[0] == "exec" and "read-only" in a and "--skip-git-repo-check" in a, a
schema = json.load(open(a[a.index("--output-schema") + 1]))
assert schema["additionalProperties"] is False
assert sys.stdin.read().strip()
open(a[a.index("-o") + 1], "w").write('{"score": 61, "lang": "en"}')
''')
assert b.complete("x", S, {"backend": "codex-cli", "command": codex, "model": "gpt-test"}) == {"score": 61, "lang": "en"}
gemini = fake("gemini", '''
import sys, json
a = sys.argv[1:]
assert a[a.index("--approval-mode") + 1] == "plan" and a[a.index("-o") + 1] == "json", a
assert "JSON Schema" in sys.stdin.read()
print(json.dumps({"response": "Here you go:\\n```json\\n{\\"score\\": 73, \\"lang\\": \\"pl\\"}\\n```", "stats": {}}))
''')
assert b.complete("x", S, {"backend": "gemini-cli", "command": gemini}) == {"score": 73, "lang": "pl"}
unauth = fake("gemini2", 'import sys; sys.stderr.write("Error authenticating: cancelled"); sys.exit(1)')
try:
    b.complete("x", S, {"backend": "gemini-cli", "command": unauth}); raise SystemExit("should have failed")
except b.BackendError as ex:
    assert "not signed in" in str(ex)
try:
    b.complete("x", S, {"backend": "codex-cli", "command": str(tmp / "missing")}); raise SystemExit("should have failed")
except b.BackendError as ex:
    assert "not installed" in str(ex)

# ---- links on the CV can never point at the local disk
from jobhunt import render
assert render.safe_url("linkedin.com/in/me") == "https://linkedin.com/in/me"
assert render.safe_url("https://example.com/x") == "https://example.com/x"
assert render.safe_url("mailto:a@b.co") == "mailto:a@b.co"
for bad in ("file:///etc/passwd", "../notes.txt", "my portfolio", "javascript:alert(1)", "", None, "/home/me/cv.html"):
    assert render.safe_url(bad) == "", bad
cv = {"language": "en", "headline": "h", "summary": "s", "key_skills": ["a"], "extras": [], "extras_title": "",
      "experience": [{"company": "c", "role": "r", "period": "p", "bullets": ["b"]}], "languages": ["English"],
      "education": [], "selected_work": [{"label": "Work", "url": "portfolio.example.com/x"}, {"label": "Local", "url": "../x.pdf"}]}
prof = {"identity": {"name": "Sam Nowak", "location": "X", "email": "sam@example.com", "phone": "1",
                     "website": "example.com", "linkedin": "linkedin.com/in/example"}}
html = render.cv_html(cv, prof, cfg)
import re as _re
hrefs = _re.findall(r'href="([^"]*)"', html)
assert hrefs and all(h.startswith(("https://", "mailto:")) for h in hrefs), hrefs
assert "Local" in html and "../x.pdf" not in html
if render.BROWSER:
    d = Path(tempfile.mkdtemp()); (d / "cv.html").write_text(html)
    assert render.to_pdf(d / "cv.html", d / "cv.pdf")
    raw = (d / "cv.pdf").read_bytes()
    assert b"file://" not in raw and str(d).encode() not in raw
    (d / "bad.html").write_text('<a href="relative/link">x</a>')
    assert not render.to_pdf(d / "bad.html", d / "bad.pdf")      # the guard refuses to keep it

print("all kit tests passed")
