"""`jobhunt init` (build profile.yaml + settings.yaml from an existing CV) and `jobhunt doctor`."""
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

from . import backends, config, render, sources
from .store import ROOT

SETUP_SCHEMA = {
    "type": "object",
    "properties": {
        "identity": {"type": "object", "properties": {k: {"type": "string"} for k in
                     ("name", "headline", "location", "email", "phone", "website", "linkedin")},
                     "required": ["name", "headline", "location", "email", "phone", "website", "linkedin"]},
        "summary_facts": {"type": "array", "items": {"type": "string"}},
        "languages": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "level": {"type": "string"}}, "required": ["name", "level"]}},
        "experience": {"type": "array", "items": {"type": "object", "properties": {
            "company": {"type": "string"}, "role": {"type": "string"}, "period": {"type": "string"},
            "domain": {"type": "string"}, "facts": {"type": "array", "items": {"type": "string"}}},
            "required": ["company", "role", "period", "domain", "facts"]}},
        "education": {"type": "array", "items": {"type": "object", "properties": {
            "school": {"type": "string"}, "degree": {"type": "string"}, "period": {"type": "string"}},
            "required": ["school", "degree", "period"]}},
        "skills": {"type": "array", "items": {"type": "object", "properties": {
            "group": {"type": "string"}, "items": {"type": "array", "items": {"type": "string"}}},
            "required": ["group", "items"]}},
        "links": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "url": {"type": "string"}}, "required": ["label", "url"]}},
        "want": {"type": "object", "properties": {k: {"type": "string"} for k in
                 ("roles", "industries", "seniority", "dealbreakers")},
                 "required": ["roles", "industries", "seniority", "dealbreakers"]},
        "queries": {"type": "array", "items": {"type": "string"}},
        "title_include": {"type": "array", "items": {"type": "string"}},
        "title_exclude": {"type": "array", "items": {"type": "string"}},
        "title_force_include": {"type": "array", "items": {"type": "string"}},
        "places": {"type": "array", "items": {"type": "object", "properties": {
            "city": {"type": "string"}, "spellings": {"type": "array", "items": {"type": "string"}}},
            "required": ["city", "spellings"]}},
    },
}
SETUP_SCHEMA["required"] = list(SETUP_SCHEMA["properties"])

SETUP_PROMPT = """Turn this person's CV into a structured profile and a job-search plan.

PART 1: the profile. This becomes the ONLY source of facts a CV-writing tool may use, so accuracy matters more
than polish.
- Copy facts from the CV. Never invent, round up, or infer employers, dates, titles, numbers, tools or degrees.
  When the CV does not say, use an empty string or an empty list.
- `experience`: every job, newest first. `period` exactly as written. `domain`: a few words on what the employer
  does, only if the CV makes it clear. `facts`: one plain sentence per thing they did or achieved, keeping every
  number and named tool.
- `summary_facts`: 3-6 true one-line facts about them overall (years of experience, range, specialities).
- `skills`: grouped (e.g. "tools", "languages", "methods"), items as written in the CV.
- `links`: portfolio or work-sample URLs found in the CV.

PART 2: the search plan, for a job search by someone living in {country}{city_part}.
- `want.roles`: the kinds of role they should apply for, as a sentence. `want.industries`: where their history is
  strongest. `want.seniority`: the level that fits their years, e.g. "mid to senior (8 years)".
  `want.dealbreakers`: only what the CV makes obvious (e.g. languages they don't speak); otherwise "none stated".
- `queries`: 6-12 search terms to type into a job board (job titles, in English{local_lang_part}).
- `title_include`: 10-25 lowercase fragments; a listing's title must contain at least one to be considered
  (e.g. "copywrit", "content", "ux writ"). Use word stems so variants match.
- `title_exclude`: 10-30 lowercase fragments that mark a title as clearly not for them: other professions that
  share a word with theirs, internships if they are experienced, and the names of languages they do not speak.
- `title_force_include`: 0-6 fragments so specific to their profession that they should override the exclude list.
- `places`: for each of these cities: {cities}. Give `city` exactly as written here and `spellings`: the lowercase
  forms a job listing might use for it, in English and in the local language, shortened to a stem where the
  local language inflects the name (e.g. for Warsaw: "warsaw", "warsz"). Empty list if there are no cities.

CV
{cv}
"""


# ---------------------------------------------------------------- reading a CV
def read_cv(src):
    if re.match(r"https?://", src):
        r = sources.S.get(src, timeout=30)
        r.raise_for_status()
        return sources.html_to_text(r.text)
    p = Path(src).expanduser()
    if not p.exists():
        sys.exit(f"Can't find {p}")
    ext = p.suffix.lower()
    if ext == ".pdf":
        if not shutil.which("pdftotext"):
            sys.exit("Reading a PDF needs `pdftotext` (package: poppler-utils). Or save your CV as .txt or .docx.")
        text = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout
    elif ext == ".docx":
        xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8", "ignore")
        text = sources.html_to_text(re.sub(r"</w:p>", "\n", xml))
    else:
        text = p.read_text(errors="ignore")
    if len(text.strip()) < 200:
        sys.exit("That file has almost no text in it (a scanned PDF?). Try a .docx or .txt version of your CV.")
    return text


