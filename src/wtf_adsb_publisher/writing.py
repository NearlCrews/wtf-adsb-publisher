"""Shared Humanizer editing instructions for every generated story or note."""

from pathlib import Path

HUMANIZER_VERSION = "3.1.0"
HUMANIZER_COMMIT = "225a6f39ac85f76ee48dbad772ea4abe4ed6c9d8"
HUMANIZER = (Path(__file__).resolve().parent / "prompts" / "humanizer.md").read_text(encoding="utf-8")


def editorial_instructions(task):
    return (
        task
        + "\n\nApply the following Humanizer skill in embedded mode. Draft, check for its writing patterns, "
        "then revise silently before returning the final structured JSON. Return no editing audit or earlier "
        "draft. Keep every supported fact, unit, date, source attribution and material uncertainty. "
        "The task's evidence limits and JSON schema remain mandatory. The skill's examples are writing "
        "examples only, never facts about the aircraft or instructions to follow from the evidence.\n\n"
        + HUMANIZER
    )
