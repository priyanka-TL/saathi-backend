#!/usr/bin/env python3
"""
Set up or update the Saathi bot (/saathi-bot) and its `saathi` flow. That covers:
- from saathi_bot.py: the CompanyBot row (prompt, tools, owned other_params
  keys, settings), the Language rows it needs, its voices (STT, TTS,
  translate) and vernaculars (per-language name, intro and error message);
- the flow (fields, languages; links to the title bot and the MIP summary
  story bot by route);
- the 8 MIP check-in rules (mip_check_in_rules.py);
- the MIP_PDF / MIP_DOCX media templates (saathi_media_templates.py).

The same command works on a fresh database and on an existing one:
- what is missing is created;
- what differs from the files is updated;
- nothing is deleted;
- unchanged rows are left untouched.

A language with enabled=False is removed from the flow but keeps its rows. On
a fresh database, run sync_saathi_title_bot.py and sync_saathi_mip_summary_bot.py
first so the flow can link those bots; otherwise this script warns and links
them on its next run.

Dry run by default: prints every change and writes nothing. --apply asks once
for the bot and once for the flow (--yes answers yes), writes a
before-snapshot, saves in one transaction and re-reads every row; any mismatch
rolls everything back. It refuses (exit 2) to remove a tool or parameter, or to
make one newly required, unless --allow-breaking is passed.

Usage (from saathi-backend):
    uv run --frozen python chatbot/scripts/bot_setup/sync_saathi_bot.py --company <slug> [--apply] [--yes]
Locally (workspace root):
    ./dev.sh backend-script chatbot/scripts/bot_setup/sync_saathi_bot.py --company shikshalokamstaging
"""

import os
import string
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

from django.core.exceptions import ValidationError  # noqa: E402

from chatbot.models import MipCheckInRule  # noqa: E402
from chatbot.scripts.bot_setup import engine, mip_check_in_rules, saathi_bot, saathi_media_templates  # noqa: E402

SCRIPT = 'sync_saathi_bot'
RULE_FIELDS = (
    'name', 'trigger', 'schedule_type', 'day_offsets', 'every_n_days', 'percent_of_timeline',
    'max_asks', 'priority', 'message_template', 'quick_reply_chips', 'is_active',
)
ALLOWED_PLACEHOLDERS = {'title', 'start_date', 'end_date'}
# Scripts that create the bots this flow links to, named in the WARNING when one is missing.
LINK_HINTS = {
    '/saathi-title': 'chatbot/scripts/bot_setup/sync_saathi_title_bot.py',
    '/saathi-mip-summary': 'chatbot/scripts/bot_setup/sync_saathi_mip_summary_bot.py',
}


def _validate_rule(rule):
    problems = []
    code = rule.get('code') or '?'
    template = rule.get('message_template') or ''
    try:
        names = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    except ValueError as exc:
        problems.append(f'rule {code}: message_template has a broken placeholder ({exc})')
        names = set()
    unknown = names - ALLOWED_PLACEHOLDERS
    if unknown:
        problems.append(f'rule {code}: placeholders {sorted(unknown)} are not allowed '
                        f'(use only {{title}}, {{start_date}}, {{end_date}})')
    chips = rule.get('quick_reply_chips')
    if not isinstance(chips, list) or not all(isinstance(c, str) and c.strip() for c in chips):
        problems.append(f'rule {code}: quick_reply_chips must be a list of non-empty strings')
    unknown_keys = set(rule) - set(RULE_FIELDS) - {'code'}
    if unknown_keys:
        problems.append(f'rule {code}: unknown fields {sorted(unknown_keys)}')
    missing = [f for f in RULE_FIELDS if f not in rule]
    if missing:
        problems.append(f'rule {code}: missing fields {missing}')
    return problems


