"""CV HTML -> PDF (any Chromium-family browser), the per-job folder, and the out/index.html dashboard."""
import json
import re
import shutil
import subprocess
import unicodedata
from datetime import date
from html import escape as e
from pathlib import Path

import yaml

from .store import ROOT

OUT = ROOT / "out"


def _find_browser():
    """Any Chromium-family browser can print the CV to PDF (Linux, macOS or Windows)."""
    for b in ("brave-browser", "brave", "chromium-browser", "chromium", "google-chrome",
              "google-chrome-stable", "microsoft-edge", "msedge", "chrome"):
        if shutil.which(b):
            return shutil.which(b)
    for p in (r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if Path(p).exists():
            return p
    return None


BROWSER = _find_browser()

LABELS = {
    "en": dict(summary="Profile", skills="Key skills", experience="Experience", work="Selected work",
               languages="Languages", education="Education"),
    "pl": dict(summary="Profil", skills="Kluczowe umiejętności", experience="Doświadczenie",
               work="Wybrane realizacje", languages="Języki", education="Wykształcenie"),
    "de": dict(summary="Profil", skills="Kernkompetenzen", experience="Berufserfahrung",
               work="Ausgewählte Arbeiten", languages="Sprachen", education="Ausbildung"),
    "es": dict(summary="Perfil", skills="Competencias clave", experience="Experiencia",
               work="Trabajos destacados", languages="Idiomas", education="Formación"),
    "fr": dict(summary="Profil", skills="Compétences clés", experience="Expérience",
               work="Réalisations", languages="Langues", education="Formation"),
}

CSS = """
@page { size: A4; margin: 14mm 15mm 14mm 15mm; }
:root { --ink:#16191F; --muted:#4B5563; --accent:ACCENT; --accent2:ACCENT2; --rule:#DFE3E8; }
* { box-sizing: border-box; }
body { font-family: 'Inter', 'Noto Sans', system-ui, sans-serif; color: var(--ink); font-size: 9.6pt;
       line-height: 1.42; margin: 0; }
header { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px;
         border-bottom: 3px solid var(--accent); padding-bottom: 8px; margin-bottom: 10px; }
h1 { font-weight: 700; color: var(--accent); font-size: 23pt; line-height: 1; margin: 0 0 5px; letter-spacing: -.01em; }
.headline { font-weight: 700; font-size: 11pt; color: var(--accent2); }
.contact { text-align: right; font-size: 8.6pt; color: var(--muted); line-height: 1.5; }
.contact a { color: var(--ink); text-decoration: none; }
h2 { font-size: 8.4pt; text-transform: uppercase; letter-spacing: .12em; color: var(--accent); margin: 12px 0 5px;
     padding-bottom: 2px; border-bottom: 1px solid var(--rule); break-after: avoid; }
p { margin: 0 0 4px; }
.skills { display: flex; flex-wrap: wrap; gap: 4px 5px; }
.skills span { border: 1px solid var(--rule); background: #F6F8FA; border-radius: 3px; padding: 1px 6px; font-size: 8.6pt; }
.job { margin: 0 0 7px; break-inside: avoid; }
.job-head { display: flex; justify-content: space-between; gap: 8px; }
.job-head b { font-size: 10pt; }
.job-head .co { color: var(--accent2); font-weight: 600; }
.job-head .period { color: var(--muted); white-space: nowrap; font-size: 8.8pt; }
ul { margin: 2px 0 0; padding-left: 14px; }
li { margin: 0 0 1.5px; }
li::marker { color: var(--accent2); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 0 18px; }
a { color: var(--accent); }
.consent { margin-top: 14px; font-size: 7pt; color: var(--muted); border-top: 1px solid var(--rule); padding-top: 5px; }
"""


def slugify(s, n=40):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:n] or "job"


def _hex(v, fallback):
    return v if isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v) else fallback


def safe_url(u):
    """A link target that can never point at the local disk.

    A bare "linkedin.com/in/me" in an href is a relative path: the browser resolves it against the CV's own
    file:// address and the PDF then carries your folder path. So: keep real web and mail links, add https://
    to anything that looks like a domain, and drop the rest (it prints as plain text instead).
    """
    u = (u or "").strip()
    if re.match(r"(?i)^(https?://|mailto:)\S+$", u):
        return u
    if re.match(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/\S*)?$", u):
        return "https://" + u
    return ""


def _link(url, label):
    href = safe_url(url)
    return f'<a href="{e(href)}">{e(label)}</a>' if href else e(label)


