"""Measure the rewrite-class rules against the verified labels.

Reads `datasets/bilingual-seed/rewrite-classes.json`, classifies every
labelled Question with `classify_question`, and writes how often the class
and the words to keep agree, class by class, to
`artifacts/evaluations/rewrite-classes.md`. Git ignores the report. It
names cases by ID only. The exit code is 1 when any case is missed, so the
script can guard a change to the rules.
"""

import argparse
import sys
from pathlib import Path

from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    InvalidRewriteCases,
    load_rewrite_cases,
    render_rewrite_scores,
    score_rewrite_cases,
)

REPORT = Path("artifacts/evaluations/rewrite-classes.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=REWRITE_CASES_FILE)
    parser.add_argument("--output", type=Path, default=REPORT)
    arguments = parser.parse_args()
    try:
        case_set = load_rewrite_cases(arguments.cases)
    except InvalidRewriteCases as error:
        sys.stderr.write(f"rewrite cases are invalid: {error}\n")
        return 1
    rows = score_rewrite_cases(case_set)
    rendered = render_rewrite_scores(case_set, rows)
    output: Path = arguments.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 1 if any(row.missed for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
