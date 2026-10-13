"""Prompts: batch scoring and per-job CV tailoring. The backend is chosen in settings.yaml."""
import json

import yaml

from . import backends
from .store import ROOT

LLMError = backends.BackendError

LANG_NAMES = {"en": "English", "pl": "Polish", "de": "German", "es": "Spanish", "fr": "French",
              "it": "Italian", "nl": "Dutch", "pt": "Portuguese", "cs": "Czech", "uk": "Ukrainian"}


def profile_text():
    return (ROOT / "profile.yaml").read_text()


def voice_text():
    f = ROOT / "voice.md"
    return f.read_text() if f.exists() else "Plain, specific and active. No clichés, no filler."


def _as_list(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def profile_brief():
    p = yaml.safe_load(profile_text()) or {}
    idt = p.get("identity", {})
    roles = "; ".join(f"{e.get('role', '')} @ {e.get('company', '')} ({e.get('period', '')}, {e.get('domain', '')})"
                      for e in _as_list(p.get("experience")))
    skills = p.get("skills", {})
    skills = ("; ".join(f"{k}: {', '.join(map(str, _as_list(v)))}" for k, v in skills.items())
              if isinstance(skills, dict) else ", ".join(map(str, _as_list(skills))))
    langs = ", ".join(f"{l.get('name')} ({l.get('level', '')})" for l in _as_list(p.get("languages")))
    return (f"{idt.get('name', 'The candidate')}, {idt.get('location', '')}. "
            f"{' '.join(map(str, _as_list(p.get('summary_facts'))))}\nRoles: {roles}\nSkills: {skills}\n"
            f"Languages: {langs or 'not stated'}. Assume they speak no other language.")


def location_policy(cfg):
    you = cfg["you"]
    parts = [f"fully remote and open to someone living in {you['country']}"]
    if you.get("home_city"):
        parts.append(f"or based in {you.get('home_label') or you['home_city'][0].title()} (their home city)")
    hybrid = [c.get("label") or c["names"][0].title() for c in you.get("hybrid_cities") or []]
    if hybrid:
        parts.append(f"or hybrid in {', '.join(hybrid)}, but only if the role is exceptional")
    return ", ".join(parts)


# ---------------------------------------------------------------- scoring
def score_schema(cfg):
    return {
        "type": "object",
        "properties": {"results": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "reason": {"type": "string"},
                "angle": {"type": "string"},
                "language": {"type": "string", "enum": cfg["cv"]["languages"]},
            },
            "required": ["id", "score", "reason", "angle", "language"]}}},
        "required": ["results"],
    }


SCORE_PROMPT = """You are triaging job listings for a job seeker. Score each listing 0-100 for how worth applying it is.

CANDIDATE
{brief}

WHAT THEY WANT
- Roles: {roles}
- Industries: {industries}
- Seniority: {seniority}
- Dealbreakers: {dealbreakers}
- Location: {location}.
  `loc` below is a rule-based guess (remote / home / hybrid). If the text shows that remote is actually restricted
  to another country, that fixed working hours make it impractical, or that the job is really on-site elsewhere,
  score it under 20.

{feedback}

SCORING GUIDE
90-100 near-perfect role and domain match. 75-89 strong fit, worth a tailored CV. 60-74 plausible.
Under 40: wrong function, requires a language the candidate doesn't speak, a seniority far off theirs, unpaid or
commission-only, a dealbreaker, or a location that doesn't actually work. Read the whole listing: requirements
that rule the candidate out are often near the bottom.

For each listing return: id, score, reason (max 20 words, plain), angle (max 15 words: which of their experience
the CV should lead with), language (the listing's language if it is one of {languages}, otherwise "{default_lang}").

LISTINGS (JSON)
{jobs}
"""


def score_batch(jobs, cfg, feedback=""):
    want = cfg.get("want", {})
    payload = [{"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"],
                "loc": j["loc_class"], "mode": j["work_mode"], "salary": j["salary"],
                "text": (j["description"] or "")[:1500]} for j in jobs]
    prompt = SCORE_PROMPT.format(
        brief=profile_brief(), roles=want.get("roles", "see the candidate's history"),
        industries=want.get("industries", "any"), seniority=want.get("seniority", "matching their history"),
        dealbreakers=want.get("dealbreakers", "none stated"), location=location_policy(cfg),
        feedback=feedback or "(No ratings or outcomes yet.)",
        languages=cfg["cv"]["languages"], default_lang=cfg["cv"]["languages"][0],
        jobs=json.dumps(payload, ensure_ascii=False, indent=0))
    out = backends.complete(prompt, score_schema(cfg), cfg["llm"]["score"], timeout=900)
    return {r["id"]: r for r in out["results"]}


