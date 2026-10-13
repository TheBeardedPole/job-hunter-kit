"""AI backends. One call, `complete(prompt, schema, spec)`, four ways to make it:

  claude-cli         Claude Code, on your Claude login          (strict answer format)
  codex-cli          OpenAI Codex CLI, on your ChatGPT login    (strict answer format)
  gemini-cli         Google Gemini CLI, on your Google login    (answer checked here)
  openai-compatible  any /chat/completions endpoint: OpenAI, Gemini API, Ollama,
                     LM Studio, OpenRouter...                   (strict where supported)

`spec` is one entry from settings.yaml's `llm:` block, e.g.
    {backend: openai-compatible, model: llama3.1, base_url: http://localhost:11434/v1}

Job listings are text written by strangers, and the CLIs are agents that can run
commands. Every CLI is therefore started with its tools off or read-only, in an
empty temp folder, so a listing cannot talk it into touching your files.

Whatever the backend, the reply is validated against the schema here and the
call is retried (with the error fed back) if it does not fit.
"""
import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests


class BackendError(RuntimeError):
    pass


# ---------------------------------------------------------------- schema helpers
def validate(obj, schema, path="reply"):
    """Tiny JSON Schema check (the subset this project uses). Returns a list of problems."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            return [f"{path} should be an object"]
        errs = [f"{path}.{k} is missing" for k in schema.get("required", []) if k not in obj]
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                errs += validate(obj[k], sub, f"{path}.{k}")
        return errs
    if t == "array":
        if not isinstance(obj, list):
            return [f"{path} should be a list"]
        errs = []
        for i, item in enumerate(obj):
            errs += validate(item, schema.get("items", {}), f"{path}[{i}]")
        return errs
    if t == "string":
        if not isinstance(obj, str):
            return [f"{path} should be text"]
        if "enum" in schema and obj not in schema["enum"]:
            return [f"{path} must be one of {schema['enum']}, got {obj!r}"]
        return []
    if t == "integer":
        return [] if isinstance(obj, int) and not isinstance(obj, bool) else [f"{path} should be a whole number"]
    if t == "number":
        return [] if isinstance(obj, (int, float)) and not isinstance(obj, bool) else [f"{path} should be a number"]
    if t == "boolean":
        return [] if isinstance(obj, bool) else [f"{path} should be true or false"]
    return []


def coerce(obj, schema):
    """Forgive the harmless slips weaker models make: "85" for 85, 85.0 for 85, out-of-range scores."""
    t = schema.get("type")
    if t == "object" and isinstance(obj, dict):
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                obj[k] = coerce(obj[k], sub)
    elif t == "array" and isinstance(obj, list):
        obj = [coerce(x, schema.get("items", {})) for x in obj]
    elif t == "integer":
        if isinstance(obj, str) and re.fullmatch(r"-?\d+(\.0+)?", obj.strip()):
            obj = int(float(obj))
        elif isinstance(obj, float) and obj.is_integer():
            obj = int(obj)
        if isinstance(obj, int) and not isinstance(obj, bool):
            obj = max(schema.get("minimum", obj), min(schema.get("maximum", obj), obj))
    return obj


def strict(schema):
    """The shape OpenAI-style strict structured output insists on."""
    s = copy.deepcopy(schema)

    def walk(node):
        if not isinstance(node, dict):
            return
        node.pop("minimum", None)
        node.pop("maximum", None)
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
            for sub in node.get("properties", {}).values():
                walk(sub)
        elif node.get("type") == "array":
            walk(node.get("items"))

    walk(s)
    return s


def extract_json(text):
    """Pull the JSON object out of a reply that may be wrapped in prose or a code fence."""
    if isinstance(text, (dict, list)):
        return text
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    for candidate in ([fence.group(1)] if fence else []) + [text]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as ex:
            raise ValueError(f"reply is not valid JSON ({ex})")
    raise ValueError("reply contains no JSON object")


def schema_instruction(schema):
    return ("\n\nReply with a single JSON object that matches this JSON Schema, and nothing else "
            "(no commentary, no code fence):\n" + json.dumps(schema))


# ---------------------------------------------------------------- backends
def _run(cmd, prompt, timeout, cwd):
    try:
        return subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        raise BackendError(f"`{cmd[0]}` is not installed or not on your PATH.")
    except subprocess.TimeoutExpired:
        raise BackendError(f"`{cmd[0]}` did not answer within {timeout}s.")


def _claude_cli(prompt, schema, spec, timeout, cwd):
    cmd = [spec.get("command", "claude"), "-p", "--output-format", "json", "--json-schema", json.dumps(schema),
           "--tools", "", "--no-session-persistence", "--strict-mcp-config"]
    if spec.get("model"):
        cmd += ["--model", spec["model"]]
    p = _run(cmd, prompt, timeout, cwd)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise BackendError(f"Claude Code exited {p.returncode}: {(p.stderr or p.stdout)[-400:]}")
    if out.get("is_error") or out.get("structured_output") is None:
        raise BackendError(f"Claude Code error: {str(out.get('result'))[:400]}")
    return out["structured_output"]


def _codex_cli(prompt, schema, spec, timeout, cwd):
    schema_file, out_file = Path(cwd) / "schema.json", Path(cwd) / "reply.json"
    schema_file.write_text(json.dumps(strict(schema)))
    cmd = [spec.get("command", "codex"), "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
           "--output-schema", str(schema_file), "-o", str(out_file)]
    if spec.get("model"):
        cmd += ["-m", spec["model"]]
    p = _run(cmd, prompt, timeout, cwd)
    if not out_file.exists():
        raise BackendError(f"Codex exited {p.returncode}: {(p.stderr or p.stdout)[-400:]}")
    return out_file.read_text()


def _gemini_cli(prompt, schema, spec, timeout, cwd):
    # Plan mode is Gemini CLI's read-only mode. It has no strict answer format, so the
    # schema goes in the prompt and the reply is checked by complete().
    cmd = [spec.get("command", "gemini"), "-p", "", "-o", "json", "--approval-mode", "plan"]
    if spec.get("model"):
        cmd += ["-m", spec["model"]]
    p = _run(cmd, prompt + schema_instruction(schema), timeout, cwd)
    if "authenticat" in (p.stdout + p.stderr).lower():
        raise BackendError("Gemini CLI is not signed in. Run `gemini` once in a terminal, sign in, then try again.")
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        if p.returncode:
            raise BackendError(f"Gemini CLI exited {p.returncode}: {(p.stderr or p.stdout)[-400:]}")
        return p.stdout
    if isinstance(out, dict) and out.get("error"):
        raise BackendError(f"Gemini CLI error: {str(out['error'])[:400]}")
    return out.get("response", out) if isinstance(out, dict) else out


def _openai_compatible(prompt, schema, spec, timeout, cwd):
    if not spec.get("base_url") or not spec.get("model"):
        raise BackendError("openai-compatible needs `base_url` and `model` in settings.yaml.")
    headers = {"Content-Type": "application/json"}
    if spec.get("api_key_env"):
        key = os.environ.get(spec["api_key_env"])
        if not key:
            raise BackendError(f"Environment variable {spec['api_key_env']} is not set (it should hold your API key).")
        headers["Authorization"] = f"Bearer {key}"
    url = spec["base_url"].rstrip("/") + "/chat/completions"
    body = {"model": spec["model"],
            "messages": [{"role": "user", "content": prompt + schema_instruction(schema)}]}
    # Best first: strict schema, then plain JSON mode, then nothing (the reply is validated either way).
    formats = [{"type": "json_schema", "json_schema": {"name": "reply", "strict": True, "schema": strict(schema)}},
               {"type": "json_object"}, None]
    last = None
    for fmt in formats:
        payload = dict(body, **({"response_format": fmt} if fmt else {}))
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as ex:
            raise BackendError(f"Could not reach {url}: {ex}")
        if r.status_code == 400 and fmt is not None:
            last = r.text[:300]
            continue  # this server doesn't support that response format; try the next
        if r.status_code >= 400:
            raise BackendError(f"{url} answered {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError):
            raise BackendError(f"Unexpected reply from {url}: {r.text[:300]}")
    raise BackendError(f"{url} rejected every response format: {last}")


BACKENDS = {"claude-cli": _claude_cli, "codex-cli": _codex_cli, "gemini-cli": _gemini_cli,
            "openai-compatible": _openai_compatible}


def available(spec):
    """Cheap check that a backend's program exists, for `jobhunt doctor`."""
    b = spec.get("backend")
    if b == "openai-compatible":
        return bool(spec.get("base_url") and spec.get("model"))
    return bool(shutil.which(spec.get("command", {"claude-cli": "claude", "codex-cli": "codex",
                                                  "gemini-cli": "gemini"}.get(b, ""))))


def complete(prompt, schema, spec, timeout=1200, attempts=3):
    """Ask the configured backend; return a dict that fits `schema` or raise BackendError."""
    fn = BACKENDS.get(spec.get("backend"))
    if not fn:
        raise BackendError(f"Unknown backend {spec.get('backend')!r}. Choose one of: {', '.join(BACKENDS)}.")
    ask, problem = prompt, None
    for _ in range(attempts):
        with tempfile.TemporaryDirectory(prefix="jobhunt-llm-") as cwd:
            raw = fn(ask, schema, spec, timeout, cwd)
        try:
            obj = coerce(extract_json(raw), schema)
            errs = validate(obj, schema)
        except ValueError as ex:
            errs = [str(ex)]
        if not errs:
            return obj
        problem = "; ".join(errs[:6])
        ask = (prompt + f"\n\nYour previous reply was rejected: {problem}. "
               "Answer again, fixing exactly that.")
    raise BackendError(f"{spec.get('backend')} kept returning an unusable reply: {problem}")
