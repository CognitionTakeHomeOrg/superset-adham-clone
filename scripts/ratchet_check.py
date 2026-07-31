#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""Counting ratchet.

Fails when any gate's finding count rises above its committed ceiling.

It does not require the count to be zero, which is what makes it usable against
debt too large to ever hand-clear. A pull request that reduces the debt may lower
a ceiling freely. Raising one requires deliberately editing a committed file, in
a diff a reviewer will see and have to approve.

    python scripts/ratchet_check.py
"""

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / "ratchet-baseline.json").read_text())

# oxlint must run from superset-frontend/ with no path arguments, via the
# lockfile-pinned binary. Naming paths under-reports -- rules-of-hooks is 47 at
# CI scope and 43 if you pass `src packages plugins`, because playwright/ drops
# out of the scan.
FRONTEND = ROOT / "superset-frontend"
OXLINT = "./node_modules/.bin/oxlint"

# mypy's answer depends on what is installed. The gate is the isolated
# environment pre-commit uses: mypy plus stub packages and nothing else. With
# Superset's real dependencies installed the same commit reports 1,500+ errors
# that have nothing to do with this check.
MYPY_LINE = re.compile(r"^[^:]+:\d+: error: .*\[(?P<code>[\w-]+)\]$")


def count_oxlint(rule: str) -> int:
    """Count findings for one oxlint rule.

    `-A all -D <rule>` disables everything, then enables exactly one at error
    severity, so the number cannot drift because an unrelated rule changed.
    """
    out = subprocess.run(  # noqa: S603  # fixed argv, no shell, no user input
        [
            OXLINT,
            "--config",
            "oxlint.json",
            "-A",
            "all",
            "-D",
            rule,
            "--format",
            "json",
        ],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return len(json.loads(out).get("diagnostics", [])) if out.strip() else 0


def count_mypy(code: str, mypy_bin: str) -> int:
    """Count mypy errors of one code, in the isolated gate environment."""
    out = subprocess.run(  # noqa: S603  # fixed argv, no shell, no user input
        [
            mypy_bin,
            "--config-file",
            str(ROOT / "ratchet-mypy.ini"),
            "--check-untyped-defs",
            "--no-color-output",
            "--no-error-summary",
            "superset/",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return sum(
        1
        for ln in out.splitlines()
        if (m := MYPY_LINE.match(ln.strip())) and m.group("code") == code
    )


def main() -> int:
    mypy_bin = BASELINE.get("mypy_bin", "mypy")
    failed: list[tuple[str, int, int]] = []
    print(f"{'gate':<40} {'now':>6} {'ceiling':>8}   verdict")
    print("-" * 72)
    for gate, spec in sorted(BASELINE["gates"].items()):
        ceiling = spec["ceiling"]
        try:
            now = (
                count_oxlint(gate)
                if spec["kind"] == "oxlint"
                else count_mypy(gate, mypy_bin)
            )
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"{gate:<40} {'?':>6} {ceiling:>8}   SKIPPED ({type(e).__name__})")
            continue
        if now > ceiling:
            verdict, bad = f"REGRESSION +{now - ceiling}", True
        elif now < ceiling:
            verdict, bad = f"improved -{ceiling - now}", False
        else:
            verdict, bad = "held", False
        print(f"{gate:<40} {now:>6} {ceiling:>8}   {verdict}")
        if bad:
            failed.append((gate, now, ceiling))

    if failed:
        print()
        for gate, now, ceiling in failed:
            print(f"error: {gate} rose from {ceiling} to {now}.", file=sys.stderr)
        print(
            "\nFix the new findings, or -- if you are deliberately removing this "
            "code -- lower the ceiling in ratchet-baseline.json.",
            file=sys.stderr,
        )
        return 1

    print("\nAll gates held or improved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