def cv_html(cv, profile, cfg, consent=False):
    L = LABELS.get(cv["language"], LABELS["en"])
    idt = profile.get("identity", {})
    style = cfg.get("cv", {})
    css = CSS.replace("ACCENT2", _hex(style.get("accent2"), "#B45309")).replace("ACCENT", _hex(style.get("accent"), "#1F4E79"))
    short = lambda url: re.sub(r"^https?://(www\.)?", "", url or "").rstrip("/")
    contact = [e(idt.get("location", ""))]
    line2 = " · ".join(x for x in [
        f'<a href="mailto:{e(idt["email"])}">{e(idt["email"])}</a>' if idt.get("email") else "",
        e(idt.get("phone", ""))] if x)
    line3 = " · ".join(_link(idt[k], short(idt[k])) for k in ("website", "linkedin") if idt.get(k))
    contact += [x for x in (line2, line3) if x]
    exp = "".join(
        f'<div class="job"><div class="job-head"><span><b>{e(x["role"])}</b> · '
        f'<span class="co">{e(x["company"])}</span></span><span class="period">{e(x["period"])}</span></div>'
        f'<ul>{"".join(f"<li>{e(b)}</li>" for b in x["bullets"])}</ul></div>'
        for x in cv["experience"])
    lst = lambda items: "".join(f"<li>{e(x)}</li>" for x in items or [])
    work = "".join(f'<li>{_link(w["url"], w["label"])}</li>' for w in cv.get("selected_work") or [])
    extras = lst(cv.get("extras"))
    extras_block = f"<h2>{e(cv.get('extras_title') or '')}</h2><ul>{extras}</ul>" if extras else ""
    side = [(L["work"], work), (L["languages"], lst(cv.get("languages"))), (L["education"], lst(cv.get("education")))]
    side = [f"<div><h2>{t}</h2><ul>{body}</ul></div>" for t, body in side if body]
    clause = (style.get("consent_clause") or "").strip()
    return f"""<!doctype html><html lang="{cv['language']}"><head><meta charset="utf-8">
<title>{e(idt.get('name', 'CV'))} — CV</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=block" rel="stylesheet">
<style>{css}</style></head><body>
<header><div><h1>{e(idt.get('name', ''))}</h1><div class="headline">{e(cv['headline'])}</div></div>
<div class="contact">{'<br>'.join(contact)}</div></header>
<h2>{L['summary']}</h2><p>{e(cv['summary'])}</p>
<h2>{L['skills']}</h2><div class="skills">{''.join(f'<span>{e(s)}</span>' for s in cv['key_skills'])}</div>
{extras_block if cv.get('extras_first') else ''}
<h2>{L['experience']}</h2>{exp}
<div class="two">{''.join(side)}</div>
{'' if cv.get('extras_first') else extras_block}
{f'<div class="consent">{e(clause)}</div>' if consent and clause else ""}
</body></html>"""


def to_pdf(html_path, pdf_path):
    if not BROWSER:
        return False
    html_path, pdf_path = Path(html_path).resolve(), Path(pdf_path).resolve()
    subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=8000", f"--print-to-pdf={pdf_path}",
                    html_path.as_uri()], capture_output=True, timeout=120)
    if pdf_path.exists() and b"file://" in pdf_path.read_bytes():
        pdf_path.unlink()
        print(f"[render] {pdf_path.name} contained a link to a file on this computer and was not kept. "
              "Check the web addresses in profile.yaml start with https://", flush=True)
    return pdf_path.exists()


def is_local(j, cv, cfg):
    """An employer in the candidate's own country, where a consent clause is expected on the CV."""
    langs = cfg["cv"]["languages"]
    return (j["loc_class"] in ("home", "hybrid") or cfg["you"]["country"].lower() in (j["location"] or "").lower()
            or (len(langs) > 1 and cv["language"] != "en"))


def write_job(j, cv, cfg):
    profile = yaml.safe_load((ROOT / "profile.yaml").read_text())
    name = profile.get("identity", {}).get("name", "CV")
    d = OUT / date.today().isoformat() / f"{slugify(j['company'], 25)}--{slugify(j['title'])}"
    d.mkdir(parents=True, exist_ok=True)
    html_path = d / "cv.html"
    html_path.write_text(cv_html(cv, profile, cfg, consent=is_local(j, cv, cfg)))
    pdf = d / f"{slugify(name, 40).replace('-', '_')}_CV_{slugify(j['company'], 30)}.pdf"
    to_pdf(html_path, pdf)
    note = (cv.get("cover_note") or "").strip()
    if note:
        (d / "cover_note.txt").write_text(note + "\n")
    (d / "cv.json").write_text(json.dumps(cv, ensure_ascii=False, indent=2))
    gaps = "\n".join(f"- {g}" for g in cv["gaps"]) or "- none flagged"
    (d / "job.md").write_text(
        f"# {j['title']} — {j['company']}\n\n"
        f"**Apply:** {j['url']}\n\n"
        f"- Score: {j['score']} ({j['loc_class']}, {j['work_mode']}) · Source: {j['source']}\n"
        f"- Location: {j['location']}\n- Salary: {j['salary'] or 'not stated'}\n"
        f"- Job id: `{j['id']}` (mark with `jobhunt mark {j['id']} applied`)\n\n"
        f"## Fit\n{cv['fit_summary']}\n\nTriage: {j['reason']}\n\n"
        f"## Gaps to be ready for\n{gaps}\n\n"
        f"## Keywords mirrored\n{', '.join(cv['keywords_matched'])}\n\n"
        + (f"## Cover note\n{note}\n\n" if note else "")
        + f"## Listing text\n{j['description'] or ''}\n")
    return d


# ---------------------------------------------------------------- dashboard
def dashboard(db):
    """Write out/index.html. The page itself is built in board.py (which can also serve it with buttons)."""
    from . import board
    return board.write_static(db)
