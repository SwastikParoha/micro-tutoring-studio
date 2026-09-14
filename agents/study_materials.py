"""Official, free study material - the real National Curriculum documents and
real past SATs papers, straight from the UK government.

Everything in ``CATALOG`` is Crown copyright published by the Department for
Education / Standards and Testing Agency under the Open Government Licence
v3.0 - free to view, download, print and reuse (with attribution). None of
this is scraped or copied from a commercial publisher such as CGP; it is the
same statutory curriculum and the same real exam papers every English state
school already teaches from and sits.

``fetch_all()`` downloads anything missing into data/study_materials/.
``extract_text()`` pulls plain text out of the (short, clean) curriculum
PDFs so the workbook-writing agents can quote the actual wording of the
National Curriculum, not just Oak's summary of it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import config

STUDY_DIR = config.ROOT / "data" / "study_materials"

_UA = {"User-Agent": "Mozilla/5.0 (compatible; MicroTutoringStudio/1.0; +study-materials)"}

LICENSE = "Open Government Licence v3.0 - Crown copyright (Department for Education)"
SOURCE_NOTE = ("Downloaded directly from gov.uk. These are the real, current National "
               "Curriculum documents and real past national tests (SATs) - the same ones "
               "every English state school teaches from and sits, free to use under the "
               "Open Government Licence.")

# --------------------------------------------------------------------------- #
# The catalog. Every URL below was checked and resolves to a genuine gov.uk /
# assets.publishing.service.gov.uk PDF at the time it was added.
# --------------------------------------------------------------------------- #
CATALOG: list[dict[str, Any]] = [
    # ---- National Curriculum programmes of study: the statutory document
    # every state school in England is marked against, by subject. ----
    {"id": "curr-maths", "category": "curriculum", "subject": "maths", "key_stage": "1-2",
     "title": "Mathematics — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7da548ed915d2ac884cb07/"
            "PRIMARY_national_curriculum_-_Mathematics_220714.pdf"},
    {"id": "curr-english", "category": "curriculum", "subject": "english", "key_stage": "1-2",
     "title": "English — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7de93840f0b62305b7f8ee/"
            "PRIMARY_national_curriculum_-_English_220714.pdf"},
    {"id": "curr-english-glossary", "category": "curriculum", "subject": "english", "key_stage": "1-2",
     "title": "English — glossary of grammatical terms",
     "url": "https://assets.publishing.service.gov.uk/media/5a7c8e4ded915d48c24108e2/"
            "English_Glossary.pdf"},
    {"id": "curr-english-spelling", "category": "curriculum", "subject": "english", "key_stage": "1-2",
     "title": "English Appendix 1 — Spelling (statutory word lists by year group)",
     "url": "https://assets.publishing.service.gov.uk/media/5a7ccc06ed915d63cc65ce61/"
            "English_Appendix_1_-_Spelling.pdf"},
    {"id": "curr-english-gpv", "category": "curriculum", "subject": "english", "key_stage": "1-2",
     "title": "English Appendix 2 — Vocabulary, grammar and punctuation",
     "url": "https://assets.publishing.service.gov.uk/media/5a7d913aed915d3fb959486f/"
            "English_Appendix_2_-_Vocabulary_grammar_and_punctuation.pdf"},
    {"id": "curr-science", "category": "curriculum", "subject": "science", "key_stage": "1-2",
     "title": "Science — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a806ebd40f0b62305b8b1fa/"
            "PRIMARY_national_curriculum_-_Science.pdf"},
    {"id": "curr-geography", "category": "curriculum", "subject": "geography", "key_stage": "1-2",
     "title": "Geography — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7c1ecae5274a1f5cc75e97/"
            "PRIMARY_national_curriculum_-_Geography.pdf"},
    {"id": "curr-history", "category": "curriculum", "subject": "history", "key_stage": "1-2",
     "title": "History — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7c2917e5274a1f5cc762cf/"
            "PRIMARY_national_curriculum_-_History.pdf"},
    {"id": "curr-computing", "category": "curriculum", "subject": "computing", "key_stage": "1-2",
     "title": "Computing — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7c576be5274a1b00423213/"
            "PRIMARY_national_curriculum_-_Computing.pdf"},
    {"id": "curr-art", "category": "curriculum", "subject": "art", "key_stage": "1-2",
     "title": "Art and design — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7ba810ed915d4147621ca0/"
            "PRIMARY_national_curriculum_-_Art_and_design.pdf"},
    {"id": "curr-dt", "category": "curriculum", "subject": "design and technology", "key_stage": "1-2",
     "title": "Design and technology — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7ca43640f0b6629523adc1/"
            "PRIMARY_national_curriculum_-_Design_and_technology.pdf"},
    {"id": "curr-music", "category": "curriculum", "subject": "music", "key_stage": "1-2",
     "title": "Music — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7b7f8c40f0b645ba3c4b8a/"
            "PRIMARY_national_curriculum_-_Music.pdf"},
    {"id": "curr-pe", "category": "curriculum", "subject": "pe", "key_stage": "1-2",
     "title": "Physical education — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7c4edfed915d3d0e87b801/"
            "PRIMARY_national_curriculum_-_Physical_education.pdf"},
    {"id": "curr-languages", "category": "curriculum", "subject": "languages", "key_stage": "2",
     "title": "Languages — National Curriculum programme of study",
     "url": "https://assets.publishing.service.gov.uk/media/5a7b9246e5274a7318b8f889/"
            "PRIMARY_national_curriculum_-_Languages.pdf"},

    # ---- Real past national tests (SATs) - the actual exams pupils sit. ----
    {"id": "sats-2024-ks2-maths-p1", "category": "past_paper", "subject": "maths", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 Maths — Paper 1: Arithmetic",
     "url": "https://assets.publishing.service.gov.uk/media/664dc7a9bd01f5ed32794027/"
            "STA248817e_2024_ks2_mathematics_Paper1_arithmetic.pdf"},
    {"id": "sats-2024-ks2-maths-p2", "category": "past_paper", "subject": "maths", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 Maths — Paper 2: Reasoning",
     "url": "https://assets.publishing.service.gov.uk/media/664dc7b6f34f9b5a56adcc33/"
            "STA248818e_2024_ks2_mathematics_Paper2_reasoning.pdf"},
    {"id": "sats-2024-ks2-maths-p3", "category": "past_paper", "subject": "maths", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 Maths — Paper 3: Reasoning",
     "url": "https://assets.publishing.service.gov.uk/media/664dc7c14f29e1d07fadcc6f/"
            "STA248819e_2024_ks2_mathematics_Paper3_reasoning.pdf"},
    {"id": "sats-2024-ks2-maths-ms", "category": "mark_scheme", "subject": "maths", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 Maths — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/664dc7cdbd01f5ed3279402a/"
            "STA248820e_2024_ks2_mathematics_Mark_schemes.pdf"},
    {"id": "sats-2024-ks2-read-booklet", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 English Reading — reading booklet",
     "url": "https://assets.publishing.service.gov.uk/media/664dc1c9ae748c43d3793ff6/"
            "STA248811e_2024_ks2_English_reading_Reading_booklet.pdf"},
    {"id": "sats-2024-ks2-read-answer", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 English Reading — answer booklet",
     "url": "https://assets.publishing.service.gov.uk/media/664dc1d8f34f9b5a56adcc23/"
            "STA248810e_2024_ks2_English_reading_Reading_answer_booklet.pdf"},
    {"id": "sats-2024-ks2-read-ms", "category": "mark_scheme", "subject": "english", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 English Reading — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/664dc202f34f9b5a56adcc26/"
            "STA248812e_2024_ks2_English_reading_mark_schemes.pdf"},
    {"id": "sats-2024-ks2-gps-p1", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 English grammar, punctuation & spelling — Paper 1: questions",
     "url": "https://assets.publishing.service.gov.uk/media/664dbfb8bd01f5ed32794010/"
            "STA248814e_2024_ks2_English_GPS_Paper1_questions.pdf"},
    {"id": "sats-2024-ks2-gps-p2", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 English grammar, punctuation & spelling — Paper 2: spelling",
     "url": "https://assets.publishing.service.gov.uk/media/664dbfd84f29e1d07fadcc58/"
            "STA248815e_2024_ks2_English_GPS_Paper2_spelling.pdf"},
    {"id": "sats-2024-ks2-gps-ms", "category": "mark_scheme", "subject": "english", "key_stage": "2",
     "year": 2024, "title": "2024 KS2 English grammar, punctuation & spelling — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/664dc027993111924d9d397b/"
            "STA248816e_2024_ks2_English_GPS_Mark_schemes.pdf"},

    # ---- more years + key stage 1 + phonics, same official source ----
    {"id": "sats-2025-ks2-maths-p1", "category": "past_paper", "subject": "maths", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 Maths — Paper 1: Arithmetic",
     "url": "https://assets.publishing.service.gov.uk/media/682dc2d8e9440506ee9538ef/"
            "2025_KS2_mathematics_Paper1_arithmetic.pdf"},
    {"id": "sats-2025-ks2-maths-p2", "category": "past_paper", "subject": "maths", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 Maths — Paper 2: Reasoning",
     "url": "https://assets.publishing.service.gov.uk/media/682dc30b7fb7a7d9cd77515a/"
            "2025_KS2_mathematics_Paper2_reasoning.pdf"},
    {"id": "sats-2025-ks2-maths-p3", "category": "past_paper", "subject": "maths", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 Maths — Paper 3: Reasoning",
     "url": "https://assets.publishing.service.gov.uk/media/682dc339a599d03a16bff385/"
            "2025_KS2_mathematics_Paper3_reasoning.pdf"},
    {"id": "sats-2025-ks2-maths-ms", "category": "mark_scheme", "subject": "maths", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 Maths — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/682dc3717fb7a7d9cd77515b/"
            "2025_KS2_mathematics_mark_schemes.pdf"},
    {"id": "sats-2025-ks2-read-booklet", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 English Reading — reading booklet",
     "url": "https://assets.publishing.service.gov.uk/media/682dbf58baff3dab99775154/"
            "2025_KS2_English_reading_booklet.pdf"},
    {"id": "sats-2025-ks2-read-answer", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 English Reading — answer booklet",
     "url": "https://assets.publishing.service.gov.uk/media/682dbe67a599d03a16bff380/"
            "2025_KS2_English_reading_answer_booklet.pdf"},
    {"id": "sats-2025-ks2-read-ms", "category": "mark_scheme", "subject": "english", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 English Reading — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/682dbf86baff3dab99775155/"
            "2025_KS2_English_reading_mark_schemes.pdf"},
    {"id": "sats-2025-ks2-gps-p1", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 English grammar, punctuation & spelling — Paper 1: questions",
     "url": "https://assets.publishing.service.gov.uk/media/682dbbc6baff3dab99775152/"
            "2025_KS2_English_GPS_Paper1_questions.pdf"},
    {"id": "sats-2025-ks2-gps-p2", "category": "past_paper", "subject": "english", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 English grammar, punctuation & spelling — Paper 2: spelling",
     "url": "https://assets.publishing.service.gov.uk/media/682dc23aa599d03a16bff382/"
            "2025_KS2_English_GPS_Paper2_spelling.pdf"},
    {"id": "sats-2025-ks2-gps-ms", "category": "mark_scheme", "subject": "english", "key_stage": "2",
     "year": 2025, "title": "2025 KS2 English grammar, punctuation & spelling — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/682dc190baff3dab99775157/"
            "2025_KS2_English_GPS_mark_schemes.pdf"},

    {"id": "sats-2024-ks1-maths-p1", "category": "past_paper", "subject": "maths", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 Maths — Paper 1: Arithmetic",
     "url": "https://assets.publishing.service.gov.uk/media/6655bdb97b792ffff71a844e/"
            "STA248807e_2024_ks1_mathematics_Paper1_arithmetic.pdf"},
    {"id": "sats-2024-ks1-maths-p2", "category": "past_paper", "subject": "maths", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 Maths — Paper 2: Reasoning",
     "url": "https://assets.publishing.service.gov.uk/media/6655bdc316cf36f4d63ebb13/"
            "STA248808e_2024_ks1_mathematics_Paper2_reasoning.pdf"},
    {"id": "sats-2024-ks1-maths-ms", "category": "mark_scheme", "subject": "maths", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 Maths — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/6655bde1d470e3279dd332ba/"
            "STA248809e_2024_ks1_mathematics_test_mark_schemes.pdf"},
    {"id": "sats-2024-ks1-read-p1", "category": "past_paper", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 English Reading — Paper 1: prompt and answer booklet",
     "url": "https://assets.publishing.service.gov.uk/media/6655b52516cf36f4d63ebaf3/"
            "STA248800e_2024_ks1_English_reading_Paper1_reading_prompt_and_answer_booklet.pdf"},
    {"id": "sats-2024-ks1-read-p2", "category": "past_paper", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 English Reading — Paper 2: reading booklet",
     "url": "https://assets.publishing.service.gov.uk/media/6655b539dc15efdddf1a8449/"
            "STA248802e_2024_ks1_English_reading_Paper2_reading_booklet.pdf"},
    {"id": "sats-2024-ks1-read-p2-ans", "category": "past_paper", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 English Reading — Paper 2: answer booklet",
     "url": "https://assets.publishing.service.gov.uk/media/6655b544d470e3279dd33297/"
            "STA248801e_2024_ks1_English_reading_Paper2_reading_answer_booklet.pdf"},
    {"id": "sats-2024-ks1-read-ms", "category": "mark_scheme", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 KS1 English Reading — mark schemes",
     "url": "https://assets.publishing.service.gov.uk/media/6655b56b0c8f88e868d33290/"
            "STA248803e_2024_ks1_English_reading_test_mark_schemes.pdf"},

    {"id": "phonics-2024-practice", "category": "past_paper", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 Phonics Screening Check — practice sheet",
     "url": "https://assets.publishing.service.gov.uk/media/68efa3cce7b6794c076bbec3/"
            "2024_phonics_screening_check_practice_sheet.pdf"},
    {"id": "phonics-2024-pupils", "category": "past_paper", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 Phonics Screening Check — pupils' materials",
     "url": "https://assets.publishing.service.gov.uk/media/68efa3d8f159f887526bbebd/"
            "2024_phonics_screening_check_pupils_materials.pdf"},
    {"id": "phonics-2024-answers", "category": "mark_scheme", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 Phonics Screening Check — answer sheet",
     "url": "https://assets.publishing.service.gov.uk/media/6669814e43c77d8616f7602a/"
            "2024_phonics_screening_check_answer_sheet.pdf"},
    {"id": "phonics-2024-scoring", "category": "mark_scheme", "subject": "english", "key_stage": "1",
     "year": 2024, "title": "2024 Phonics Screening Check — scoring guidance",
     "url": "https://assets.publishing.service.gov.uk/media/666981ac205b335264f76031/"
            "2024_phonics_screening_check_scoring_guidance.pdf"},
]

_CATEGORY_LABEL = {
    "curriculum": "National Curriculum documents",
    "past_paper": "Real past SATs papers",
    "mark_scheme": "Official mark schemes",
}
_CATEGORY_BLURB = {
    "curriculum": "The statutory programme of study for each subject, word for word - "
                  "what every state-school pupil at this key stage must be taught.",
    "past_paper": "The actual national tests pupils have sat in previous years - key stage 1, key "
                  "stage 2 and the Year 1 phonics check. Print these for realistic timed practice. "
                  "(A couple of English reading booklets include a small number of licensed stock "
                  "photos inside the story text - those stay under the original test's own terms; "
                  "everything else is Crown copyright, OGL.)",
    "mark_scheme": "How each past paper is actually marked - useful for scoring a pupil's "
                   "practice attempt the way the real test would.",
}
_CATEGORY_ORDER = ["curriculum", "past_paper", "mark_scheme"]

# Curriculum docs are short, clean and single-column - good candidates for text
# extraction so the AI agents can quote the real wording. Past papers/mark
# schemes are multi-column exam layouts and are kept as reference PDFs only.
_EXTRACTABLE = {"curriculum"}


# --------------------------------------------------------------------------- #
def _filename(item: dict) -> str:
    return item["id"] + ".pdf"


def local_path(item: dict) -> Path:
    return STUDY_DIR / item["category"] / _filename(item)


def available(item: dict) -> bool:
    p = local_path(item)
    return p.is_file() and p.stat().st_size > 1000


def curriculum_doc_for(subject: str) -> Optional[dict[str, Any]]:
    """The National Curriculum programme-of-study catalog entry for a subject, if any."""
    for item in CATALOG:
        if item["category"] == "curriculum" and item["subject"] == subject:
            row = dict(item)
            row["downloaded"] = available(item)
            return row
    return None


def get(material_id: str) -> Optional[dict[str, Any]]:
    for item in CATALOG:
        if item["id"] == material_id:
            row = dict(item)
            row["downloaded"] = available(item)
            return row
    return None


def catalog_with_status() -> list[dict[str, Any]]:
    out = []
    for item in CATALOG:
        row = dict(item)
        row["downloaded"] = available(item)
        row["size_kb"] = round(local_path(item).stat().st_size / 1024) if row["downloaded"] else None
        out.append(row)
    return out


def grouped_catalog() -> list[dict[str, Any]]:
    """[{category, label, blurb, subjects: [{subject, items:[...]}]}] - ready for the template."""
    items = catalog_with_status()
    out = []
    for cat in _CATEGORY_ORDER:
        cat_items = [i for i in items if i["category"] == cat]
        if not cat_items:
            continue
        subjects: dict[str, list] = {}
        for i in cat_items:
            subjects.setdefault(i["subject"], []).append(i)
        subj_list = [{"subject": s, "docs": sorted(v, key=lambda x: (-(x.get("year") or 0), x["title"]))}
                     for s, v in sorted(subjects.items())]
        out.append({"category": cat, "label": _CATEGORY_LABEL[cat],
                    "blurb": _CATEGORY_BLURB[cat], "subjects": subj_list})
    return out


def counts() -> dict[str, int]:
    items = catalog_with_status()
    return {"total": len(items), "downloaded": sum(1 for i in items if i["downloaded"]),
            "missing": sum(1 for i in items if not i["downloaded"])}


def fetch_all(*, verbose: bool = True) -> dict[str, int]:
    """Download every catalog item that isn't already on disk."""
    import requests

    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    ok, failed, skipped = 0, 0, 0
    for item in CATALOG:
        dest = local_path(item)
        if available(item):
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if verbose:
                print(f"  downloading: {item['title']} ...", flush=True)
            r = requests.get(item["url"], headers=_UA, timeout=30)
            r.raise_for_status()
            dest.write_bytes(r.content)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"    failed - {exc}")
            failed += 1
    return {"downloaded": ok, "already_had": skipped, "failed": failed}


