#!/usr/bin/env python3
"""
Set up or update the Saathi Profile bot (/saathi-profile) and its
`saathi_profile` flow from saathi_profile_bot.py. That covers:
- the CompanyBot row (prompt, tools, settings);
- the Language rows it needs;
- its voices (STT, TTS, translate, transliterate) and vernaculars
  (per-language name, intro and error message);
- the flow (fields, languages, links).

The same command works on a fresh database and on an existing one:
- what is missing is created;
- what differs from the file is updated;
- nothing is deleted;
- unchanged rows are left untouched.

A language with enabled=False is removed from the flow but keeps its rows.

Dry run by default: prints every change and writes nothing. --apply asks once
for the bot and once for the flow (--yes answers yes), writes a
before-snapshot, saves in one transaction and re-reads every row; any mismatch
rolls everything back.

Usage (from saathi-backend):
    uv run --frozen python chatbot/scripts/bot_setup/sync_saathi_profile_bot.py --company <slug> [--apply] [--yes]
Locally (workspace root):
    ./dev.sh backend-script chatbot/scripts/bot_setup/sync_saathi_profile_bot.py --company shikshalokamstaging
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

from chatbot.scripts.bot_setup import engine, saathi_profile_bot  # noqa: E402

SCRIPT = 'sync_saathi_profile_bot'


def run(argv=None, **overrides):
    """CLI entry point; tests pass overrides such as bot_module=, ask= or log=."""
    kwargs = {'bot_module': saathi_profile_bot}
    kwargs.update(overrides)
    return engine.main(argv, script=SCRIPT, description=__doc__, **kwargs)


if __name__ == '__main__':
    sys.exit(run())