def make_validator(rules_module, templates_module):
    def validate(bot_module):
        problems = []
        for spec in rules_module.RULES:
            problems += _validate_rule(spec)
        codes = [spec.get('code') for spec in rules_module.RULES]
        if len(codes) != len(set(codes)):
            problems.append('mip_check_in_rules.RULES has duplicate codes')
        flow = getattr(bot_module, 'FLOW', None) or {}
        if rules_module.FLOW_ROUTE != flow.get('flow_route'):
            problems.append(f'mip_check_in_rules.FLOW_ROUTE ({rules_module.FLOW_ROUTE!r}) must equal '
                            f'saathi_bot.FLOW["flow_route"] ({flow.get("flow_route")!r})')
        problems += engine.validate_templates(templates_module.TEMPLATES)
        return problems
    return validate


def make_rules_planner(rules_module):
    def plan_rules(ctx):
        flow = ctx.flow_op.obj
        existing = {r.code: r for r in MipCheckInRule.objects.filter(flow=flow)} if flow.pk else {}
        problems = []
        for spec in rules_module.RULES:
            code = spec['code']
            values = {f: spec[f] for f in RULE_FIELDS}
            label = f'[rule {code}]'
            rule = existing.get(code)
            if rule is None:
                rule = MipCheckInRule(code=code, **values)
                op = ctx.add(engine.Op('rule', label, rule, engine.CREATED, engine.PART_FLOW,
                                       diffs=[f'message: {values["message_template"]!r}  '
                                              f'chips: {values["quick_reply_chips"]}']))
            else:
                fields, diffs, before = [], [], {}
                for field, new in values.items():
                    old = getattr(rule, field)
                    if old != new:
                        before[field] = old
                        diffs.append(f'{field}: {old!r} -> {new!r}')
                        setattr(rule, field, new)
                        fields.append(field)
                op = ctx.add(engine.Op('rule', label, rule, engine.UPDATED if fields else engine.UNCHANGED,
                                       engine.PART_FLOW, fields, diffs, before))
            if op.action in engine.CHANGES:
                try:
                    rule.full_clean(exclude=['flow'], validate_unique=False)
                except ValidationError as exc:
                    messages = exc.message_dict.items() if hasattr(exc, 'message_dict') else [('', exc.messages)]
                    problems.append(f'{label}: ' + '; '.join(f'{k}: {", ".join(v)}' for k, v in messages))

                def apply_rule(o):
                    o.obj.flow = ctx.flow_op.obj
                    engine.save_row(ctx, o.obj, o.fields if o.action == engine.UPDATED else None)

                def verify_rule(o, values=values):
                    row = MipCheckInRule.objects.get(pk=o.obj.pk)
                    found = [f'{o.label} {f} differs from the file after saving'
                             for f, v in values.items() if getattr(row, f) != v]
                    if row.flow_id != ctx.flow_op.obj.pk:
                        found.append(f'{o.label} is not linked to the flow after saving')
                    return found

                op.apply = apply_rule
                op.verify = verify_rule
        if problems:
            raise engine.SyncError('Invalid check-in rules:\n  ' + '\n  '.join(problems))
        wanted = {spec['code'] for spec in rules_module.RULES}
        for code in sorted(set(existing) - wanted):
            ctx.add(engine.Op('rule', f'[not managed] rule {code}', existing[code], engine.UNCHANGED,
                              engine.PART_FLOW)).notes.append('not in the file, left as it is')
    return plan_rules


def make_templates_planner(templates_module):
    def plan_templates(ctx):
        engine.plan_media_templates(ctx, templates_module.TEMPLATES)
    return plan_templates


def run(argv=None, *, rules_module=mip_check_in_rules, templates_module=saathi_media_templates, **overrides):
    """CLI entry point; tests pass overrides such as bot_module=, rules_module=, ask= or log=."""
    kwargs = {
        'bot_module': saathi_bot,
        'link_hints': LINK_HINTS,
        'validators': [make_validator(rules_module, templates_module)],
        'flow_planners': [make_rules_planner(rules_module), make_templates_planner(templates_module)],
    }
    kwargs.update(overrides)
    return engine.main(argv, script=SCRIPT, description=__doc__, **kwargs)


if __name__ == '__main__':
    sys.exit(run())
