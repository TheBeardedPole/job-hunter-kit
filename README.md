# Job Hunter

The clerical half of a job search, automated. Every run it:

1. **Searches** job boards for the roles you want.
2. **Drops** the obvious misses with plain rules (wrong job title, wrong place, too old). No AI, no cost.
3. **Scores** what's left against your profile with an AI model, reading each listing to the end. This is
   where "fluent German required" in the small print gets caught.
4. **Adjusts your CV** for the few worth applying to: which job leads, which skills come first, which of the
   listing's own terms truthfully apply. You get a PDF and a short note on where you fall short.

It can only use facts from a profile file that you check and own. It can't invent a tool, a number or a job.
It does not apply for you, and by default it does not write cover notes: that part is yours.

## What you need

- Linux or macOS (Windows works without the installer: run `python -m jobhunt.cli ...` from this folder).
- Python 3 with `requests` and `PyYAML` (`pip install --user -r requirements.txt`).
- Brave, Chrome, Chromium or Edge, to make the PDF.
- One AI to do the reading and writing. See **Choosing your AI** below.

## Setup

```sh
./install.sh                                  # adds the `jobhunt` command and checks what's installed
jobhunt init --cv ~/Documents/my-cv.pdf       # builds your profile and search settings from your current CV
```

`init` asks where you live, which cities you'd commute to, and which AI to use. Then:

1. **Read `profile.yaml` line by line.** Everything in it will be stated as fact on your CVs. Fix what's wrong,
   delete what you don't want used, add what your old CV left out.
2. Skim `settings.yaml`. The search terms and title rules decide which listings you ever see.
3. `jobhunt doctor --live` checks everything, including one small real AI call.
4. `jobhunt run`. The first run is slow (there's a backlog to score). Later runs only handle new listings.
5. Happy with it? `./install.sh --timer` runs it every morning at 08:30 and keeps the board running.

## Daily use

```sh
jobhunt board                         # the dashboard with buttons, in your browser (this computer only)
jobhunt status                        # the same, in the terminal
jobhunt tailor <id-or-URL>            # a CV for any listing now, including one you found yourself
jobhunt mark <id> sent                # or: responded, interview, offer, rejected, skipped
jobhunt rate <id> 4 --note "why"      # how much you liked the offer, 1-5
jobhunt learn                         # what your ratings and outcomes are telling the scorer
```

Each match gets a folder in `out/<date>/` with the CV as PDF and `job.md`: the link, why it scored as it
did, and the gaps to be ready for in an interview.

### It learns what you like

Tell it two things as you go, with the buttons on the board or the commands above:

- **Status**: what happened. Sent, Responded, Interview, Offer, Rejected, Skipped.
- **Rating**: how much you liked the offer, 1 to 5, with an optional note on why. You can rate anything,
  including the near-misses listed under "Worth a look".

Changed your mind about a near-miss? Each one under "Worth a look" has a **Make a CV** button. It writes
the CV in the background (a minute or two) and the listing moves to "Ready to send".

Both are shown to the AI when it scores new listings. Ratings count as strong evidence of your taste.
Outcomes count as weak evidence: an interview nudges similar roles up, but a rejection never pushes a role
down, because a rejection mostly reflects who else applied. Nothing is trained and nothing is hidden:
`jobhunt learn` prints the exact text the scorer is given. Expect it to take a handful of ratings before you
notice a difference, and note that scores wobble a little from run to run anyway.

## Choosing your AI

Set in `settings.yaml` under `llm:`. There are two steps and each can use a different model: `score` runs
often on many listings, `tailor` writes the CV. Use your strongest model for `tailor`.

| `backend` | Runs on | Notes |
|---|---|---|
| `claude-cli` | [Claude Code](https://claude.com/claude-code), your Claude login | No API key. The most tested route. |
| `codex-cli` | OpenAI's Codex CLI, your ChatGPT login | No API key. |
| `gemini-cli` | Google's Gemini CLI, your Google login | No API key. Run `gemini` once first to sign in. |
| `openai-compatible` | Any `/chat/completions` endpoint | OpenAI, Gemini API, Ollama, LM Studio, OpenRouter. |

```yaml
llm:
  score:  {backend: openai-compatible, base_url: "http://localhost:11434/v1", model: llama3.1}   # Ollama, local and free
  tailor: {backend: claude-cli, model: opus}
```

For a paid API, add `api_key_env: OPENAI_API_KEY`: the *name* of an environment variable holding your key.
The key itself never goes in a file.

Things worth knowing:

- **A ChatGPT or Gemini subscription does not include the API.** The command-line tools above run on the
  subscription; the `openai-compatible` route with a hosted provider is billed separately, per use.
- **Small local models are free and private, and noticeably worse** at catching a deal-breaker buried in a
  listing and at sticking to the facts. They're fine for `score` if you check the near-misses; use something
  strong for `tailor`, and always read the CV before you send it.
- Every reply is checked against the expected format and retried if it doesn't fit. Every command-line tool is
  started read-only in an empty folder, so the text of a job listing can't make it touch your files.

**How tested is each route?** `claude-cli` has run the whole pipeline end to end. The other three are built to
each tool's documented options and pass tests against stand-ins, but have not yet been run against the real
services. If one misbehaves, `jobhunt doctor --live` will say how.

## Your data

`profile.yaml`, `settings.yaml`, `voice.md`, `data/` and `out/` hold your personal details and are excluded
from git. Listings and your profile are sent to whichever AI you chose, and nowhere else. With a local model
nothing leaves your machine except the job-board searches.

## Which job boards

Remotive, RemoteOK, Himalayas, Jobicy, Working Nomads and We Work Remotely. All 6 publish a feed made for
other programs to read, and all 6 are remote-work boards.

**It doesn't work with Pracuj.pl, No Fluff Jobs or LinkedIn, because of their terms of service.** At your own
risk, you can use an LLM to extend the tool to read those sites yourself; see *Adding a board* below.

For a single listing you found anywhere, `jobhunt tailor <URL>` reads that one page and makes a CV for it.

### Adding a board

A board is one function in `jobhunt/sources.py` that returns a list of `job(...)` records (title, company,
location, work mode, link, description) and one entry in `FETCHERS`. The 6 that ship are short and make
good patterns to copy. Check a site's terms before you point a program at it.

## Limits

- It finds and prepares. It never applies for you, and you should read every CV before you send it.
- `voice.example.md` shows how to give it your own writing style (copy it to `voice.md`).
- Built by one person for his own job search and shared as is. Issues are welcome; help is best effort.

## Support

Job Hunter is free. If it saved you some of the dull part of a job search and you'd like to support
projects like this one, you can buy me a coffee:
[buycoffee.to/adamkoscielak](https://buycoffee.to/adamkoscielak).

## Licence

GPL-3.0. See `LICENSE`.

## Tests

```sh
python3 tests/test_kit.py     # no network and no AI calls
```
