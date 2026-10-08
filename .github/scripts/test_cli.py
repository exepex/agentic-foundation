#!/usr/bin/env python3
"""Tests for the `stagr` command line.

Runnable with plain `python .github/scripts/test_cli.py` (no pytest). Exit 0 = pass.
This is a thin runner: the tests live in `cli_tests/`, grouped by command, and share the single
`failures` list defined in `cli_tests.harness`. `doctor` is not offered yet.
"""
from __future__ import annotations

import sys

from cli_tests.harness import failures
from cli_tests.codex_api_tests import CODEX_API_TESTS
from cli_tests.config_template_tests import CONFIG_TEMPLATE_TESTS
from cli_tests.force_tests import FORCE_TESTS
from cli_tests.help_tests import HELP_TESTS
from cli_tests.init_tests import INIT_TESTS
from cli_tests.plan_apply_tests import PLAN_APPLY_TESTS
from cli_tests.profile_requirements_tests import PROFILE_REQUIREMENTS_TESTS
from cli_tests.profile_tests import PROFILE_TESTS
from cli_tests.prune_tests import PRUNE_TESTS
from cli_tests.remediation_tests import REMEDIATION_TESTS


def main() -> int:
    for test_function in (*HELP_TESTS, *INIT_TESTS, *PLAN_APPLY_TESTS, *PRUNE_TESTS, *PROFILE_TESTS, *FORCE_TESTS,
                          *REMEDIATION_TESTS, *CONFIG_TEMPLATE_TESTS,
                          *PROFILE_REQUIREMENTS_TESTS, *CODEX_API_TESTS):
        test_function()
    if failures:
        print(f"\n{len(failures)} test failure(s).", file=sys.stderr)
        return 1
    print("\nAll CLI tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
