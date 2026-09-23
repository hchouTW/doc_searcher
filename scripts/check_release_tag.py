#!/usr/bin/env python3
# Purpose: Fail a release early when the tag does not match the application version.
# What the code does:
#   - Compares a tag such as v1.2.0 with APP_VERSION read as text from
#     src/doc_searcher/version.py (via verify_build.expected_version), importing nothing heavy,
#     so the release workflow can run it before installing dependencies or packaging.
# Usage notes, dependencies, or assumptions:
#   - python scripts/check_release_tag.py v1.2.0      -> exit 0 on match, 1 otherwise
#   - Standard library only.

import sys

from verify_build import expected_version


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: check_release_tag.py <tag>", file=sys.stderr)
        return 2
    tag, version = args[0], expected_version()
    if tag != f"v{version}":
        print(
            f"[release] Tag {tag!r} does not match APP_VERSION {version!r} in "
            f"src/doc_searcher/version.py; expected tag 'v{version}'.",
            file=sys.stderr,
        )
        return 1
    print(f"[release] Tag {tag} matches APP_VERSION {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
