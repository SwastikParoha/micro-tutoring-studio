"""HTML/CSS -> PDF pipeline.

Injects the Explainer's Markdown + the Problem Setter's questions into
``templates/workbook_template.html`` (Jinja2) and compiles to a print-ready
A4 PDF with WeasyPrint.

If WeasyPrint's native dependencies are unavailable, falls back to writing the
rendered HTML next to the target path so the workbook is still produced.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

import markdown as _md
from jinja2 import Environment, FileSystemLoader, select_autoescape

import config

BUSINESS_NAME = config.BUSINESS_NAME

_env = Environment(
    loader=FileSystemLoader(str(config.TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _delatex(text: str) -> str:
    """Strip LaTeX a model may sneak in, so children see plain '1/4' not $\\frac{1}{4}$."""
    import re
    if not text:
        return text
    text = re.sub(r"\\(?:d?frac|tfrac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"\1/\2", text)
    text = re.sub(r"\\times", "x", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = text.replace("$", "").replace("\\(", "").replace("\\)", "").replace("\\", "")
    text = re.sub(r"\b(\d+)\s*/\s*1\s*/\s*(\d+)\b", r"\1/\2", text)   # "1/1/100" -> "1/100"
    return text


_DIFF_LABEL = {"support": "easier", "core": "", "stretch": "challenge"}


def render_html(result: dict[str, Any], student: dict[str, Any]) -> str:
    curriculum = result["curriculum"]
    strategy = result["strategy"]

    notes_html = _md.markdown(
        _delatex(result["explanation_md"] or ""),
        extensions=["extra", "sane_lists"],
    )
    questions = []
    for q in result["problems"].get("questions", []):
        q = dict(q)
        q["prompt"] = _delatex(q.get("prompt", ""))
        q["hint"] = _delatex(q.get("hint", ""))
        q["answer"] = _delatex(q.get("answer", ""))
        q["marking_note"] = _delatex(q.get("marking_note", ""))
        questions.append(q)

    week_of = _dt.date.today().strftime("%d %b %Y")
    topic_title = (result["topic"] or curriculum.get("unit_title", "")).strip()
    topic_title = topic_title[:1].upper() + topic_title[1:] if topic_title else "This week"

    template = _env.get_template("workbook_template.html")
    return template.render(
        student_name=student.get("full_name") or "Your Pupil",
        year_group=student.get("year_group") or curriculum.get("year_group"),
        subject=(result["subject"] or "maths").title(),
        topic_title=topic_title,
        difficulty=_DIFF_LABEL.get(result.get("difficulty", "core"), ""),
        space=result.get("space", "some"),
        week_of=week_of,
        week_focus=_delatex(strategy.get("week_focus", "")),
        notes_html=notes_html,
        questions=questions,
    )


def build_pdf(result: dict[str, Any], student: dict[str, Any],
              out_path: Path | str | None = None) -> Path:
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        name = f"{_slug(student.get('full_name') or 'pupil')}_{_slug(result['topic'])}_{_dt.date.today().isoformat()}"
        out_path = config.OUTPUT_DIR / f"{name}.pdf"
    out_path = Path(out_path)

    html = render_html(result, student)
    html_path = out_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    for engine in (_pdf_weasyprint, _pdf_playwright):
        name = engine.__name__.removeprefix("_pdf_")
        try:
            engine(html_path if engine is _pdf_playwright else html, out_path)
            print(f"[pdf] Rendered with {name}.")
            return out_path
        except Exception as exc:  # noqa: BLE001
            reason = "GTK/native libraries not installed" if "libgobject" in str(exc) or \
                "cannot load library" in str(exc) else (str(exc).splitlines() or ["unavailable"])[0]
            print(f"[pdf] {name} not available ({reason}); trying next engine.")

    print(f"[pdf] No PDF engine available - rendered HTML written instead: {html_path}")
    print("[pdf] Fix: `pip install weasyprint` + GTK runtime, OR `pip install playwright && "
          "playwright install chromium`, OR open the HTML and 'Print to PDF'.")
    return html_path


def _quiet():
    """Silence stdout+stderr (Python- and OS-level) for the duration of the block.

    WeasyPrint's FFI loader prints a multi-line banner to *stdout* via a bare
    ``print()`` when native libraries are missing; this keeps the CLI clean.
    """
    import contextlib
    import io
    import os
    import sys

    @contextlib.contextmanager
    def _cm():
        saved_fds, devnull = {}, None
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            for fd in (1, 2):
                saved_fds[fd] = os.dup(fd)
        except OSError:
            yield
            return
        old_out, old_err = sys.stdout, sys.stderr
        try:
            sys.stdout.flush(); sys.stderr.flush()
            os.dup2(devnull, 1); os.dup2(devnull, 2)
            sys.stdout = sys.stderr = io.StringIO()
            yield
        finally:
            try:
                sys.stdout.flush(); sys.stderr.flush()
            except Exception:
                pass
            sys.stdout, sys.stderr = old_out, old_err
            for fd, saved in saved_fds.items():
                os.dup2(saved, fd)
                os.close(saved)
            if devnull is not None:
                os.close(devnull)

    return _cm()


def _pdf_weasyprint(html: str, out_path: Path) -> None:
    with _quiet():
        from weasyprint import HTML  # heavy native deps (Pango/Cairo/GObject)

        HTML(string=html, base_url=str(config.TEMPLATE_DIR)).write_pdf(str(out_path))


def _pdf_playwright(html_path: Path, out_path: Path) -> None:
    """Headless-Chromium fallback: honours @page CSS via print emulation.

    Navigates to the HTML as a real file:// URL (not page.set_content(), which
    loads the page at an opaque about:blank-ish origin) - Chromium refuses to
    load file:// images/assets referenced from a page that wasn't itself
    opened via file://, so any <img src="file:///..."> silently fails to
    render under set_content(). Navigating to the file directly keeps local
    image references (e.g. this screenshot-compilation doc) working.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(path=str(out_path), print_background=True, prefer_css_page_size=True)
        browser.close()


