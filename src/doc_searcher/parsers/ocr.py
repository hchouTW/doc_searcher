# Purpose: Local, bounded OCR shared by scanned PDF and image parsers.
# Behavior: Render RGB images and invoke Tesseract without a shell, with a timeout.
# Usage: Install Tesseract and eng/chi_tra/chi_sim data; optional DOC_SEARCHER_OCR_* overrides.
#   OCR is off unless the caller enables it for the current thread with enabled_scope(True);
#   parsers then report skipped image text as "ocr_disabled" warnings.
import csv
import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from functools import lru_cache


_cancellation = threading.local()


class OCRCancelled(RuntimeError):
    pass


@contextmanager
def cancellation_scope(check):
    previous = getattr(_cancellation, "check", None)
    previous_state = getattr(_cancellation, "state", None)
    state = {"cancelled": False}
    _cancellation.check = check
    _cancellation.state = state
    try:
        yield state
    finally:
        _cancellation.check = previous
        _cancellation.state = previous_state


@contextmanager
def enabled_scope(enabled):
    previous = getattr(_cancellation, "enabled", False)
    _cancellation.enabled = bool(enabled)
    try:
        yield
    finally:
        _cancellation.enabled = previous


def is_enabled():
    return getattr(_cancellation, "enabled", False)


def check_cancelled():
    check = getattr(_cancellation, "check", None)
    if check and check():
        _cancellation.state["cancelled"] = True
        raise OCRCancelled("OCR cancelled.")


@lru_cache(maxsize=8)
def _languages(executable, data_prefix):
    result = subprocess.run(
        [executable, "--list-langs"], capture_output=True, text=True, timeout=5, check=True
    )
    return set(result.stdout.splitlines()[1:])


def recognize(pixmap) -> str:
    """Recognize one bounded pixmap. Missing runtimes/data and timeout are explicit failures."""
    check_cancelled()
    executable = os.environ.get("DOC_SEARCHER_TESSERACT") or shutil.which("tesseract")
    if not executable:
        # Finder-launched macOS apps often omit Homebrew from PATH.
        executable = next(
            (
                p
                for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract")
                if os.path.isfile(p)
            ),
            None,
        )
    if not executable:
        raise RuntimeError(
            "Tesseract is unavailable; install Tesseract and Chinese/English language data."
        )
    languages = os.environ.get("DOC_SEARCHER_OCR_LANGUAGES", "chi_tra+chi_sim+eng")
    available = _languages(executable, os.environ.get("TESSDATA_PREFIX", ""))
    missing = set(languages.split("+")) - available
    if missing:
        raise RuntimeError("Tesseract language data missing: " + ", ".join(sorted(missing)))
    timeout = max(1.0, min(120.0, float(os.environ.get("DOC_SEARCHER_OCR_TIMEOUT", "30"))))
    with tempfile.TemporaryDirectory(prefix="doc-searcher-ocr-") as directory:
        path = os.path.join(directory, "page.png")
        pixmap.save(path)
        process = subprocess.Popen(
            [executable, path, "stdout", "-l", languages, "--psm", "3", "tsv"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                check_cancelled()
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"OCR exceeded {timeout:g} seconds.")
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise RuntimeError("Tesseract failed: " + stderr[-1000:])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    words = []
    for row in csv.DictReader(io.StringIO(stdout), delimiter="\t"):
        if row.get("level") == "5" and row.get("text", "").strip():
            words.append(
                {**row, **{key: int(row[key]) for key in ("left", "top", "width", "height")}}
            )
    return OCRText(words, pixmap.width, pixmap.height)


def render(page, clip=None):
    """Render at 200 DPI, capped at 12 million RGB pixels per OCR operation."""
    import pymupdf

    area = clip or page.rect
    scale = min(200 / 72, (12_000_000 / max(1, area.width * area.height)) ** 0.5)
    return page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale), clip=clip, colorspace=pymupdf.csRGB, alpha=False
    )


def render_image(data):
    """Use original raster pixels for mixed pages; native overlays must not be OCRed twice."""
    import pymupdf

    pixmap = pymupdf.Pixmap(data)
    if pixmap.colorspace != pymupdf.csRGB:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
    if pixmap.alpha:
        pixmap = pymupdf.Pixmap(pixmap, 0)
    while pixmap.width * pixmap.height > 12_000_000:
        pixmap.shrink(1)
    return pixmap


def word_text(words):
    lines = []
    key = None
    for word in words:
        line_key = tuple(word[field] for field in ("block_num", "par_num", "line_num"))
        if line_key != key:
            lines.append(word["text"])
            key = line_key
        else:
            previous = lines[-1][-1:]
            following = word["text"][:1]

            def cjk(character):
                return bool(character) and "\u3400" <= character <= "\u9fff"

            lines[-1] += ("" if cjk(previous) and cjk(following) else " ") + word["text"]
    return "\n".join(lines)


class OCRText(str):
    def __new__(cls, words, width, height):
        instance = super().__new__(cls, word_text(words))
        instance.words = words
        instance.width = width
        instance.height = height
        return instance
