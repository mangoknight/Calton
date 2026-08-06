#!/usr/bin/env python3
"""Run the F14 E2E suite so that "it ran nothing" is a failure, not a pass.

    python3 e2e/run_ci.py --min 24

## Why not just `python -m unittest discover`

⚠️ **The obvious answer — "plain discovery is green when it runs nothing" — is
wrong here, and I checked before writing it down rather than after.** Measured on
this repo, Python 3.13.12:

| what breaks | plain `unittest discover` |
|---|---|
| `dist/` missing (`serve()` raises `SystemExit`) | **exit 1** |
| a module cannot import (no playwright/chromium) | **exit 1** |
| discovery matches nothing at all | **exit 5** |

So all three hard failures are already loud, and a `--require-run` flag modelled
on the `parity` job would be solving a problem this suite does not have.

**What plain discovery genuinely does not catch is the suite quietly getting
smaller.** Measured: a directory holding 5 of the 24 cases runs, reports `OK`,
and exits 0. Nothing anywhere says 19 cases stopped existing. That is the failure
mode with a real path into this repo — a rename, a moved file, an `__init__.py`
that shadows a module, someone deleting the A3 group during a refactor — and it
looks exactly like a passing run.

So the only thing this script adds is the **floor**, and that is all it claims.
It is a floor rather than an exact count so adding a case does not require
editing CI in the same commit.

> Note the exit-5-for-empty behaviour is itself version-dependent (it arrived in
> Python 3.12). The floor does not depend on that, which is the other reason to
> pin a number rather than trust the runner's exit code.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

E2E = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--min",
        type=int,
        default=1,
        help="fail if fewer than this many tests actually ran",
    )
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.discover(start_dir=str(E2E), top_level_dir=str(E2E))

    # Discovery turns an un-importable module into a synthetic test that fails on
    # run, so an import error is a red run rather than a silently smaller suite.
    result = unittest.TextTestRunner(verbosity=2).run(suite)

    if result.testsRun < args.min:
        print(
            f"\ne2e/run_ci.py: only {result.testsRun} test(s) ran, expected at least"
            f" {args.min}.\n"
            "  An E2E suite that runs (almost) nothing exits 0 under plain unittest,"
            " which is how a decorative job looks exactly like a passing one.\n"
            "  Usual causes: dist/ was never built, chromium is not installed, or a"
            " module failed to import.",
            file=sys.stderr,
        )
        return 1

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