def _render_to_pdf(html: str, out_path: Path) -> Path:
    """Shared HTML->PDF with the same WeasyPrint->Chromium->HTML fallback chain."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    for engine in (_pdf_weasyprint, _pdf_playwright):
        try:
            if engine is _pdf_playwright:
                engine(html_path, out_path)
            else:
                engine(html, out_path)
            return out_path
        except Exception:  # noqa: BLE001
            continue
    return html_path


def build_invoice_pdf(invoice: dict[str, Any], student: dict[str, Any],
                      enrolment: dict[str, Any] | None, out_path: Path | str | None = None) -> Path:
    items = invoice.get("line_items_json") or []
    if out_path is None:
        out_path = config.OUTPUT_DIR / f"{_slug(invoice.get('number', 'invoice'))}.pdf"
    html = _env.get_template("invoice_template.html").render(
        business_name=BUSINESS_NAME,
        invoice=invoice, student=student, items=items,
        student_name=student.get("full_name", ""),
        number=invoice.get("number", ""),
        payment_method=(enrolment or {}).get("payment_method", "bank transfer"),
        payment_schedule=(enrolment or {}).get("payment_schedule", ""),
    )
    return _render_to_pdf(html, out_path)


def build_report_pdf(report: dict[str, Any], student: dict[str, Any],
                     scores: list | None = None, tutor_name: str = "Your tutor",
                     out_path: Path | str | None = None) -> Path:
    body_html = _md.markdown(_delatex(report.get("content_md") or ""),
                             extensions=["extra", "sane_lists"])
    if out_path is None:
        name = f"{_slug(student.get('full_name') or 'pupil')}_report_{_dt.date.today().isoformat()}"
        out_path = config.OUTPUT_DIR / f"{name}.pdf"
    html = _env.get_template("report_template.html").render(
        business_name=BUSINESS_NAME,
        student_name=student.get("full_name", ""),
        year_group=student.get("year_group"),
        school=student.get("school"),
        period=report.get("period", ""),
        created_on=report.get("created_on", _dt.date.today().isoformat()),
        scores=scores or [],
        body_html=body_html,
        tutor_name=tutor_name,
    )
    return _render_to_pdf(html, out_path)


if __name__ == "__main__":
    import json
    import sys

    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(build_pdf(data["result"], data["student"]))
