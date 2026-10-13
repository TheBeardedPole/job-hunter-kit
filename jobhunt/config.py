"""Load settings.yaml on top of sane defaults."""
import copy
import sys

import yaml

from .store import ROOT

SETTINGS = ROOT / "settings.yaml"
PROFILE = ROOT / "profile.yaml"

DEFAULTS = {
    "you": {"country": "", "home_city": [], "home_label": "", "hybrid_cities": []},
    "want": {},
    "queries": [],
    "title_include": [],
    "title_exclude": [],
    "title_force_include": [],
    "min_score": {"remote": 70, "home": 65, "hybrid": 85},
    "remote_allow": ["worldwide", "anywhere", "global"],
    "limits": {"score_batch": 20, "max_scored_per_run": 120, "max_tailored_per_run": 6, "max_age_days": 21},
    "llm": {"score": {"backend": "claude-cli", "model": "sonnet"},
            "tailor": {"backend": "claude-cli", "model": "opus"}},
    "cv": {"languages": ["en"], "accent": "#1F4E79", "accent2": "#B45309", "consent_clause": "", "cover_note": False},
    "sources": {"remotive": True, "remoteok": True, "himalayas": True, "jobicy": True,
                "workingnomads": True, "weworkremotely": True},
}


def merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load():
    if not SETTINGS.exists() or not PROFILE.exists():
        sys.exit("Not set up yet. Run:  jobhunt init --cv path/to/your-cv.pdf")
    cfg = merge(DEFAULTS, yaml.safe_load(SETTINGS.read_text()) or {})
    you = cfg["you"]
    if isinstance(you.get("home_city"), str):
        you["home_city"] = [you["home_city"]]
    if not you.get("country"):
        sys.exit("settings.yaml: `you.country` is empty. Set the country you live in.")
    if not cfg["cv"].get("languages"):
        cfg["cv"]["languages"] = ["en"]
    # A remote job that names your own country is always open to you.
    cfg["remote_allow"] = list(dict.fromkeys([*cfg["remote_allow"], you["country"].lower()]))
    if not cfg["title_include"]:
        sys.exit("settings.yaml: `title_include` is empty, so every listing would be dropped. Add the job titles you want.")
    return cfg
