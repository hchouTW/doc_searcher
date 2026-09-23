# Locked dependencies

`ci.txt` pins every package (with SHA-256 hashes for all published files) used by CI, release
builds, and `packaging/build_*`. `pyproject.toml` keeps the abstract ranges; this lock is what
actually gets installed.

| File | Role |
| --- | --- |
| `ci.txt` | Generated, universal lock (Linux/Windows/macOS, Python 3.10–3.14, environment markers). Do not edit by hand. |
| `build.in` | Build-system requirements, locked so the project installs with `--no-build-isolation`. |
| `platform.in` | Upstream wheel gaps worked around with marker-scoped caps; each entry says when to remove it. |

## Install exactly the locked set

```bash
python -m pip install --require-hashes -r constraints/ci.txt
python -m pip install --no-deps --no-build-isolation -e .
```

`--require-hashes` re-verifies every file, including ones served from the pip cache.

## Change or refresh pins

```bash
pip install -e '.[dev]'                     # provides uv
scripts/update_constraints.sh               # after editing pyproject.toml ranges
scripts/update_constraints.sh --upgrade     # move everything to the newest allowed versions
scripts/update_constraints.sh --check       # only verify wheels exist for every CI target
```

The script fails if any pinned package lacks a wheel for a CI target (jieba is sdist-only and
exempt). `.github/workflows/dependency-refresh.yml` runs `--upgrade` weekly, tests the result on
the full matrix, and opens a pull request; nothing changes without review.
