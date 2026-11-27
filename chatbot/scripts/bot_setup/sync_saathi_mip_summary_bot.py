#!/usr/bin/env python3
"""
Set up or update the Saathi MIP Summary bot (/saathi-mip-summary) from
mip_summary_bot.py: its CompanyBot row (prompt, tools, settings). It is
called by the backend only (no languages, voices or flow). The `saathi` flow
links it as its story_bot by route (sync_saathi_bot.py).

The same command works on a fresh database and on an existing one:
- what is missing is created;
- what differs from the file is updated;
- nothing is deleted;
- unchanged rows are left untouched.

Dry run by default: prints every change and writes nothing. --apply asks before
writing (--yes answers yes), writes a before-snapshot, saves in one transaction
and re-reads every row; any mismatch rolls everything back.

Usage (from saathi-backend):
    uv run --frozen python chatbot/scripts/bot_setup/sync_saathi_mip_summary_bot.py --company <slug> [--apply] [--yes]
Locally (workspace root):
    ./dev.sh backend-script chatbot/scripts/bot_setup/sync_saathi_mip_summary_bot.py --company shikshalokamstaging
"""

import os
import sys

BOT_SETUP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(BOT_SETUP_DIR)))
# Import this folder as the chatbot.scripts.bot_setup package only, never as top-level modules.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != BOT_SETUP_DIR]
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shikshalokam_mohini.settings')

import django  # noqa: E402

django.setup()

from chatbot.scripts.bot_setup import engine, mip_summary_bot  # noqa: E402

SCRIPT = 'sync_saathi_mip_summary_bot'


def run(argv=None, **overrides):
    """CLI entry point; tests pass overrides such as bot_module=, ask= or log=."""
    kwargs = {'bot_module': mip_summary_bot}
    kwargs.update(overrides)
    return engine.main(argv, script=SCRIPT, description=__doc__, **kwargs)


if __name__ == '__main__':
    sys.exit(run())
