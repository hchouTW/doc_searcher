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

    def _values(self, matches):
        values, terms, stack = {}, {}, [(self, False)]
        while stack:
            node, visited = stack.pop()
            if not node.operator:
                if node.term not in terms:
                    terms[node.term] = matches(node.term)
                values[id(node)] = terms[node.term]
            elif not visited:
                stack.extend([(node, True), (node.right, False), (node.left, False)])
            else:
                a, b = values[id(node.left)], values[id(node.right)]
                if node.operator == "OR":
                    result = a | b
                elif node.operator == "NOT":
                    result = a - b if isinstance(a, set) else a and not b
                else:
                    result = a & b
                values[id(node)] = result
        return values

    def evaluate(self, matches):
        return self._values(matches)[id(self)]

    def positives(self, matches):
        values = self._values(matches)
        terms, stack = [], [self]
        while stack:
            node = stack.pop()
            if not values[id(node)]:
                continue
            if not node.operator:
                terms.append(node.term)
            else:
                if node.operator != "NOT":
                    stack.append(node.right)
                stack.append(node.left)
        return list(dict.fromkeys(terms))

    def terms(self):
        terms, stack = [], [self]
        while stack:
            node = stack.pop()
            if node.operator:
                stack.extend([node.right, node.left])
            else:
                terms.append(node.term)
        return list(dict.fromkeys(terms))


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