# ---------------------------------------------------------------- tailoring
def tailor_schema(cfg, cover_note):
    s = {
        "type": "object",
        "properties": {
            "language": {"type": "string", "enum": cfg["cv"]["languages"]},
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "key_skills": {"type": "array", "items": {"type": "string"}},
            "experience": {"type": "array", "items": {
                "type": "object",
                "properties": {"company": {"type": "string"}, "role": {"type": "string"},
                               "period": {"type": "string"},
                               "bullets": {"type": "array", "items": {"type": "string"}}},
                "required": ["company", "role", "period", "bullets"]}},
            "education": {"type": "array", "items": {"type": "string"}},
            "languages": {"type": "array", "items": {"type": "string"}},
            "extras_first": {"type": "boolean"},
            "extras_title": {"type": "string"},
            "extras": {"type": "array", "items": {"type": "string"}},
            "selected_work": {"type": "array", "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "url": {"type": "string"}},
                "required": ["label", "url"]}},
            "fit_summary": {"type": "string"},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "keywords_matched": {"type": "array", "items": {"type": "string"}},
        },
    }
    if cover_note:
        s["properties"]["cover_note"] = {"type": "string"}
    s["required"] = list(s["properties"])
    return s


TAILOR_PROMPT = """You are an expert CV writer adjusting a one-to-two page CV for a specific job. Keep it tight and
specific: active verbs, no fluff, no clichés ("passionate", "results-driven", "dynamic"), no first person in bullets.

HARD RULES. Breaking these is worse than a weak CV:
1. Use ONLY facts in the PROFILE. Never invent employers, dates, titles, metrics, tools, clients, degrees or
   achievements. You may rephrase, reorder, merge and choose emphasis. If the job wants something the candidate
   lacks, list it in `gaps`. Do not imply it on the CV.
2. Keep company names, roles and periods exactly as in the profile (you may translate a role name when the CV is
   not in the profile's language). Include every employer, newest first; relevant roles get 3-5 bullets, less
   relevant ones 1-2.
3. Obey every `note`, `usage` and `positioning` field in the profile: they are the candidate's own instructions
   about what may be said and how.
4. Write the CV in {language_name}. Mirror the listing's own key terms where they truthfully apply.
5. `headline`: the target role framed for this job, max 8 words; it must fit on one line. `summary`: 3-4 sentences aimed at this job.
   `key_skills`: 8-12 short items, most relevant first.
   `education`: one line per entry in the profile ("Degree, School (years)"), or empty if the profile has none.
   `languages`: one line per language in the profile ("English: native"), written in {language_name}.
   `extras_title`/`extras`: an optional section (side projects, tools, volunteering), 2-5 items, or empty. Never
   repeat languages, education or contact details there. Set `extras_first` true only when that section is the
   candidate's strongest evidence for this job, so it prints above the employment history.
   Write each item as "Name (short link if it has one): what it is, then the 1-2 facts that matter most for this
   job", in at most 45 words. A reader should take in the whole section in 20 seconds; pick, don't list everything.
   `selected_work`: 0-4 links from the PROFILE most relevant to this job (exact URLs from the profile only).
{cover_rule}7. `fit_summary`: one honest sentence for the candidate (not the employer) on why this is or isn't a strong shot.
   `gaps`: every requirement in the listing the profile does not clearly meet. `keywords_matched`: the listing's
   terms you mirrored.
8. VOICE applies to everything you write. Where it conflicts with rules 1-3, the rules win.

VOICE
{voice}

NOTE FROM TRIAGE: {angle}

PROFILE (YAML)
{profile}

JOB
Title: {title}
Company: {company}
Location: {location} ({work_mode})
Salary: {salary}
URL: {url}

{description}
"""

COVER_RULE = """6. `cover_note`: max 170 words, same language as the CV, plain text, ready to paste into an application form.
   Specific to this company and role, in the candidate's voice, signed with their first name.
"""
NO_COVER_RULE = """6. Do not write a cover note or any message to the employer.
"""


def tailor(j, cfg):
    langs = cfg["cv"]["languages"]
    lang = j["language"] if j["language"] in langs else langs[0]
    cover = bool(cfg["cv"].get("cover_note"))
    prompt = TAILOR_PROMPT.format(
        language_name=LANG_NAMES.get(lang, lang), cover_rule=COVER_RULE if cover else NO_COVER_RULE,
        voice=voice_text(), angle=j["angle"] or "none", profile=profile_text(), title=j["title"],
        company=j["company"], location=j["location"], work_mode=j["work_mode"],
        salary=j["salary"] or "not stated", url=j["url"], description=(j["description"] or "")[:14000])
    cv = backends.complete(prompt, tailor_schema(cfg, cover), cfg["llm"]["tailor"], timeout=1200)
    cv["language"] = lang
    return cv