# --------------------------------------------------------------------------- #
# Text extraction, for grounding the AI agents in the real curriculum wording.
# --------------------------------------------------------------------------- #
def extract_text(item: dict) -> str:
    """Plain text from a curriculum PDF, cached next to it as a .txt file."""
    if item["category"] not in _EXTRACTABLE or not available(item):
        return ""
    pdf_path = local_path(item)
    txt_path = pdf_path.with_suffix(".txt")
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        text = "\n".join(ln.rstrip() for ln in text.splitlines() if ln.strip())
        txt_path.write_text(text, encoding="utf-8")
        return text
    except Exception:  # noqa: BLE001
        return ""


def curriculum_text_for(subject: str, *, max_chars: int = 200_000) -> str:
    """The real National Curriculum wording for a subject, trimmed to a prompt-sized chunk."""
    matches = [i for i in CATALOG if i["category"] == "curriculum" and i["subject"] == subject]
    for item in matches:
        text = extract_text(item)
        if text:
            return text[:max_chars]
    return ""


def relevant_curriculum_excerpt(subject: str, topic: str, *, year_group: int | None = None,
                                window: int = 900) -> str:
    """A chunk of the REAL National Curriculum wording most relevant to a topic - used to
    ground the AI's notes in the actual statutory document rather than a summary of it.

    The programme-of-study PDFs are laid out as one "Year N programme of study" section
    per year group, so when we know the pupil's year we search inside that section first
    (the same topic name means different, more/less advanced things in different years) and
    only fall back to a whole-document search if nothing matches there.
    """
    import re

    text = curriculum_text_for(subject)
    if not text:
        return ""
    words = [w for w in re.findall(r"[a-z]{4,}", topic.lower())]
    if not words:
        return ""

    def _find(haystack: str) -> int:
        low = haystack.lower()
        for w in words:
            pos = low.find(w)
            if pos != -1:
                return pos
        return -1

    search_text, offset = text, 0
    if year_group:
        low_full = text.lower()
        # the contents page repeats these same headings followed by just a page number -
        # skip those and use the first heading that actually starts a section of prose.
        heading = None
        for m in re.finditer(rf"year\s+{int(year_group)}\s+programme of study", low_full):
            after = text[m.end():m.end() + 30]
            if not re.match(r"^\s*\d{1,3}\s*\n", after):
                heading = m
                break
        if heading:
            start = heading.end()
            nxt = re.search(r"year\s+\d+\s+programme of study", low_full[start:])
            end = start + nxt.start() if nxt else min(len(text), start + 6000)
            if end > start:
                search_text, offset = text[start:end], start

    pos = _find(search_text)
    if pos == -1 and offset:  # nothing in this year's section - widen to the whole document
        search_text, offset, pos = text, 0, _find(text)
    if pos == -1:
        return search_text[:window].strip()
    start = max(0, pos - window // 3)
    return search_text[start:start + window].strip()
