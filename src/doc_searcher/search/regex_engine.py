# Purpose: Compile and bound user-supplied regular expressions.
# What the code does:
#   - compile_user_regex() compiles the user's pattern with the third-party `regex` module,
#     which accepts Python `re` syntax and, unlike `re`, supports a per-call timeout. Patterns
#     that backtrack catastrophically (e.g. (x+x+)+y) otherwise pin a search thread forever.
#   - REGEX_TIME_BUDGET_SECONDS bounds one whole regex search; Deadline tracks what is left.
# Usage notes, dependencies, or assumptions:
#   - Requires `regex`. Literal keyword highlighting keeps using `re` (escaped, cannot backtrack).
#   - A search that exceeds the budget raises TimeoutError from `regex`; callers translate it.

import time
from typing import Any, Optional

import regex

REGEX_TIME_BUDGET_SECONDS = 10.0
RegexError = regex.error


def compile_user_regex(expression: str, match_case: bool = False, whole_word: bool = False) -> Any:
    """Compile a user regex (raises RegexError on bad syntax)."""
    if whole_word:
        expression = rf"(?<!\w)(?:{expression})(?!\w)"
    return regex.compile(expression, 0 if match_case else regex.IGNORECASE)


class Deadline:
    """Remaining time of a budget, in the form regex's timeout= argument expects."""

    def __init__(self, seconds: Optional[float] = None):
        self.seconds = REGEX_TIME_BUDGET_SECONDS if seconds is None else seconds
        self.expires = time.monotonic() + self.seconds

    def remaining(self) -> float:
        left = self.expires - time.monotonic()
        if left <= 0:
            raise TimeoutError("regex time budget exhausted")
        return left

    @staticmethod
    def timeout_kwargs(deadline: Optional["Deadline"]) -> dict:
        return {} if deadline is None else {"timeout": deadline.remaining()}
