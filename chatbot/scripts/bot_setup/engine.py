"""
Shared engine for the bot setup scripts in chatbot/scripts/bot_setup/.

A script names one bot data module (and, for the main Saathi bot, extra flow
planners). This module plans, prints, confirms, applies and verifies, the same
way on a fresh database and on an existing one:

- Language: matched by iso_code; created when missing; never renamed or
  deleted (the table is shared by all bots).
- CompanyBot: matched by company + route; created, or the prompt fields, tools,
  owned other_params keys and settings are updated to equal the file.
- Voice (primary rows only): matched by bot + type + language; created or
  updated; fallback rows are never touched.
- BotVernacular: matched by bot + language; created or updated.
- Flow: matched by flow_route; created, or only the differing fields are
  updated. Links to other bots are resolved by route. `languages` is the list
  of enabled LANGUAGES, in file order.
- Media templates (optional): matched by flow + type. A stored file is never
  overwritten.
- Nothing is ever deleted. Rows that aren't in the file are listed as
  "not managed".

The run itself:
1. A dry run (the default) prints every change and writes nothing.
2. --apply asks once per part that has changes, the "bot" part and then the
   "flow" part (--yes answers yes).
3. It writes a before-snapshot.
4. It saves everything in one transaction, then re-reads every written row
   and compares it with the file. Any mismatch rolls everything back.

Exit codes: 0 done (also with WARNINGs), 1 error (nothing written), 2 refused
(a breaking tool change without --allow-breaking, or no TTY without --yes).
"""

import argparse
import copy
import difflib
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import DatabaseError, transaction

from chatbot.constants.provider_slugs import SLUG_TO_VOICE_PROVIDER
from chatbot.constants.voice_provider_defaults import get_provider_defaults
from chatbot.models import (
    BotVernacular, Company, CompanyBot, Flow, ImageConfiguration, Language, MediaTemplate, Provider, Voice,
)
from chatbot.models.enums import GenderChoices, MediaTemplateType, UserTypeChoices, VoiceType
from chatbot.scripts.bot_setup.compare import (
    PROMPT_TEXT_FIELDS, SECRET_FIELDS, compare_tools, dump_tool_context, find_secret_keys, normalise_text,
    parse_tool_context, texts_equal, tool_signature,
)
from chatbot.utils.pycountry_utils import resolve_iso_language

logger = logging.getLogger('django')

BOT_SETUP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(BOT_SETUP_DIR)))

CREATED = 'CREATED'
UPDATED = 'UPDATED'
UNCHANGED = 'UNCHANGED'
SKIPPED = 'SKIPPED'
CHANGES = (CREATED, UPDATED)

PART_COMMON = 'common'   # applied when any other part is accepted (Language rows)
PART_BOT = 'bot'
PART_FLOW = 'flow'

APPLY_ORDER = ('language', 'bot', 'voice', 'vernacular', 'flow', 'rule', 'template')

VOICE_FIELDS = ('provider', 'name', 'provider_code', 'gender', 'voice_speed', 'sample_link', 'other_params')
VOICE_SPEC_KEYS = ('type', 'language') + VOICE_FIELDS
VERNACULAR_FIELDS = ('name', 'introductory_message', 'alt_introductory_message', 'error_message')
VERNACULAR_SPEC_KEYS = ('language',) + VERNACULAR_FIELDS
LANGUAGE_SPEC_KEYS = ('code', 'name', 'enabled')
FLOW_FIELDS = ('flow_name', 'hidden', 'active', 'websocket_url', 'user_type')
FLOW_LINKS = {
    'bot': 'bot_route',
    'story_bot': 'story_bot_route',
    'story_validation_bot': 'story_validation_bot_route',
    'title_bot': 'title_bot_route',
}
FLOW_SPEC_KEYS = ('flow_route',) + FLOW_FIELDS + tuple(FLOW_LINKS.values()) + ('parent_flow_route', 'image_config_name')
TEMPLATE_SPEC_KEYS = ('template_name', 'type', 'template_path', 'file_path', 'constants_json')
NOT_SETTINGS = (set(PROMPT_TEXT_FIELDS) | {'tool_context', 'other_params', 'company', 'route', 'id',
                                           'created_at', 'updated_at'} | set(SECRET_FIELDS))
SETTING_FIELDS = tuple(f.name for f in CompanyBot._meta.concrete_fields if f.name not in NOT_SETTINGS)


class SyncError(Exception):
    """Configuration or database state that stops the run (nothing is written)."""


class Refused(Exception):
    """The run is refused on purpose (exit 2, nothing written)."""


class Aborted(Exception):
    """The operator stopped the run at a prompt (nothing written)."""


class Op:
    """One row to create or update, how to show it, how to save it and how to check it."""

    def __init__(self, kind, label, obj, action, part, fields=(), diffs=(), before=None):
        self.kind = kind
        self.label = label
        self.obj = obj
        self.action = action
        self.part = part
        self.fields = list(fields)
        self.diffs = list(diffs)
        self.before = before
        self.warnings = []
        self.notes = []
        self.tool_breaking = []
        self.tool_notes = []
        self.apply = None    # callable(op) -> None, saves self.obj
        self.verify = None   # callable(op) -> [problem, ...]


class Context:
    """Everything one run needs: the company, the bot module, options, lookups and the planned ops."""

    def __init__(self, *, script, bot_module, company, log, link_hints):
        self.script = script
        self.module = bot_module
        self.company = company
        self.log = log
        self.link_hints = link_hints or {}
        self.ops = []
        self.languages = {}      # iso_code -> Language (saved, or planned for creation)
        self._providers = {}
        self.bot_op = None
        self.flow_op = None
        self.warnings = []

    def add(self, op):
        self.ops.append(op)
        return op

    def warn(self, op, message):
        line = f'{op.label}: {message}' if op else message
        (op.warnings if op else self.warnings).append(message)
        logger.warning('[%s] %s', self.script, line)

    def provider(self, slug):
        if slug not in self._providers:
            self._providers[slug] = Provider.objects.filter(slug=slug).first()
        return self._providers[slug]

    @property
    def change_reason(self):
        return f'{self.script} {getattr(self.module, "RELEASE", "")}'.strip()


# ── small helpers ────────────────────────────────────────────────────────────

def _text_diff(field, old, new, context_lines=3):
    old_lines = (normalise_text(old) or '').splitlines()
    new_lines = (normalise_text(new) or '').splitlines()
    return '\n'.join(difflib.unified_diff(old_lines, new_lines, f'{field} (db)', f'{field} (file)',
                                          n=context_lines, lineterm=''))


def _json_lines(value):
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def _json_diff(field, old, new):
    return _text_diff(field, _json_lines(old), _json_lines(new), context_lines=2)


def _short(value, limit=80):
    text = repr(value)
    return text if len(text) <= limit else text[:limit - 3] + '...'


