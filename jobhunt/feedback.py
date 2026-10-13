"""What you did with past listings, turned into guidance for scoring new ones.

Two kinds of evidence, treated differently on purpose:

  ratings   how much YOU liked an offer (1-5). Strong evidence of taste, and yours to give.
  outcomes  what happened after you applied. Weak evidence: a rejection usually says more about
            the other 200 applicants than about the fit, so it must never talk the scorer out
            of a role you would like. An interview is a better signal than a rejection.

Nothing is "trained". The examples are shown to the model that scores new listings, in plain
text, so you can read exactly what it is being told with `jobhunt learn`.
"""

PIPELINE = ["sent", "responded", "interview", "offer", "rejected"]
LABELS = {"ready": "Ready to send", "sent": "Sent", "responded": "Responded", "interview": "Interview",
          "offer": "Offer", "rejected": "Rejected", "skipped": "Skipped", "queued": "Queued",
          "below_bar": "Below your bar"}


def _line(r):
    bits = [f"{r['title']} @ {r['company']}", f"[{r['loc_class'] or '?'}, {r['work_mode'] or '?'}]"]
    if r["salary"]:
        bits.append(f"pay: {r['salary'][:40]}")
    if r["note"]:
        bits.append(f'their note: "{r["note"][:160]}"')
    elif r["reason"]:
        bits.append(f"triage said: {r['reason'][:110]}")
    return " · ".join(bits)


def preferences(db, per_group=8):
    """Text block for the scoring prompt, or "" when there is nothing to learn from yet."""
    out = []
    rated = db.execute("SELECT * FROM jobs WHERE rating IS NOT NULL ORDER BY status_at DESC, updated DESC").fetchall()
    liked = [r for r in rated if r["rating"] >= 4][:per_group]
    meh = [r for r in rated if r["rating"] == 3][:4]
    disliked = [r for r in rated if r["rating"] <= 2][:per_group]
    if rated:
        out.append("THEIR OWN RATINGS of past listings (1-5). This is strong evidence of what they want: score new "
                   "listings that resemble the liked ones higher, and ones that resemble the disliked ones lower, "
                   "and say so in the reason when it changed your score.")
        for label, rows in (("Liked", liked), ("Lukewarm", meh), ("Disliked", disliked)):
            for r in rows:
                out.append(f"  {label} ({r['rating']}/5): {_line(r)}")
    got = {s: db.execute("SELECT * FROM jobs WHERE status=? ORDER BY status_at DESC, updated DESC LIMIT ?",
                         (s, per_group)).fetchall() for s in ("offer", "interview", "responded", "rejected")}
    if any(got.values()):
        out.append("WHAT HAPPENED AFTER THEY APPLIED. Weak evidence. Roles like the ones that led to a reply or an "
                   "interview deserve a small boost. Do NOT lower a score because a similar application was "
                   "rejected or went unanswered: that is mostly noise.")
        for s in ("offer", "interview", "responded", "rejected"):
            for r in got[s]:
                out.append(f"  {LABELS[s]}: {r['title']} @ {r['company']} [{r['loc_class'] or '?'}]")
    return "\n".join(out)


def funnel(db):
    c = dict(db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
    sent_ever = sum(c.get(s, 0) for s in PIPELINE)
    heard = sum(c.get(s, 0) for s in ("responded", "interview", "offer"))
    return {"counts": c, "sent_ever": sent_ever, "heard_back": heard}
