# Purpose: Offset-preserving, overlapping passages for semantic embeddings.
# Behavior: Prefer natural boundaries, preserve the complete tail, and obey model token limits.
# Usage: Literal search always uses original segments; token_count includes model prefixes.
import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    text: str


def chunk_text(
    text: str,
    *,
    target=800,
    maximum=1000,
    overlap=0.15,
    token_count: Optional[Callable[[str], int]] = None,
    max_tokens=512,
) -> list[Chunk]:
    if not 0 < target <= maximum or not 0 < overlap < 1 or max_tokens < 8:
        raise ValueError("Invalid chunk limits")
    boundaries = [m.end() for m in re.finditer(r"\n+|[。！？.!?](?:\s|$)", text)]
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + maximum)
        if end < len(text):
            natural = [
                position
                for position in boundaries
                if start + target // 2 <= position <= start + target
            ]
            end = natural[-1] if natural else min(len(text), start + target)
        if token_count:
            # Binary search a safe prefix; no embedding text is silently truncated.
            low, high = start + 1, end
            while low < high:
                middle = (low + high + 1) // 2
                if token_count(text[start:middle]) <= max_tokens:
                    low = middle
                else:
                    high = middle - 1
            end = low
            if token_count(text[start:end]) > max_tokens:
                raise ValueError("A single character exceeds the model token limit")
        chunks.append(Chunk(start, end, text[start:end]))
        if end == len(text):
            break
        start = max(start + 1, end - max(1, round((end - start) * overlap)))
    return chunks
