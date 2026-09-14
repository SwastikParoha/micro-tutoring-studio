# Micro-Tutoring Studio

Backend and multi-agent pipeline for a small, local primary-school tutoring
business. It turns a tutor's demo-lesson assessment into a **personalised,
print-ready weekly PDF workbook**, grounded in the **official UK National
Curriculum** (Oak National Academy).

Subjects covered: **maths, English, science, geography, history** (plus art,
computing, music, PE, DT, languages) — Key Stages 1 and 2.

---

## Get started

```bash
pip install -r requirements.txt

python main.py setup      # one-time: downloads curriculum data (~40 MB) + creates the database
python main.py serve      # opens the web interface in your browser
```

No API keys needed to run — see [Optional API keys](#optional-api-keys).

### The web interface (for tutors — no command line)

`python main.py serve` starts a small local web app at **http://127.0.0.1:5000**
and opens it for you. From there you can:

* **Add a pupil** — paste the demo-lesson notes *and/or upload a photo* of their
  work; the AI reads the photo and works out where they are
* **Log a weekly result** — on each pupil's page: type a score, or upload a photo
  of their finished practice. The AI marks it and updates the suggested difficulty
* **New workbook** — pick the pupil, subject, Key Stage and difficulty (pre-set
  from their recent results), browse or type a topic, click *Build workbook*
* **Download** the finished PDF, and see each pupil's score history and level

Everything runs on your own machine. Photos and notes go only to Google (Gemini)
if you've added a key, and nowhere else. Close the terminal to stop it.

### The workbook

Each PDF is **for the pupil only** — no tutor notes on it. Two parts:

1. **Revision notes** (1 page) — the big idea, steps to remember, key words, "easy
   to get wrong", and a quick self-check. Written for a 7–9 year old to read alone
   and revise before a school test.
2. **Practice** — 5–10 questions, each with a half-page of ruled working space,
   then an answers page at the back ("try first, then check").

**Difficulty** is *support* (easier), *core* (year-group standard) or *stretch*
(a challenge). It's chosen automatically from the pupil's recent scores — over
~85% moves to stretch, under ~55% drops to support — or set it yourself.

### Or use the terminal

```bash
python main.py demo       # watch it build a workbook for a sample pupil
python main.py doctor     # check everything is wired up
```

---

## Everyday use

```bash
# 1. Add a pupil from demo-lesson notes and/or a photo of their work
python main.py onboard --notes-file notes.txt
python main.py onboard --name "Ava" --year 3 --image ava_worksheet.jpg

# 2. See what you can teach
python main.py topics --subject science --keystage 2 --search "water cycle"

# 3. Build this week's workbook  (difficulty auto-set from recent results)
python main.py generate-week --student "Ava Patel" --topic "Rivers" --keystage 2 --subject geography
python main.py generate-week --student "Ava Patel" --topic "Fractions" --difficulty stretch

# 4. After the week - log how they did (updates the difficulty for next time)
python main.py log-result --student "Ava Patel" --score 82
python main.py log-result --student "Ava Patel" --image ava_week2.jpg

# CRM
python main.py students
python main.py student "Ava Patel"
```

Every command has `--help`. Run `python main.py` on its own for a guided overview.

---

## How it works

```
  onboard:   tutor notes ──►  ASSESSOR  ──►  structured JSON profile  ──►  SQLite CRM

  generate-week:
     topic + Key Stage
        │
        ▼
   ┌──────────┐   ┌────────────────┐   ┌───────────┐   ┌────────────────┐
   │ INGESTOR │──►│ CURRICULUM     │──►│ EXPLAINER │──►│ PROBLEM SETTER │
   │ (Oak)    │   │ MAPPER         │   │ (Markdown)│   │ 70% baseline / │
   │objectives│   │ strategy +     │   │           │   │ 30% targeted   │
   │+ misconc.│   │ personalisation│   │           │   │                │
   └──────────┘   └────────────────┘   └───────────┘   └────────────────┘
        └────────────  grounding packet passed to every agent  ───────────┘
                                      │
                                      ▼
                   HTML (Jinja2)  ──►  PDF (WeasyPrint / Chromium)
                                      │
                                      ▼
                     outputs/<pupil>_<topic>_<date>.pdf  +  WeeklyBooks row
```

### The five agents (`agents/`)

| Agent | File | Role |
|---|---|---|
| **The Ingestor** | `curriculum_ingestor.py` | Fetches the official learning objectives + known pupil misconceptions for the week's topic. |
| **The Assessor** | `assessor_agent.py` | Parses raw demo-lesson notes into JSON: `weak_points`, `strengths`, pupil details. |
| **The Curriculum Mapper** | `curriculum_agent.py` | UK curriculum data + pupil profile → a tailored weekly teaching strategy. |
| **The Explainer** | `explainer_agent.py` | Writes a child-friendly Markdown mini-lesson from the strategy. |
| **The Problem Setter** | `problem_setter.py` | 5–10 practice questions: 70% at the UK baseline, 30% targeting the pupil's weak points. |

Orchestration: `agents/crew.py` runs a strict sequential hand-off. By default it
calls the agent modules directly; pass `--crewai` to run the same roles through a
CrewAI `Crew` (`Process.sequential`).

### Where the curriculum comes from

The Ingestor tries three sources, in order:

1. **Oak National Academy API (live)** — needs `OAK_API_KEY`.
2. **Oak National Academy bulk dataset (local, offline)** — a 40 MB SQLite file
   downloaded by `python main.py setup` from the
   [Oak Curriculum Ontology release](https://github.com/oaknational/oak-curriculum-ontology/releases)
   (Open Government Licence v3.0, no key). Same content as the API:
   50,948 key learning points, 11,207 misconceptions, all NC subjects.
3. **Bundled sample data** (`data/oak_mock_curriculum.json`) — a handful of units
   per subject so the app still runs if the download hasn't happened.

`python main.py doctor` and `config` tell you which source is active.

### With vs. without a Gemini key

| | Assessor | Mapper / Explainer / Problem Setter |
|---|---|---|
| **`GEMINI_API_KEY` set** | Gemini 1.5 Pro parses the notes | Gemini writes the strategy, lesson and questions |
| **No key** | deterministic keyword parser | deterministic generators that present the real Oak objectives, vocabulary (with Oak's own definitions) and misconceptions, and are honest that the tutor teaches the concept in the session |

Either way the workbook is produced and is grounded in the same curriculum data.

---

## Optional API keys

Create `.env` (`setup` does this) and fill in what you have:

| Var | Where to get it | Effect |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | richer, fully-written lessons and questions |
| `GEMINI_MODEL` | — | defaults to `gemini-3.5-flash`; try `gemini-flash-latest` or `gemini-pro-latest`. If one model is out of daily quota the app auto-tries a couple of alternates. |
| `OAK_API_KEY` | https://open-api.thenational.academy/docs/about-oaks-api/api-keys | live curriculum API instead of the local dataset |

`.env.example` has all the settings with comments.

> **Gemini free tier:** a free AI Studio key is capped at ~20 requests/day. One
> workbook uses 2–3 requests, so you'll get ~7 AI-written workbooks per day before
> it switches back to the built-in generator until the quota resets. Enable
> billing in Google AI Studio for higher limits. `python main.py doctor` tells you
> if the key is currently working.

---

## PDF engine

`WeasyPrint` is tried first (needs the GTK runtime — easy on Linux/macOS, extra
setup on Windows). If it's unavailable the pipeline automatically falls back to
**headless Chromium via Playwright** (`playwright install chromium`), and failing
that writes the rendered HTML so you can "Print to PDF" from a browser. `doctor`
reports which engine you have.

---

## Database (`database/schema.sql`)

| Table | Purpose |
|---|---|
| `Students` | CRM: name, year group, key stage, interests, guardian contact |
| `Assessments` | one row per demo: raw notes, score, strengths/weak-points JSON, full Assessor output |
| `WeeklyBooks` | one row per workbook: curriculum packet, strategy, explanation, questions, PDF path |

---

## Layout

```
agents/
  curriculum_ingestor.py   The Ingestor  (3-tier retrieval, normalises to CurriculumPacket)
  oak_local.py             offline queries against the Oak bulk SQLite dataset
  assessor_agent.py        The Assessor
  curriculum_agent.py      The Curriculum Mapper
  explainer_agent.py       The Explainer
  problem_setter.py        The Problem Setter
  crew.py                  sequential orchestration (direct + CrewAI)
  llm.py                   Gemini helper
  textutil.py              shared text tidy-ups for the no-LLM generators
database/   schema.sql + db_manager.py (CRUD) + tutoring.db (created)
templates/  workbook_template.html   (kid-friendly A4 print CSS)
data/       oak_mock_curriculum.json   +   oak-curriculum.sqlite (downloaded)
outputs/    generated .pdf / .html / .json
config.py   env-driven configuration
pdf_builder.py   Markdown + questions -> HTML (Jinja2) -> PDF
main.py     CLI  (setup / serve / demo / doctor / onboard / generate-week / ...)
webapp.py   Flask web interface  (python main.py serve)
templates/web/   web pages + style.css
tests/test_smoke.py   end-to-end smoke tests  (python tests/test_smoke.py)
```

---

## Tests

```bash
python tests/test_smoke.py        # or: python -m pytest
```

Covers the offline retrieval tiers, all five subjects, the Oak dataset queries,
the deterministic pipeline end-to-end, and the Gemini code paths (stubbed, no
network).