# ---------------------------------------------------------------- init
def _ask(question, default=""):
    if not sys.stdin.isatty():
        return default
    ans = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
    return ans or default


def pick_backend(a):
    if a.backend:
        b = a.backend
    else:
        found = [b for b, cmd in (("claude-cli", "claude"), ("codex-cli", "codex"), ("gemini-cli", "gemini"))
                 if shutil.which(cmd)]
        default = found[0] if found else "openai-compatible"
        print("AI backends: claude-cli, codex-cli, gemini-cli, openai-compatible (OpenAI, Gemini API, Ollama, LM Studio)")
        print(f"Found on this machine: {', '.join(found) or 'none of the command-line tools'}")
        b = _ask("Which one", default)
    if b not in backends.BACKENDS:
        sys.exit(f"Unknown backend {b!r}.")
    spec = {"backend": b}
    if b == "openai-compatible":
        spec["base_url"] = a.base_url or _ask("Endpoint address (Ollama is http://localhost:11434/v1)")
        spec["model"] = a.model or _ask("Model name")
        key = a.api_key_env if a.api_key_env is not None else _ask("Name of the environment variable holding your API key (blank for none)")
        if key:
            spec["api_key_env"] = key
        return spec, dict(spec)
    if a.model:
        spec["model"] = a.model
        return spec, dict(spec)
    if b == "claude-cli":  # cheap model for triage, the best writer for the CV
        return dict(spec, model="sonnet"), dict(spec, model="opus")
    return spec, dict(spec)


def dump(v):
    return yaml.safe_dump(v, allow_unicode=True, sort_keys=False, default_flow_style=True, width=100000).strip().removesuffix("...").strip()


def block(v, indent=0):
    text = yaml.safe_dump(v, allow_unicode=True, sort_keys=False, width=100)
    return "".join(" " * indent + line + "\n" for line in text.splitlines())


