"""stagr — the agentic-foundation control plane CLI.

The command line is being rebuilt on the neutral core (`stagr/core/`) and the platform
renderers (`stagr/platforms/`). The commands:

    stagr help     # list commands, or `stagr help <command>` / `stagr <command> help`
    stagr init     # write a starter .agentic/config.yml (asks for the publisher App ID)
    stagr plan     # validate the config and list the files it produces; writes nothing
    stagr apply    # validate the config and write those same files

`plan` and `apply` share one pipeline (`render_pipeline.py`) and differ only in the last step.

Design invariants for every command:
  * No network. No secret VALUE is ever read, printed, or logged — only secret NAMES.
  * Fail loud: a config that does not validate exits non-zero with a precise message.
"""
from __future__ import annotations

from .parser import build_parser, cmd_help, main

__all__ = ["build_parser", "cmd_help", "main"]
