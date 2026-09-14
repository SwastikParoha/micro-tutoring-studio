# Micro-Tutoring Studio

This started as a way to stop hand-writing worksheets every week for a small
tutoring business. It's grown into most of what that business actually
needs day to day: a place to register pupils and track how they're doing, an
AI pipeline that turns a quick assessment into a proper personalised
workbook, billing and invoices, a booking calendar, and a portal parents can
log into to see their own kid's plan, payments and progress.

Covers maths, English, science, geography and history (plus art, computing,
music, PE, DT and languages), Key Stages 1 and 2, grounded in the real UK
National Curriculum — not just whatever a model happens to remember about it.

## Running it

```bash
pip install -r requirements.txt
python main.py setup      # downloads the curriculum data + study material, makes the database
python main.py serve      # http://127.0.0.1:5000
```

No API key required — it works without one, just with simpler AI-written
content. First run prints an admin login in the terminal (change the
password from the Account page once you're in).

There's a CLI too if you'd rather not touch the browser (`python main.py
--help`), but honestly the web interface is what you want — a tutor isn't
going to run `generate-week --difficulty stretch` from a terminal.

## What's actually in here

**Admin side** — register a pupil, run an assessment off typed notes or a
photo of their finished work (Gemini reads the handwriting), generate a
workbook in one click, manage their plan and pricing, raise invoices, write
progress reports (AI drafts them, you edit before sending), run the
calendar and mark attendance.

**Parent side** — separate login, one family sees exactly one child. Current
plan, what's been paid, what's due, a live "in class now / finished today"
status, and self-service booking against whatever slots the tutor's made
available. Book past your plan's included classes and it gets flagged as an
extra, priced up front, and lands on the next invoice automatically.

**The workbook itself** is pupil-only — no tutor content leaking into it.
A page of revision notes (written for roughly a 7-year-old's reading level,
grounded in the actual curriculum text for that topic and year group), a
page or two of practice questions with space to work in, and the answers on
a separate page so it can be printed double-sided without the pupil ever
seeing them. Difficulty (support / core / stretch) is suggested from recent
scores, or you can just pick it.

This took a few rewrites to get right — the first version mixed teacher and
pupil content together and wasted whole pages doing basically nothing. Not
proud of that one.

## The AI pipeline

Instead of one prompt trying to do everything, it's five smaller steps that
each hand off to the next:

```
notes/photo → ASSESSOR → score, weak points, suggested difficulty

topic → INGESTOR (real curriculum data) → MAPPER (this week's focus)
      → EXPLAINER (writes the notes) → PROBLEM SETTER (writes the questions)
      → HTML → PDF
```

| Step | File | Does what |
|---|---|---|
| Assessor | `agents/assessor_agent.py` | Reads notes or a photo, scores the pupil, flags what they're struggling with |
| Ingestor | `agents/curriculum_ingestor.py` | Pulls the real curriculum objectives + common misconceptions for the topic |
| Curriculum Mapper | `agents/curriculum_agent.py` | Decides what this specific pupil needs to focus on this week |
| Explainer | `agents/explainer_agent.py` | Writes the actual revision notes, in plain language |
| Problem Setter | `agents/problem_setter.py` | 5–10 questions, ~70% standard / 30% aimed at the pupil's weak spots |

`agents/crew.py` runs these in order. `--crewai` on `generate-week` routes
the same steps through a CrewAI `Crew` instead, if you want that.

### Where the curriculum content actually comes from

Not made up, and not just Oak either — three layers, stacked:

1. **Oak National Academy** — either their live API (`OAK_API_KEY`) or the
   offline bulk dataset `setup` downloads (~40MB, Open Government Licence).
2. **The real UK Government curriculum documents** — `python main.py setup`
   also pulls down 45 actual gov.uk PDFs: the National Curriculum programme
   of study for every subject, plus real past SATs papers and mark schemes.
   Same free-to-reuse licence. This is what actually grounds the wording in
   the revision notes, not just Oak's summary of it. Browse them from
   Admin → Study material.
3. **A small bundled sample** if neither of the above has been downloaded
   yet, so the app doesn't just break with nothing set up.

`python main.py doctor` tells you what's currently active.

### With / without a Gemini key

Without one, everything still works — a deterministic generator writes
honest, curriculum-accurate (if plainer) notes and questions. With
`GEMINI_API_KEY` set, Gemini writes the strategy, the notes and the
questions, and can read photos. If the configured model runs out of its
daily free quota mid-session, it quietly tries a couple of alternate models
before giving up and falling back — you shouldn't ever see a hard failure
from this.

> Free AI Studio keys are capped at roughly 20 requests/day, and one
> workbook uses 2–3 of them. It resets daily. `doctor` will tell you if the
> key's actually responding right now.

## Config

`python main.py setup` creates `.env` for you from `.env.example` — fill in
whatever you have:

| Var | Get it from | What it changes |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com/apikey | AI-written lessons/questions instead of the built-in generator |
| `GEMINI_MODEL` | — | defaults to `gemini-flash-latest` |
| `OAK_API_KEY` | open-api.thenational.academy | live curriculum API + real lesson transcripts, instead of just the offline dataset |
| `BUSINESS_NAME` / `TUTOR_NAME` | — | shown on the workbooks, invoices, reports |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | — | your admin login, created on first run |

## PDF generation

Tries WeasyPrint first, falls back to headless Chromium (Playwright) if
WeasyPrint's not set up right (which, on Windows without GTK, it usually
isn't), and if neither works it just hands back the rendered HTML so you can
print-to-PDF from a browser instead of getting nothing. `doctor` tells you
which one it's actually using.

## Database

One SQLite file, `database/tutoring.db`. The main tables:

| Table | What's in it |
|---|---|
| `Students` | the CRM record — name, year, guardian details, status (enquiry → trial → active → paused → ended) |
| `Enrolments` | a pupil's plan — rate, classes/month, subjects, payment method |
| `Invoices` | auto-numbered, draft/sent/paid |
| `Assessments` / `WeeklyBooks` | every assessment and every generated workbook, with the full AI output stored |
| `Sessions` / `Availability` | the booking calendar |
| `Users` | logins — role (admin/parent), parents tied to exactly one student |

Schema changes are additive (`database/schema.sql` + a small migrations list
in `db_manager.py`), so running setup again on an existing database is safe.

## Deploying this somewhere

There's a full non-technical walkthrough in `docs/` for hosting this for
free and permanently (PythonAnywhere or Oracle Cloud, with a custom domain
if you want one) — the short version is: avoid Railway/Render's free tiers,
they sleep the app.

## Tests

```bash
python tests/test_smoke.py
```

Covers the curriculum pipeline end to end (with and without a real Gemini
key, the second one stubbed), the CRM/billing math, auth and access control
(a parent account genuinely cannot load another family's data — there's a
test for that specifically), and the booking/calendar logic. ~100 checks,
no network calls.

## Layout, roughly

```
agents/       the five AI steps, the curriculum lookup, the Gemini wrapper, the study-material catalog
database/     schema.sql + db_manager.py (all the DB access lives here, nowhere else)
webapp.py     the whole Flask app — admin routes, parent routes, auth
templates/    workbook_template.html (the printable PDF) + templates/web/ (the actual site)
pdf_builder.py    HTML -> PDF, with the fallback chain
main.py       the CLI, if you want it
tests/test_smoke.py
```