def cmd_init(a):
    if (config.SETTINGS.exists() or config.PROFILE.exists()) and not a.force:
        sys.exit("profile.yaml or settings.yaml already exists. Use --force to replace them.")
    cv_src = a.cv or _ask("Path or web address of your current CV (.pdf, .docx, .txt)")
    if not cv_src:
        sys.exit("A CV is needed to build your profile:  jobhunt init --cv path/to/your-cv.pdf")
    country = a.country or _ask("Country you live in (in English, e.g. Poland)")
    city = a.city if a.city is not None else _ask("Your city (blank to search remote only)")
    hybrid = a.hybrid if a.hybrid is not None else _ask("Other cities you'd commute to for a hybrid job, comma-separated (blank for none)")
    langs = a.languages or _ask("Languages you can apply in, as codes, best first", "en")
    if not country:
        sys.exit("The country is required.")
    langs = [l.strip().lower() for l in langs.split(",") if l.strip()]
    score_spec, tailor_spec = pick_backend(a)

    cities = [c.strip() for c in (hybrid or "").split(",") if c.strip()]
    all_cities = ([city] if city else []) + cities

    print("Reading your CV...")
    cv_text = read_cv(cv_src)
    print(f"Building your profile and search plan with {tailor_spec['backend']} (a minute or two)...")
    local_langs = [config_lang for config_lang in langs if config_lang != "en"]
    from .llm import LANG_NAMES
    out = backends.complete(
        SETUP_PROMPT.format(country=country, city_part=f", in or near {city}" if city else "",
                            local_lang_part=(" and in " + " and ".join(LANG_NAMES.get(l, l) for l in local_langs)) if local_langs else "",
                            cities=", ".join(all_cities) or "(none)",
                            cv=cv_text[:30000]),
        SETUP_SCHEMA, tailor_spec, timeout=900)

    idt = {k: v for k, v in out["identity"].items()}
    for k in ("website", "linkedin"):   # full addresses, so they become real links on the CV
        idt[k] = render.safe_url(idt[k]) or idt[k]
    for link in out["links"]:
        link["url"] = render.safe_url(link["url"]) or link["url"]
    profile = {
        "identity": idt,
        "languages": out["languages"],
        "summary_facts": out["summary_facts"],
        "experience": out["experience"],
        "education": out["education"],
        "skills": {(g["group"] or "other").lower(): g["items"] for g in out["skills"]},
        "links": out["links"],
    }
    config.PROFILE.write_text(
        "# Your profile: the ONLY facts the CV writer may use. Built by `jobhunt init` from your CV.\n"
        "# READ IT THROUGH. Fix anything wrong, delete anything you don't want used, add what is missing.\n"
        "# Optional extras the writer obeys: a `note:` on any job (e.g. \"under NDA, keep generic\"),\n"
        "# and a top-level `positioning:` paragraph on how you want to be pitched.\n\n"
        + yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=110))

    spell = {p["city"].strip().lower(): [x.lower().strip() for x in p["spellings"] if x.strip()] for p in out["places"]}
    names = lambda c: list(dict.fromkeys([*spell.get(c.lower(), []), c.lower()]))  # the plain name always last
    lower = lambda xs: [x.lower().strip() for x in xs if x.strip()]
    config.SETTINGS.write_text(f"""# Job Hunter settings, written by `jobhunt init`. Edit freely; re-read on every run.

you:
  country: {dump(country)}            # a remote job must be open to this country
  home_city: {dump(names(city) if city else [])}          # spellings of your city as listings write it (add local-language forms)
  home_label: {dump(city or "")}            # how to name it in plain text
  hybrid_cities:                   # cities you'd commute to for a hybrid role, only if the job is really good
{block([{"label": c, "names": names(c)} for c in cities], 4) if cities else "    []\n"}
# What the AI is told you want. Plain sentences; change them whenever your aim changes.
want:
{block(out["want"], 2)}
# Search terms typed into each job board (one request per term per board).
queries: {dump(out["queries"])}

# A listing's TITLE must contain one of these (lowercase fragments) to be looked at at all.
title_include: {dump(lower(out["title_include"]))}
# ...and none of these, unless it also matches title_force_include. Prefix "re:" for a regular expression.
title_exclude: {dump(lower(out["title_exclude"]))}
title_force_include: {dump(lower(out["title_force_include"]))}

# The score (0-100) a listing needs before a CV is written for it, by where the job is.
min_score:
  remote: 70
  home: 65
  hybrid: 85

# A remote listing on an international board must mention one of these (your country is added automatically).
remote_allow: [worldwide, anywhere, global, europe, emea, eu, cet]

limits:
  score_batch: 20          # listings per scoring request
  max_scored_per_run: 120  # newest first; the rest waits for the next run
  max_tailored_per_run: 6  # CVs per run; extra matches queue for the next run
  max_age_days: 21

# Which AI does the work. `score` runs often on many listings: a cheap or local model is fine.
# `tailor` writes the CV: use the strongest model you have.
# backends: claude-cli | codex-cli | gemini-cli | openai-compatible
# openai-compatible also takes base_url, and api_key_env (the NAME of an environment variable, never the key).
llm:
  score: {dump(score_spec)}
  tailor: {dump(tailor_spec)}

cv:
  languages: {dump(langs)}        # languages you can apply in; the first is the default
  accent: "#1F4E79"         # headings and your name
  accent2: "#B45309"        # company names and bullets
  consent_clause: ""        # a sentence printed at the foot of CVs for employers in your country (e.g. Poland's RODO clause)
  cover_note: false         # true to have a short cover note drafted too

# Job boards. All of these publish a feed made for this.
sources:
  remotive: true
  remoteok: true
  himalayas: true
  jobicy: true
  workingnomads: true
  weworkremotely: true
""")
    yaml.safe_load(config.SETTINGS.read_text())  # fail loudly now rather than on the first run
    print(f"\nWrote {config.PROFILE.name} ({len(profile['experience'])} jobs) and {config.SETTINGS.name}.")
    print("Next:\n  1. Open profile.yaml and check every line. The tool will state these as facts about you.")
    print("  2. Skim settings.yaml: the search terms and title rules decide what you get to see.")
    print("  3. jobhunt doctor      (checks everything is installed)")
    print("  4. jobhunt run         (first run takes a while: it has a backlog to score)")


# ---------------------------------------------------------------- doctor
def cmd_doctor(a):
    ok = True

    def line(good, text, fix=""):
        nonlocal ok
        ok = ok and good
        print(f"  {'ok ' if good else 'FIX'}  {text}{'' if good or not fix else '  ->  ' + fix}")

    print("Job Hunter check")
    line(config.PROFILE.exists(), "profile.yaml", "jobhunt init --cv your-cv.pdf")
    line(config.SETTINGS.exists(), "settings.yaml", "jobhunt init --cv your-cv.pdf")
    line(bool(render.BROWSER), f"browser for PDFs ({render.BROWSER or 'none found'})",
         "install Brave, Chrome, Chromium or Edge")
    if config.PROFILE.exists() and config.SETTINGS.exists():
        cfg = config.load()
        for step in ("score", "tailor"):
            spec = cfg["llm"][step]
            label = f"AI for {step}: {spec.get('backend')} {spec.get('model', '')}".rstrip()
            if not backends.available(spec):
                line(False, label, "not installed or not fully configured")
            elif a.live:
                try:
                    r = backends.complete('Reply with {"ok": true}.', {"type": "object", "properties": {
                        "ok": {"type": "boolean"}}, "required": ["ok"]}, spec, timeout=180, attempts=2)
                    line(r.get("ok") is True, label + " (answered)")
                except backends.BackendError as ex:
                    line(False, label, str(ex))
            else:
                line(True, label + " (installed; add --live to test a real call)")
        on = [n for n, v in cfg["sources"].items() if v]
        line(bool(on), f"job boards: {', '.join(on)}")
    print("All good." if ok else "Fix the lines marked FIX, then run this again.")
    return 0 if ok else 1