def _value_diff(field, old, new):
    return f'{field}: {_short(old)} -> {_short(new)}'


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def save_row(ctx, obj, fields=None):
    obj._change_reason = ctx.change_reason
    if fields:
        obj.save(update_fields=sorted(set(fields) | {'updated_at'}))
    else:
        obj.save()


def _route(bot):
    return bot.route if bot is not None else None


def expected_voice_params(spec):
    """other_params as Voice.save() will store it: provider defaults replace None/{} when there are any."""
    params = spec.get('other_params')
    if params in (None, {}):
        defaults = get_provider_defaults(SLUG_TO_VOICE_PROVIDER.get(spec['provider']), spec['type'])
        if defaults:
            return copy.deepcopy(defaults)
    return copy.deepcopy(params)


def _voice_spec(spec):
    full = {'name': None, 'provider_code': None, 'gender': GenderChoices.MALE, 'voice_speed': 1.0,
            'sample_link': None, 'other_params': None}
    full.update(spec)
    return full


def enabled_languages(module):
    return [lang['code'] for lang in getattr(module, 'LANGUAGES', ()) if lang.get('enabled', True)]


# ── validation of the files (no DB) ──────────────────────────────────────────

def validate_bot_module(module):
    """Problems in a bot data module. Any problem stops the run before anything is written."""
    route = getattr(module, 'ROUTE', None) or '?'
    problems = []
    required = ('ROUTE', 'PROMPT_FIELDS', 'OTHER_PARAMS', 'SETTINGS')
    for name in required:
        if not hasattr(module, name):
            problems.append(f'{route}: {name} is missing')
    if problems:
        return problems
    languages = list(getattr(module, 'LANGUAGES', ()))
    voices = list(getattr(module, 'VOICES', ()))
    vernaculars = list(getattr(module, 'VERNACULARS', ()))
    flow = getattr(module, 'FLOW', None)

    secrets = find_secret_keys({
        'SETTINGS': module.SETTINGS, 'PROMPT_FIELDS': module.PROMPT_FIELDS, 'OTHER_PARAMS': module.OTHER_PARAMS,
        'VOICES': voices, 'VERNACULARS': vernaculars, 'FLOW': flow,
    })
    if secrets:
        problems.append(f'{route}: credentials must not be in bot files ({", ".join(secrets)})')

    missing = [f for f in PROMPT_TEXT_FIELDS + ('tool_context',) if f not in module.PROMPT_FIELDS]
    if missing:
        problems.append(f'{route}: PROMPT_FIELDS is missing {", ".join(missing)}')
    tools = module.PROMPT_FIELDS.get('tool_context')
    if tools is not None:
        tool_list = tools.get('tools') if isinstance(tools, dict) else tools
        if not isinstance(tool_list, list):
            problems.append(f'{route}: tool_context must hold a list of tools')
        else:
            for i, tool in enumerate(tool_list):
                fn = tool.get('function') if isinstance(tool, dict) else None
                if not isinstance(tool, dict) or tool.get('type') != 'function' or not isinstance(fn, dict) \
                        or not fn.get('name'):
                    problems.append(f'{route}: tool #{i} needs type "function" and function.name')
        try:
            json.dumps(tools, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            problems.append(f'{route}: tool_context is not JSON-serialisable ({exc})')
    if not isinstance(module.OTHER_PARAMS, dict):
        problems.append(f'{route}: OTHER_PARAMS must be a dict')

    forbidden = sorted(set(module.SETTINGS) & ({'company', 'route', 'id'} | set(SECRET_FIELDS)))
    if forbidden:
        problems.append(f'{route}: SETTINGS must not set {", ".join(forbidden)}')
    unknown = sorted(set(module.SETTINGS) - set(SETTING_FIELDS) - set(forbidden))
    if unknown:
        problems.append(f'{route}: SETTINGS has unknown CompanyBot fields {unknown}')

    codes = []
    for i, lang in enumerate(languages):
        if not isinstance(lang, dict) or set(lang) - set(LANGUAGE_SPEC_KEYS) or 'code' not in lang:
            problems.append(f'{route}: LANGUAGES[{i}] needs only code, name, enabled')
            continue
        code = lang['code']
        codes.append(code)
        if not isinstance(code, str) or not resolve_iso_language(code):
            problems.append(f'{route}: LANGUAGES[{i}] {code!r} is not a known ISO 639 language code')
        if not isinstance(lang.get('name'), str) or not lang['name'].strip():
            problems.append(f'{route}: LANGUAGES[{i}] ({code}) needs a name')
        if not isinstance(lang.get('enabled', True), bool):
            problems.append(f'{route}: LANGUAGES[{i}] ({code}) enabled must be True or False')
    if len(codes) != len(set(codes)):
        problems.append(f'{route}: LANGUAGES has duplicate codes')

    seen = set()
    for i, spec in enumerate(voices):
        if not isinstance(spec, dict):
            problems.append(f'{route}: VOICES[{i}] must be a dict')
            continue
        unknown_keys = sorted(set(spec) - set(VOICE_SPEC_KEYS))
        if unknown_keys:
            problems.append(f'{route}: VOICES[{i}] has unknown keys {unknown_keys}')
        spec = _voice_spec(spec)
        label = f'VOICES[{i}] ({spec.get("type")} {spec.get("language")})'
        if spec.get('type') not in VoiceType.values:
            problems.append(f'{route}: {label}: type must be one of {sorted(VoiceType.values)}')
        if spec.get('language') not in codes:
            problems.append(f'{route}: {label}: language is not in LANGUAGES')
        if spec.get('provider') not in SLUG_TO_VOICE_PROVIDER:
            problems.append(f'{route}: {label}: provider must be one of {sorted(SLUG_TO_VOICE_PROVIDER)}')
        if spec['gender'] not in GenderChoices.values:
            problems.append(f'{route}: {label}: gender must be one of {sorted(GenderChoices.values)}')
        speed = spec['voice_speed']
        if speed is not None and (not isinstance(speed, (int, float)) or not 0.25 <= speed <= 4.0):
            problems.append(f'{route}: {label}: voice_speed must be between 0.25 and 4.0')
        if spec['other_params'] is not None and not isinstance(spec['other_params'], dict):
            problems.append(f'{route}: {label}: other_params must be a dict or None')
        key = (spec.get('type'), spec.get('language'))
        if key in seen:
            problems.append(f'{route}: {label}: duplicate voice (one per type and language)')
        seen.add(key)

    vern_langs = []
    for i, spec in enumerate(vernaculars):
        if not isinstance(spec, dict):
            problems.append(f'{route}: VERNACULARS[{i}] must be a dict')
            continue
        unknown_keys = sorted(set(spec) - set(VERNACULAR_SPEC_KEYS))
        if unknown_keys:
            problems.append(f'{route}: VERNACULARS[{i}] has unknown keys {unknown_keys}')
        if spec.get('language') not in codes:
            problems.append(f'{route}: VERNACULARS[{i}] language {spec.get("language")!r} is not in LANGUAGES')
        vern_langs.append(spec.get('language'))
    if len(vern_langs) != len(set(vern_langs)):
        problems.append(f'{route}: VERNACULARS has duplicate languages')

    if flow is not None:
        if not isinstance(flow, dict):
            problems.append(f'{route}: FLOW must be a dict')
        else:
            unknown_keys = sorted(set(flow) - set(FLOW_SPEC_KEYS))
            if unknown_keys:
                problems.append(f'{route}: FLOW has unknown keys {unknown_keys}')
            if not flow.get('flow_route'):
                problems.append(f'{route}: FLOW needs flow_route')
            if flow.get('bot_route') != module.ROUTE:
                problems.append(f'{route}: FLOW bot_route must be this bot ({module.ROUTE})')
            if flow.get('user_type', UserTypeChoices.ALL) not in UserTypeChoices.values:
                problems.append(f'{route}: FLOW user_type must be one of {sorted(UserTypeChoices.values)}')
            if not enabled_languages(module):
                problems.append(f'{route}: a bot with a FLOW needs at least one enabled language')
    return problems


def language_coverage_warnings(module):
    """Non-fatal gaps: an enabled language without a voice type the other languages have, or without a vernacular."""
    warnings = []
    voices = [_voice_spec(v) for v in getattr(module, 'VOICES', ())]
    vern_langs = {v.get('language') for v in getattr(module, 'VERNACULARS', ())}
    types = {v['type'] for v in voices}
    for code in enabled_languages(module):
        have = {v['type'] for v in voices if v['language'] == code}
        missing = sorted(types - have)
        if missing:
            warnings.append(f'language {code!r} has no {", ".join(missing)} voice in the file '
                            f'(runtime falls back to English for STT/TTS)')
        if getattr(module, 'FLOW', None) and getattr(module, 'VERNACULARS', None) and code not in vern_langs:
            warnings.append(f'language {code!r} has no vernacular (intro/error message) in the file')
    return warnings


def validate_templates(templates):
    problems = []
    names, types = [], []
    for i, spec in enumerate(templates):
        unknown_keys = sorted(set(spec) - set(TEMPLATE_SPEC_KEYS))
        if unknown_keys:
            problems.append(f'TEMPLATES[{i}] has unknown keys {unknown_keys}')
        if spec.get('type') not in MediaTemplateType.values:
            problems.append(f'TEMPLATES[{i}] type must be one of {sorted(MediaTemplateType.values)}')
        if not spec.get('template_name'):
            problems.append(f'TEMPLATES[{i}] needs template_name')
        for key in ('template_path', 'file_path'):
            path = spec.get(key)
            if path and not os.path.isfile(os.path.join(BOT_SETUP_DIR, path)):
                problems.append(f'TEMPLATES[{i}] {key} {path!r} not found in {BOT_SETUP_DIR}')
        if spec.get('type') == MediaTemplateType.PDF and not spec.get('template_path'):
            problems.append(f'TEMPLATES[{i}] a PDF template needs template_path')
        if spec.get('type') == MediaTemplateType.DOCX and not spec.get('file_path'):
            problems.append(f'TEMPLATES[{i}] a DOCX template needs file_path')
        names.append(spec.get('template_name'))
        types.append(spec.get('type'))
    if len(names) != len(set(names)) or len(types) != len(set(types)):
        problems.append('TEMPLATES has duplicate names or types (one per type per flow)')
    return problems


# ── planning (no writes) ─────────────────────────────────────────────────────

def plan_languages(ctx):
    for lang in getattr(ctx.module, 'LANGUAGES', ()):
        code = lang['code']
        row = Language.objects.filter(iso_code=code).first()
        label = f'[language {code}]'
        if row is None:
            row = Language(iso_code=code, name=lang['name'])
            op = ctx.add(Op('language', label, row, CREATED, PART_COMMON, diffs=[f'name: {lang["name"]!r}']))
            op.apply = lambda o: save_row(ctx, o.obj)
            op.verify = _verify_fields({'iso_code': code, 'name': lang['name']})
        else:
            op = ctx.add(Op('language', label, row, UNCHANGED, PART_COMMON))
            if row.name != lang['name']:
                op.notes.append(f'name in the database is {row.name!r} (file: {lang["name"]!r}); kept, the '
                                f'Language table is shared by all bots')
        ctx.languages[code] = row


def plan_bot(ctx):
    module, company = ctx.module, ctx.company
    desired_tools = module.PROMPT_FIELDS['tool_context']
    label = f'[bot {module.ROUTE}]'
    bot = CompanyBot.objects.filter(company=company, route=module.ROUTE).order_by('id').first()
    duplicates = CompanyBot.objects.filter(company=company, route=module.ROUTE).count()
    if duplicates > 1:
        raise SyncError(f'{duplicates} bots with route {module.ROUTE!r} exist for company {company.slug!r}; '
                        f'fix that in admin first (routes must be unique per company).')

    expected = {}
    if bot is None:
        bot = CompanyBot(company=company, route=module.ROUTE, **copy.deepcopy(module.SETTINGS))
        for field in PROMPT_TEXT_FIELDS:
            setattr(bot, field, module.PROMPT_FIELDS[field])
        bot.tool_context = dump_tool_context(desired_tools)
        bot.other_params = copy.deepcopy(module.OTHER_PARAMS) or None
        lines = len((module.PROMPT_FIELDS['context'] or '').splitlines())
        op = Op('bot', label, bot, CREATED, PART_BOT,
                diffs=[f'context: new prompt ({lines} lines)',
                       'settings: ' + ', '.join(f'{k}={_short(v, 40)}' for k, v in module.SETTINGS.items())])
        op.tool_breaking, op.tool_notes = compare_tools({}, tool_signature(desired_tools))
    else:
        before = {f: getattr(bot, f) for f in PROMPT_TEXT_FIELDS + ('tool_context', 'other_params')}
        before.update({f: getattr(bot, f) for f in module.SETTINGS})
        fields, diffs = [], []
        for field in PROMPT_TEXT_FIELDS:
            new = module.PROMPT_FIELDS[field]
            if not texts_equal(getattr(bot, field), new):
                diffs.append(_text_diff(field, getattr(bot, field), new))
                setattr(bot, field, new)
                fields.append(field)
        try:
            current_tools = parse_tool_context(bot.tool_context)
        except ValueError:
            current_tools = '<invalid JSON in the database>'
        if current_tools != desired_tools:
            diffs.append(_json_diff('tool_context', current_tools, desired_tools))
            bot.tool_context = dump_tool_context(desired_tools)
            fields.append('tool_context')
        if module.OTHER_PARAMS:
            current_params = bot.other_params if isinstance(bot.other_params, dict) else {}
            merged = dict(current_params)
            merged.update(copy.deepcopy(module.OTHER_PARAMS))
            if merged != current_params:
                diffs.append(_json_diff('other_params', current_params, merged))
                bot.other_params = merged
                fields.append('other_params')
        for field, new in module.SETTINGS.items():
            old = getattr(bot, field)
            if old != new:
                diffs.append(_value_diff(field, old, new))
                setattr(bot, field, copy.deepcopy(new))
                fields.append(field)
        op = Op('bot', label, bot, UPDATED if fields else UNCHANGED, PART_BOT, fields, diffs, before)
        before_sig = tool_signature(current_tools) if isinstance(current_tools, (dict, list)) else {}
        op.tool_breaking, op.tool_notes = compare_tools(before_sig, tool_signature(desired_tools))

    expected.update({f: module.PROMPT_FIELDS[f] for f in PROMPT_TEXT_FIELDS})
    expected.update(module.SETTINGS)

    def apply_bot(o):
        # CompanyBot.save() resets gateway_model / gateway_sub_provider when the provider or model
        # changes; re-apply the file's values until they stick (at most two more saves).
        save_row(ctx, o.obj, o.fields if o.action == UPDATED else None)
        for _ in range(3):
            reset = [f for f, v in module.SETTINGS.items() if getattr(o.obj, f) != v]
            if not reset:
                break
            for f in reset:
                setattr(o.obj, f, copy.deepcopy(module.SETTINGS[f]))
            save_row(ctx, o.obj, reset)

    def verify_bot(o):
        problems = []
        row = CompanyBot.objects.get(pk=o.obj.pk)
        for field in PROMPT_TEXT_FIELDS:
            if not texts_equal(getattr(row, field), module.PROMPT_FIELDS[field]):
                problems.append(f'{o.label} {field} differs from the file after saving')
        try:
            stored_tools = parse_tool_context(row.tool_context)
        except ValueError:
            stored_tools = None
        if stored_tools != desired_tools:
            problems.append(f'{o.label} tool_context differs from the file after saving (CompanyBot.save() keeps '
                            f'search_knowledge_base in step with use_vector_service={row.use_vector_service}; '
                            f'check that setting)')
        params = row.other_params if isinstance(row.other_params, dict) else {}
        for key, value in module.OTHER_PARAMS.items():
            if params.get(key) != value:
                problems.append(f'{o.label} other_params.{key} differs from the file after saving')
        for field, value in module.SETTINGS.items():
            if getattr(row, field) != value:
                problems.append(f'{o.label} {field} is {getattr(row, field)!r} after saving, file says {value!r}')
        return problems

    op.apply = apply_bot
    op.verify = verify_bot
    ctx.bot_op = ctx.add(op)
    return op


def plan_voices(ctx):
    module = ctx.module
    bot = ctx.bot_op.obj
    existing = {}
    fallbacks = []
    if bot.pk:
        for row in Voice.objects.filter(company_bot=bot).select_related('provider_ref', 'language_ref'):
            existing[(row.type, row.language)] = row
        fallbacks = list(Voice.all_voices.filter(company_bot=bot, is_fallback=True)
                         .select_related('primary_voice'))
    wanted = set()
    for raw in getattr(module, 'VOICES', ()):
        spec = _voice_spec(raw)
        key = (spec['type'], spec['language'])
        wanted.add(key)
        label = f'[voice {module.ROUTE} {spec["type"]} {spec["language"]}]'
        expected_params = expected_voice_params(spec)
        provider = ctx.provider(spec['provider'])
        language = ctx.languages.get(spec['language'])
        row = existing.get(key)
        if provider is None or language is None:
            op = ctx.add(Op('voice', label, row, SKIPPED, PART_BOT))
            missing = (f'provider {spec["provider"]!r} has no Provider row here (migration 0095 adds them; run '
                       f'./dev.sh migrate backend)' if provider is None else
                       f'language {spec["language"]!r} has no Language row')
            ctx.warn(op, f'{missing}; voice not written, runtime falls back to the English voice')
            continue
        if row is None:
            # The legacy language/provider CharFields are what runtime lookups filter on. Voice.save() syncs
            # them from the refs, but only once language_ref_id is set, which is not yet the case for a
            # Language created in this same run, so set them explicitly.
            row = Voice(type=spec['type'], language=spec['language'], language_ref=language,
                        provider=SLUG_TO_VOICE_PROVIDER[spec['provider']], provider_ref=provider,
                        name=spec['name'], provider_code=spec['provider_code'], gender=spec['gender'],
                        voice_speed=spec['voice_speed'], sample_link=spec['sample_link'],
                        other_params=copy.deepcopy(spec['other_params']))
            details = ', '.join(x for x in (spec['provider'], spec['name'], spec['gender']) if x)
            op = ctx.add(Op('voice', label, row, CREATED, PART_BOT, diffs=[details]))
        else:
            current = {
                'provider': row.provider_ref.slug if row.provider_ref_id else None, 'name': row.name,
                'provider_code': row.provider_code, 'gender': row.gender, 'voice_speed': row.voice_speed,
                'sample_link': row.sample_link, 'other_params': row.other_params,
            }
            wanted_values = dict(spec, other_params=expected_params)
            fields = [f for f in VOICE_FIELDS if current[f] != wanted_values[f]]
            before = {f: current[f] for f in fields}
            diffs = [_value_diff(f, current[f], wanted_values[f]) for f in fields if f != 'other_params']
            if 'other_params' in fields:
                diffs.append(_json_diff('other_params', current['other_params'], expected_params))
            for f in fields:
                if f == 'provider':
                    row.provider_ref = provider
                else:
                    setattr(row, f, copy.deepcopy(wanted_values[f]))
            op = ctx.add(Op('voice', label, row, UPDATED if fields else UNCHANGED, PART_BOT, fields, diffs, before))
            if 'other_params' in fields and spec['type'] == VoiceType.TextToText and spec['provider'] == 'google' \
                    and isinstance(expected_params, dict) and 'glossary_entries' in expected_params:
                ctx.warn(op, 'glossary_entries changed: open this voice in admin and save it so the Google '
                             'glossary is synced')

        def apply_voice(o, expected_params=expected_params):
            o.obj.company_bot = ctx.bot_op.obj
            o.obj._change_reason = ctx.change_reason
            o.obj.save()
            # Voice.save() reloads provider defaults when the provider or type changes; put the file's value back.
            if o.obj.other_params != expected_params:
                o.obj.other_params = copy.deepcopy(expected_params)
                save_row(ctx, o.obj, ['other_params'])

        def verify_voice(o, spec=spec, expected_params=expected_params):
            row = Voice.all_voices.select_related('provider_ref').get(pk=o.obj.pk)
            problems = []
            got = {
                'type': row.type, 'language': row.language, 'provider': row.provider_ref.slug,
                'name': row.name, 'provider_code': row.provider_code, 'gender': row.gender,
                'voice_speed': row.voice_speed, 'sample_link': row.sample_link, 'other_params': row.other_params,
                'company_bot': row.company_bot_id, 'is_fallback': row.is_fallback,
            }
            want = dict(spec, other_params=expected_params, company_bot=ctx.bot_op.obj.pk, is_fallback=False)
            for f, v in want.items():
                if got[f] != v:
                    problems.append(f'{o.label} {f} is {got[f]!r} after saving, file says {v!r}')
            return problems

        if op.action in CHANGES:
            op.apply = apply_voice
            op.verify = verify_voice

    for key in sorted(set(existing) - wanted):
        ctx.add(Op('voice', f'[not managed] voice {module.ROUTE} {key[0]} {key[1]}', existing[key], UNCHANGED,
                   PART_BOT)).notes.append('not in the file, left as it is')
    for row in fallbacks:
        primary = f'{row.primary_voice.type} {row.primary_voice.language}' if row.primary_voice_id else '?'
        ctx.add(Op('voice', f'[not managed] fallback voice {module.ROUTE} for {primary}', row, UNCHANGED,
                   PART_BOT)).notes.append('fallback rows are managed in admin, left as they are')


def plan_vernaculars(ctx):
    module = ctx.module
    bot = ctx.bot_op.obj
    existing = {}
    if bot.pk:
        existing = {row.language: row for row in BotVernacular.objects.filter(company_bot=bot)}
    wanted = set()
    for spec in getattr(module, 'VERNACULARS', ()):
        code = spec['language']
        wanted.add(code)
        label = f'[vernacular {module.ROUTE} {code}]'
        values = {f: spec.get(f) for f in VERNACULAR_FIELDS}
        row = existing.get(code)
        if row is None:
            row = BotVernacular(language=code, **values)
            op = ctx.add(Op('vernacular', label, row, CREATED, PART_BOT, diffs=[f'name: {values["name"]!r}']))
        else:
            fields = [f for f in VERNACULAR_FIELDS if not texts_equal(getattr(row, f), values[f])]
            before = {f: getattr(row, f) for f in fields}
            diffs = []
            for f in fields:
                old, new = getattr(row, f), values[f]
                diffs.append(_text_diff(f, old, new, 0) if len(str(old or '')) + len(str(new or '')) > 120
                             else _value_diff(f, old, new))
                setattr(row, f, new)
            op = ctx.add(Op('vernacular', label, row, UPDATED if fields else UNCHANGED, PART_BOT, fields, diffs,
                            before))

        def apply_vernacular(o):
            o.obj.company_bot = ctx.bot_op.obj
            o.obj._change_reason = ctx.change_reason
            o.obj.save()   # full save: BotVernacular.save() also refreshes language_ref

        def verify_vernacular(o, values=values, code=code):
            row = BotVernacular.objects.get(pk=o.obj.pk)
            problems = [f'{o.label} {f} differs from the file after saving'
                        for f, v in values.items() if not texts_equal(getattr(row, f), v)]
            if row.company_bot_id != ctx.bot_op.obj.pk or row.language != code:
                problems.append(f'{o.label} is not linked to {module.ROUTE} / {code} after saving')
            return problems

        if op.action in CHANGES:
            op.apply = apply_vernacular
            op.verify = verify_vernacular
    for code in sorted(set(existing) - wanted):
        ctx.add(Op('vernacular', f'[not managed] vernacular {module.ROUTE} {code}', existing[code], UNCHANGED,
                   PART_BOT)).notes.append('not in the file, left as it is')


def _resolve_link(ctx, route):
    """(bot or planned bot, found) for a route in this company; this script's own bot may be unsaved."""
    if route is None:
        return None, True
    if route == ctx.module.ROUTE:
        return ctx.bot_op.obj, True
    bot = CompanyBot.objects.filter(company=ctx.company, route=route).order_by('id').first()
    return bot, bot is not None


def plan_flow(ctx):
    spec = getattr(ctx.module, 'FLOW', None)
    if spec is None:
        return None
    flow_route = spec['flow_route']
    label = f'[flow {flow_route}]'
    flow = Flow.objects.filter(flow_route=flow_route).select_related(
        'bot', 'story_bot', 'story_validation_bot', 'title_bot', 'parent_flow', 'image_config').first()
    if flow is not None and flow.bot.company_id != ctx.company.id:
        raise SyncError(f'Flow {flow_route!r} belongs to company {flow.bot.company.slug!r}, not '
                        f'{ctx.company.slug!r}.')
    languages = enabled_languages(ctx.module)
    links, link_warnings = {}, []
    for field, key in FLOW_LINKS.items():
        route = spec.get(key)
        target, found = _resolve_link(ctx, route)
        if not found:
            hint = ctx.link_hints.get(route)
            link_warnings.append(f'{field}: bot {route!r} not found for company {ctx.company.slug!r}'
                                 + (f'; run {hint} first, then run this script again' if hint else ''))
        links[field] = (route, target, found)
    parent = None
    if spec.get('parent_flow_route'):
        parent = Flow.objects.filter(flow_route=spec['parent_flow_route']).first()
        if parent is None:
            link_warnings.append(f'parent_flow: flow {spec["parent_flow_route"]!r} not found')
    image_config = None
    if spec.get('image_config_name'):
        image_config = ImageConfiguration.objects.filter(name=spec['image_config_name']).first()
        if image_config is None:
            link_warnings.append(f'image_config: {spec["image_config_name"]!r} not found')

    if flow is None:
        flow = Flow(flow_route=flow_route, languages=list(languages),
                    **{f: spec.get(f, Flow._meta.get_field(f).get_default()) for f in FLOW_FIELDS})
        for field, (_link_route, target, found) in links.items():
            if found and field != 'bot':
                setattr(flow, field, target)
        flow.parent_flow = parent
        flow.image_config = image_config
        diffs = [f'{f}: {getattr(flow, f)!r}' for f in FLOW_FIELDS]
        diffs.append(f'languages: {languages}')
        diffs += [f'{field}: {route}' for field, (route, _, found) in links.items() if route and found]
        op = Op('flow', label, flow, CREATED, PART_FLOW, diffs=diffs)
    else:
        fields, diffs, before = [], [], {}
        for f in FLOW_FIELDS:
            if f in spec and getattr(flow, f) != spec[f]:
                before[f] = getattr(flow, f)
                diffs.append(_value_diff(f, getattr(flow, f), spec[f]))
                setattr(flow, f, spec[f])
                fields.append(f)
        if list(flow.languages or []) != languages:
            old = list(flow.languages or [])
            added = [c for c in languages if c not in old]
            removed = [c for c in old if c not in languages]
            note = '; '.join(x for x in (f'added {", ".join(added)}' if added else '',
                                         f'removed {", ".join(removed)}' if removed else '',
                                         'reordered' if not added and not removed else '') if x)
            before['languages'] = old
            diffs.append(f'languages: {old} -> {languages} ({note})')
            flow.languages = list(languages)
            fields.append('languages')
        for field, (route, target, found) in links.items():
            current = getattr(flow, field)
            if not found:
                continue   # never clear a link only because its target bot is missing here
            if field == 'bot' and target.pk is None:
                same = False
            else:
                same = (current.pk if current else None) == (target.pk if target else None)
            if not same:
                before[field] = _route(current)
                diffs.append(f'{field}: {_route(current)} -> {route}')
                fields.append(field)
        if spec.get('parent_flow_route') is None or parent is not None:
            if flow.parent_flow_id != (parent.pk if parent else None):
                before['parent_flow'] = flow.parent_flow.flow_route if flow.parent_flow_id else None
                diffs.append(f'parent_flow: {before["parent_flow"]} -> {spec.get("parent_flow_route")}')
                flow.parent_flow = parent
                fields.append('parent_flow')
        if spec.get('image_config_name') is None or image_config is not None:
            if flow.image_config_id != (image_config.pk if image_config else None):
                before['image_config'] = flow.image_config.name if flow.image_config_id else None
                diffs.append(f'image_config: {before["image_config"]} -> {spec.get("image_config_name")}')
                flow.image_config = image_config
                fields.append('image_config')
        op = Op('flow', label, flow, UPDATED if fields else UNCHANGED, PART_FLOW, fields, diffs, before)
    for message in link_warnings:
        ctx.warn(op, message)

    def apply_flow(o):
        for field, (_link_route, target, found) in links.items():
            if found and (o.action == CREATED or field in o.fields):
                setattr(o.obj, field, target)
        save_row(ctx, o.obj, o.fields if o.action == UPDATED else None)

    def verify_flow(o):
        row = Flow.objects.get(pk=o.obj.pk)
        problems = []
        if row.languages != languages:
            problems.append(f'{o.label} languages are {row.languages} after saving, file says {languages}')
        for f in FLOW_FIELDS:
            if f in spec and getattr(row, f) != spec[f]:
                problems.append(f'{o.label} {f} differs from the file after saving')
        for field, (route, target, found) in links.items():
            # Only links this run wrote: a link can be left out (its target is missing, or the bot part
            # was declined), and an unchanged link already matched before the run.
            written = o.action == CREATED or field in o.fields
            if found and written and getattr(row, f'{field}_id') != (target.pk if target else None):
                problems.append(f'{o.label} {field} is not {route} after saving')
        return problems

    op.apply = apply_flow
    op.verify = verify_flow
    ctx.flow_op = ctx.add(op)
    return op


def plan_media_templates(ctx, templates):
    """MediaTemplate rows of this script's flow (FLOW must be set)."""
    flow_op = ctx.flow_op
    flow = flow_op.obj
    existing = {}
    if flow.pk:
        existing = {t.type: t for t in MediaTemplate.objects.filter(flow=flow)}
    for spec in templates:
        label = f'[template {flow.flow_route} {spec["type"]} {spec["template_name"]}]'
        html = None
        if spec.get('template_path'):
            with open(os.path.join(BOT_SETUP_DIR, spec['template_path']), encoding='utf-8') as fh:
                html = fh.read()
        file_bytes, file_sha, file_name = None, None, None
        if spec.get('file_path'):
            with open(os.path.join(BOT_SETUP_DIR, spec['file_path']), 'rb') as fh:
                file_bytes = fh.read()
            file_sha = _sha256(file_bytes)
            file_name = os.path.basename(spec['file_path'])
        row = existing.get(spec['type'])
        adopted = False
        if row is None:
            by_name = MediaTemplate.objects.filter(template_name=spec['template_name']).select_related('flow').first()
            if by_name is not None and by_name.flow_id is not None:
                op = ctx.add(Op('template', label, by_name, SKIPPED, PART_FLOW))
                ctx.warn(op, f'template_name {spec["template_name"]!r} belongs to flow '
                             f'{by_name.flow.flow_route!r}; not written (rename it there or here)')
                continue
            if by_name is not None:
                row, adopted = by_name, True
        fields, diffs, before, pending_warnings = [], [], {}, []
        file_needs_upload = False
        if row is None:
            row = MediaTemplate(type=spec['type'], template_name=spec['template_name'],
                                constants_json=copy.deepcopy(spec['constants_json']), template=html)
            diffs.append(f'constants_json: {len(spec["constants_json"] or {})} languages'
                         + (f'; template: {len(html)} chars' if html else '')
                         + (f'; file: {file_name} (sha256 {file_sha[:12]})' if file_bytes else ''))
            file_needs_upload = file_bytes is not None
            action = CREATED
        else:
            if adopted:
                before['flow'] = None
                diffs.append(f'flow: None -> {flow.flow_route} (row had no flow; re-attached)')
                fields.append('flow')
            if row.template_name != spec['template_name']:
                before['template_name'] = row.template_name
                diffs.append(_value_diff('template_name', row.template_name, spec['template_name']))
                row.template_name = spec['template_name']
                fields.append('template_name')
            if row.constants_json != spec['constants_json']:
                before['constants_json'] = row.constants_json
                diffs.append(_json_diff('constants_json', row.constants_json, spec['constants_json']))
                row.constants_json = copy.deepcopy(spec['constants_json'])
                fields.append('constants_json')
            if not texts_equal(row.template, html):
                before['template'] = row.template
                diffs.append(_text_diff('template', row.template, html))
                row.template = html
                fields.append('template')
            if file_bytes is not None:
                stored_sha = None
                if row.template_file:
                    try:
                        with row.template_file.storage.open(row.template_file.name, 'rb') as fh:
                            stored_sha = _sha256(fh.read())
                    except Exception as exc:  # noqa: BLE001 - storage may be unreachable; never fail the run
                        stored_sha = 'unreadable'
                        pending_warnings.append(f'stored file {row.template_file.name!r} could not be read '
                                                f'({type(exc).__name__}); file not compared or replaced')
                if stored_sha not in (file_sha, 'unreadable'):
                    before['template_file'] = row.template_file.name or None
                    diffs.append(f'template_file: {row.template_file.name or None} '
                                 f'(sha256 {str(stored_sha)[:12]}) -> {file_name} (sha256 {file_sha[:12]})')
                    file_needs_upload = True
                    fields.append('template_file')
            action = UPDATED if fields else UNCHANGED
        op = ctx.add(Op('template', label, row, action, PART_FLOW, fields, diffs, before))
        for message in pending_warnings:
            ctx.warn(op, message)

        def apply_template(o, file_bytes=file_bytes, file_sha=file_sha, file_name=file_name,
                           upload=file_needs_upload):
            o.obj.flow = ctx.flow_op.obj
            db_fields = [f for f in o.fields if f != 'template_file']
            if o.action == CREATED or db_fields or o.obj.pk is None:
                save_row(ctx, o.obj, None if o.action == CREATED else db_fields)
            if upload:
                o.obj.template_file.name = _store_template_file(o.obj, file_name, file_bytes, file_sha, ctx)
                save_row(ctx, o.obj, ['template_file'])

        def verify_template(o, spec=spec, html=html, file_sha=file_sha):
            row = MediaTemplate.objects.get(pk=o.obj.pk)
            problems = []
            if row.flow_id != ctx.flow_op.obj.pk:
                problems.append(f'{o.label} is not linked to the flow after saving')
            if row.template_name != spec['template_name'] or row.constants_json != spec['constants_json'] \
                    or not texts_equal(row.template, html):
                problems.append(f'{o.label} fields differ from the file after saving')
            if file_sha and 'template_file' in o.fields + (['template_file'] if o.action == CREATED else []):
                try:
                    with row.template_file.storage.open(row.template_file.name, 'rb') as fh:
                        if _sha256(fh.read()) != file_sha:
                            problems.append(f'{o.label} stored file differs from the file after saving')
                except Exception as exc:  # noqa: BLE001
                    problems.append(f'{o.label} stored file cannot be read back ({type(exc).__name__})')
            return problems

        if action in CHANGES:
            op.apply = apply_template
            op.verify = verify_template


def _store_template_file(obj, file_name, data, sha, ctx):
    """Storage name for the template file. Never overwrites: reuse a stored copy with the same bytes."""
    storage = obj.template_file.storage
    field = MediaTemplate._meta.get_field('template_file')
    stem, ext = os.path.splitext(file_name)
    for candidate in (file_name, f'{stem}_{sha[:8]}{ext}'):
        target = field.generate_filename(obj, candidate)
        try:
            if storage.exists(target):
                with storage.open(target, 'rb') as fh:
                    if _sha256(fh.read()) == sha:
                        ctx.log(f'    reusing stored file {target} (same content, nothing uploaded)')
                        return target
                continue   # same name, different bytes: never overwrite, try the unique name
        except Exception as exc:  # noqa: BLE001 - treat an unreadable key as taken
            logger.warning('[%s] could not check stored file %s: %s', ctx.script, target, exc)
            continue
        saved = storage.save(target, ContentFile(data))
        logger.info('[%s] uploaded template file %s (not transactional: it stays if the run rolls back)',
                    ctx.script, saved)
        ctx.log(f'    uploaded {saved}')
        return saved
    raise SyncError(f'{obj.template_name}: no free storage name for {file_name}')


def _verify_fields(expected):
    def verify(o):
        row = type(o.obj)._default_manager.get(pk=o.obj.pk)
        return [f'{o.label} {f} is {getattr(row, f)!r} after saving, file says {v!r}'
                for f, v in expected.items() if getattr(row, f) != v]
    return verify


# ── output ───────────────────────────────────────────────────────────────────

def _indent(text, prefix='    '):
    return '\n'.join(prefix + line for line in text.splitlines())


def print_plan(ctx, dry_run):
    log = ctx.log
    for op in ctx.ops:
        if op.label.startswith('[not managed]'):
            continue
        verb = 'would be ' if dry_run and op.action in CHANGES else ''
        head = f'{op.label} {verb}{op.action}'
        if op.action == UPDATED:
            head += f': {", ".join(op.fields)}'
        log(head)
        if op.action in CHANGES:
            for diff in op.diffs:
                log(_indent(diff))
        for note in op.notes:
            log(f'    note: {note}')
        for warning in op.warnings:
            log(f'    WARNING: {warning}')
        if op.kind == 'bot':
            for line in op.tool_breaking:
                log(f'    BREAKING tool change: {line}')
            for line in op.tool_notes:
                log(f'    tool change: {line}')
            if not op.tool_breaking and not op.tool_notes:
                log('    tools: contract unchanged (same tools, parameters and required fields)')
    for op in ctx.ops:
        if op.label.startswith('[not managed]'):
            log(f'{op.label} ({"; ".join(op.notes)})')
    for warning in ctx.warnings:
        log(f'WARNING: {warning}')


def summary_line(ops):
    counts = {a: sum(1 for op in ops if op.action == a and not op.label.startswith('[not managed]'))
              for a in (CREATED, UPDATED, UNCHANGED, SKIPPED)}
    return (f'{counts[CREATED]} to create, {counts[UPDATED]} to update, {counts[UNCHANGED]} unchanged, '
            f'{counts[SKIPPED]} skipped (WARNING)')


# ── confirm / apply / verify ─────────────────────────────────────────────────

def default_ask(question):
    if not sys.stdin.isatty():
        raise Refused('No terminal to ask on. Run it interactively, or pass --yes to answer yes to every '
                      'question.')
    try:
        answer = input(f'{question} [y/N] ')
    except (EOFError, KeyboardInterrupt):
        print()
        raise Aborted('Stopped at the prompt.') from None
    return answer.strip().lower() in ('y', 'yes')


def _part_question(ctx, part):
    if part == PART_BOT:
        return (f'Create/update bot {ctx.module.ROUTE} (languages, settings, prompts, voices, vernaculars)?'
                if ctx.bot_op.action != CREATED else
                f'Create bot {ctx.module.ROUTE} (languages, settings, prompts, voices, vernaculars)?')
    verb = 'Create' if ctx.flow_op.action == CREATED else 'Update'
    return f'{verb} flow {ctx.flow_op.obj.flow_route} (fields, languages, links, check-in rules, templates)?'


def choose_parts(ctx, ask):
    """Ask once per part that has changes. Returns the accepted parts (PART_COMMON included if any)."""
    changed = {op.part for op in ctx.ops if op.action in CHANGES}
    order = [PART_BOT, PART_FLOW]
    if PART_COMMON in changed and not (changed & set(order)):
        changed.add(PART_BOT)   # only Language rows changed: ask with the bot question
    accepted = set()
    for part in order:
        if part in changed and ask(_part_question(ctx, part)):
            accepted.add(part)
    if accepted:
        accepted.add(PART_COMMON)
    return accepted


def drop_unaccepted(ctx, accepted):
    """
    Mark ops of declined parts as declined and keep dependants consistent (a flow needs a saved main
    bot). Returns the WARNING lines this produces, for the caller to print.
    """
    messages = []
    for op in ctx.ops:
        if op.action in CHANGES and op.part not in accepted:
            op.declined = True
    bot_missing = ctx.bot_op.obj.pk is None and getattr(ctx.bot_op, 'declined', False)
    if ctx.flow_op is not None and PART_FLOW in accepted and bot_missing:
        if ctx.flow_op.action == CREATED:
            message = (f'not created: its main bot {ctx.module.ROUTE} does not exist and the bot part was '
                       f'declined; rules and templates of this flow are not written either')
            ctx.warn(ctx.flow_op, message)
            messages.append(f'{ctx.flow_op.label} {message}')
            for op in ctx.ops:
                if op.part == PART_FLOW and op.action in CHANGES:
                    op.declined = True
        elif 'bot' in ctx.flow_op.fields:
            ctx.flow_op.fields.remove('bot')
            message = 'bot link not changed: the bot part was declined'
            ctx.warn(ctx.flow_op, message)
            messages.append(f'{ctx.flow_op.label} {message}')
            if not ctx.flow_op.fields:
                ctx.flow_op.declined = True
    return messages


def write_backup(ctx, ops, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = os.path.join(backup_dir, f'{stamp}_before.json')
    payload = {
        'script': ctx.script,
        'release': getattr(ctx.module, 'RELEASE', None),
        'company': ctx.company.slug,
        'taken_at': stamp,
        'objects': [{'label': op.label, 'action': op.action, 'fields': op.fields, 'before': op.before}
                    for op in ops],
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    return path


def apply_ops(ctx, ops):
    ordered = sorted(ops, key=lambda o: APPLY_ORDER.index(o.kind))
    with transaction.atomic():
        for op in ordered:
            op.apply(op)
        problems = []
        for op in ordered:
            problems += op.verify(op)
        if problems:
            raise SyncError('Verification failed, nothing was saved:\n  ' + '\n  '.join(problems))
    for op in ordered:
        ctx.log(f'{op.label} VERIFIED ({op.action})')


# ── entry points ─────────────────────────────────────────────────────────────

def run(*, script, bot_module, company_slug, do_apply=False, assume_yes=False, allow_breaking=False,
        backup_dir=None, log=print, ask=None, link_hints=None, validators=(), flow_planners=()):
    """
    Plan, print and (with do_apply) write one bot's configuration. Returns the exit code.

    validators: callables(bot_module) -> [problem] for extra files (e.g. check-in rules).
    flow_planners: callables(ctx) adding PART_FLOW ops after the flow is planned (rules, templates).
    """
    problems = validate_bot_module(bot_module)
    for validator in validators:
        problems += validator(bot_module)
    if problems:
        raise SyncError('Invalid configuration files:\n  ' + '\n  '.join(problems))

    company = Company.objects.filter(slug=company_slug).first()
    if company is None:
        raise SyncError(f'Company {company_slug!r} not found. Create it first with prepare_db '
                        f'(./dev.sh setup backend, or `python manage.py prepare_db` on a server).')

    ctx = Context(script=script, bot_module=bot_module, company=company, log=log, link_hints=link_hints)
    for message in language_coverage_warnings(bot_module):
        ctx.warn(None, message)
    plan_languages(ctx)
    plan_bot(ctx)
    plan_voices(ctx)
    plan_vernaculars(ctx)
    if plan_flow(ctx) is not None:
        for planner in flow_planners:
            planner(ctx)

    mode = 'APPLY' if do_apply else 'DRY RUN (nothing is written)'
    log(f'{script} {getattr(bot_module, "RELEASE", "")}  company={company.slug}  {mode}')
    log('')
    print_plan(ctx, dry_run=not do_apply)
    log('')
    breaking = list(ctx.bot_op.tool_breaking)
    log(f'Summary: {summary_line(ctx.ops)}'
        f'{", " + str(len(breaking)) + " BREAKING tool change(s)" if breaking else ""}.')

    changes = [op for op in ctx.ops if op.action in CHANGES]
    if not do_apply:
        log('Dry run: nothing written.' + (' Re-run with --apply to write these changes.' if changes else ''))
        return 0
    if not changes:
        log('Nothing to apply: the database already matches the files.')
        return 0
    if breaking and not allow_breaking:
        log('Refusing to apply: BREAKING tool changes would break existing flows. '
            'Fix the tool schema, or pass --allow-breaking once the handlers are updated.')
        return 2

    if ask is None:
        ask = (lambda question: True) if assume_yes else default_ask
    accepted = choose_parts(ctx, ask)
    for message in drop_unaccepted(ctx, accepted):
        log(f'WARNING: {message}')
    to_apply = [op for op in changes if not getattr(op, 'declined', False)]
    if not to_apply:
        log('Nothing applied (all parts declined).')
        return 0
    declined = len(changes) - len(to_apply)

    path = write_backup(ctx, to_apply, backup_dir or os.path.join(REPO_ROOT, 'logs', 'bot_setup', script))
    log(f'Before-snapshot written to {path}')
    apply_ops(ctx, to_apply)
    log(f'Applied {len(to_apply)} change(s)' + (f', {declined} declined' if declined else '')
        + '. Re-run without --apply to confirm everything is UNCHANGED.')
    return 0


def build_parser(script, description):
    parser = argparse.ArgumentParser(prog=script, description=description,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--company', required=True, help='Company slug that owns the bot (routes are unique per company).')
    parser.add_argument('--apply', action='store_true', help='Write the changes (default: dry run, nothing written).')
    parser.add_argument('--yes', action='store_true', help='With --apply: answer yes to every question (non-interactive).')
    parser.add_argument('--allow-breaking', action='store_true',
                        help='Apply even when a tool, parameter or required field would be removed or made required.')
    parser.add_argument('--backup-dir', default=None,
                        help='Folder for the before-snapshot written by --apply (default: logs/bot_setup/<script>/).')
    return parser


def main(argv, *, script, description, **run_kwargs):
    """CLI wrapper around run(): parses argv, maps errors to exit codes and messages."""
    options = build_parser(script, description).parse_args(argv)
    try:
        return run(script=script, company_slug=options.company, do_apply=options.apply,
                   assume_yes=options.yes, allow_breaking=options.allow_breaking,
                   backup_dir=options.backup_dir, **run_kwargs)
    except Refused as exc:
        print(f'Refused: {exc} Nothing written.', file=sys.stderr)
        return 2
    except Aborted as exc:
        print(f'Aborted: {exc} Nothing written.', file=sys.stderr)
        return 1
    except SyncError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except (DatabaseError, ValidationError, ValueError) as exc:
        print(f'Error: {type(exc).__name__}: {exc}\nNothing written (the transaction was rolled back). If tables are '
              f'missing, run the migrations first (./dev.sh migrate backend).', file=sys.stderr)
        logger.error('[%s] failed: %s', script, exc, exc_info=True)
        return 1
