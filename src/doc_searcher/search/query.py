# Purpose: One boolean query contract for candidates, verification and positive matches.
# Behavior: Parse quoted terms and FTS precedence (implicit AND, NOT, AND, OR).
# Usage: Leaves remain original literals; callers supply term matching and candidate sets.
import re
from dataclasses import dataclass


class QuerySyntaxError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Node:
    term: str = ""
    operator: str = ""
    left: "Node | None" = None
    right: "Node | None" = None

    def evaluate(self, matches):
        if not self.operator:
            return matches(self.term)
        a, b = self.left.evaluate(matches), self.right.evaluate(matches)
        if self.operator == "OR":
            return a | b
        if self.operator == "NOT":
            return a - b if isinstance(a, set) else a and not b
        return a & b

    def positives(self, matches):
        if not self.operator:
            return [self.term] if matches(self.term) else []
        if not self.evaluate(matches):
            return []
        terms = self.left.positives(matches)
        if self.operator != "NOT":
            terms += self.right.positives(matches)
        return list(dict.fromkeys(terms))

    def terms(self):
        if not self.operator:
            return [self.term]
        return list(dict.fromkeys(self.left.terms() + self.right.terms()))


@dataclass(frozen=True)
class ParsedQuery:
    root: Node


def parse_query(query: str) -> ParsedQuery:
    if query.count('"') % 2:
        raise QuerySyntaxError("unpaired_phrase")
    tokens = re.findall(r'"[^\"]*"|\S+', query)
    parts = []
    operators = {"AND", "OR", "NOT"}
    for token in tokens:
        operator = token.upper() if token.upper() in operators else None
        if operator:
            if not parts:
                raise QuerySyntaxError("operator_position")
            if isinstance(parts[-1], str):
                raise QuerySyntaxError("repeated_operator")
            parts.append(operator)
        else:
            if parts and isinstance(parts[-1], Node):
                parts.append("IMPLICIT")
            parts.append(Node(term=token.strip('"')))
    if not parts or isinstance(parts[-1], str):
        raise QuerySyntaxError("operator_position")
    for operator in ("IMPLICIT", "NOT", "AND", "OR"):
        i = 1
        while i < len(parts):
            if parts[i] == operator:
                parts[i-1:i+2] = [Node(operator=operator, left=parts[i-1], right=parts[i+1])]
            else:
                i += 2
    return ParsedQuery(parts[0])
