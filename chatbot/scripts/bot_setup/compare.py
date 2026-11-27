"""
Pure helpers for comparing bot configuration (no DB access).

Used by chatbot/scripts/bot_setup/engine.py to decide what the bot setup
scripts write.
"""
import json
from typing import Any

# CompanyBot text fields that make up a bot's prompt. The sync script overrides
# all of them from the bot's module on every run. tool_context is handled
# separately because it is JSON.
PROMPT_TEXT_FIELDS = (
    'context',
    'pre_context',
    'end_context',
    'tag_context',
    'dynamic_context',
    'dynamic_context_type',
    'introductory_message',
)
PROMPT_FIELDS = PROMPT_TEXT_FIELDS + ('tool_context',)

# Never read from or written to bot modules: credentials belong in .env / admin.
SECRET_FIELDS = ('provider_keys', 'llm_key')


def normalise_text(value: Any) -> str | None:
    """CRLF -> LF; None stays None. Seeded prompts keep CRLF, Python strings use LF."""
    if value is None:
        return None
    return str(value).replace('\r\n', '\n')


def texts_equal(a: Any, b: Any) -> bool:
    """Equal ignoring line endings; None and '' count as equal (both mean "no text")."""
    return (normalise_text(a) or '') == (normalise_text(b) or '')


def parse_tool_context(value: str | dict | list | None) -> dict | list | None:
    """tool_context as stored (JSON text) or as written in a module (dict/list) -> Python object or None."""
    if value is None or value == '':
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def dump_tool_context(value: Any) -> str | None:
    """Python object -> the JSON text stored in CompanyBot.tool_context (same format as CompanyBot.save)."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _tools_list(parsed: dict | list | None) -> list:
    if isinstance(parsed, dict):
        return list(parsed.get('tools') or parsed.get('tool') or [])
    if isinstance(parsed, list):
        return list(parsed)
    return []


def tool_signature(tool_context: str | dict | list | None) -> dict[str, dict[str, Any]]:
    """
    {tool_name: {"params": [...], "required": [...], "description": str}} for a
    tool_context (text, dict or None). This is the contract the handlers depend
    on; descriptions are kept only to report "description changed".
    """
    signature = {}
    for tool in _tools_list(parse_tool_context(tool_context)):
        fn = tool.get('function', tool) if isinstance(tool, dict) else {}
        name = fn.get('name')
        if not name:
            continue
        params = fn.get('parameters') or {}
        signature[name] = {
            'params': sorted((params.get('properties') or {}).keys()),
            'required': sorted(params.get('required') or []),
            'description': fn.get('description') or '',
            'properties': params.get('properties') or {},
        }
    return signature


def compare_tools(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """
    Compare two tool signatures. Returns (breaking, notes):
    breaking: removed tools, removed parameters, newly required parameters.
    notes: added tools/params, description changes, and anything else non-breaking.
    """
    breaking, notes = [], []
    for name in sorted(set(before) | set(after)):
        if name not in after:
            breaking.append(f'tool removed: {name}')
            continue
        if name not in before:
            notes.append(f'tool added: {name}')
            continue
        b, a = before[name], after[name]
        for param in sorted(set(b['params']) - set(a['params'])):
            breaking.append(f'{name}: parameter removed: {param}')
        for param in sorted(set(a['params']) - set(b['params'])):
            notes.append(f'{name}: parameter added: {param}')
        for param in sorted(set(a['required']) - set(b['required'])):
            breaking.append(f'{name}: parameter now required: {param}')
        for param in sorted(set(b['required']) - set(a['required'])):
            notes.append(f'{name}: parameter no longer required: {param}')
        if b['description'] != a['description']:
            notes.append(f'{name}: description changed')
        for param in sorted(set(a['params']) & set(b['params'])):
            if b['properties'].get(param) != a['properties'].get(param):
                notes.append(f'{name}: parameter "{param}" definition changed')
    return breaking, notes


def find_secret_keys(obj: Any, path: str = '') -> list[str]:
    """Paths of any SECRET_FIELDS keys anywhere inside a nested dict/list."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f'{path}.{key}' if path else str(key)
            if key in SECRET_FIELDS:
                found.append(here)
            found.extend(find_secret_keys(value, here))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found.extend(find_secret_keys(value, f'{path}[{i}]'))
    return found
