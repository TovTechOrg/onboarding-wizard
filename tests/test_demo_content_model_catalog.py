"""Regression for a 2026-09-18 end-to-end finding: picking gemini +
gemini-2.5-pro in this wizard's LLM frame silently reported
gemini-flash-latest in the sibling pr-review-bot repo's demo dashboard --
this file's GEMINI_MODELS already offered gemini-2.5-pro, but pr-review-bot's
demo/model_catalog.py::MODELS_BY_PROVIDER hadn't caught up. Neither repo can
import the other's Python, so the two lists are pinned by hand on both
sides -- see demo/content.py's own comment above these constants.
"""
from demo.content import GEMINI_MODELS, GROQ_MODELS, VERTEX_MODELS


def test_model_catalog_matches_the_pinned_pr_review_bot_side():
    assert GEMINI_MODELS == ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-pro"]
    assert GROQ_MODELS == ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    assert VERTEX_MODELS == ["gemini-flash-latest", "gemini-2.5-flash"]
