#!/bin/sh
# Set up Job Hunter on Linux: the `jobhunt` command, and (with --timer) a daily run at 08:30.
# Safe to re-run. Usage: ./install.sh [--timer]
set -e
ROOT=$(cd "$(dirname "$0")" && pwd)

ok=1
python3 -c 'import requests, yaml' 2>/dev/null || { echo "MISSING: Python 3 with requests + PyYAML  ->  pip install --user -r requirements.txt"; ok=0; }
for b in brave-browser brave chromium-browser chromium google-chrome google-chrome-stable microsoft-edge; do
    command -v "$b" >/dev/null && browser=$b && break
done
[ -n "$browser" ] || { echo "MISSING: Brave, Chrome, Chromium or Edge (used to make the PDF)"; ok=0; }
ai=""
for c in claude codex gemini ollama; do command -v "$c" >/dev/null && ai="$ai $c"; done
[ -n "$ai" ] && echo "AI tools found:$ai" || echo "note: no AI command-line tool found (claude, codex, gemini, ollama). You can still use an API endpoint; see README."
command -v pdftotext >/dev/null || echo "note: pdftotext not found. Reading a PDF CV needs it (package poppler-utils); .docx and .txt work without."

mkdir -p "$HOME/bin"
cat > "$HOME/bin/jobhunt" <<WRAP
#!/bin/sh
# Job Hunter. See $ROOT/README.md
cd "$ROOT" && exec python3 -m jobhunt.cli "\$@"
WRAP
chmod +x "$HOME/bin/jobhunt"
echo "installed: ~/bin/jobhunt -> $ROOT"
case ":$PATH:" in *":$HOME/bin:"*) ;; *) echo "note: add ~/bin to your PATH, or run it as ~/bin/jobhunt";; esac

if [ "$1" = "--timer" ]; then
    command -v systemctl >/dev/null || { echo "systemd not found; set up your own daily job that runs: jobhunt run"; exit 1; }
    mkdir -p "$HOME/.config/systemd/user"
    cp "$ROOT/systemd/job-hunter.service" "$ROOT/systemd/job-hunter.timer" "$HOME/.config/systemd/user/"
    systemctl --user daemon-reload
    systemctl --user enable --now job-hunter.timer
    echo "installed: daily run at 08:30. Turn off with: systemctl --user disable --now job-hunter.timer"
fi

[ $ok = 1 ] && echo "Next: jobhunt init --cv path/to/your-cv.pdf" || echo "Fix the MISSING items above, then: jobhunt init --cv path/to/your-cv.pdf"
