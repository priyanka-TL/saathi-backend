"""
Messages for the chatbot app: text sent to users or returned in API responses.

New code defines such text here and imports it from this module. Other constant
values (keys, event names, codes) live in chatbot/constants/constants.py; log
messages stay inline where they are logged.

Like constants.py, this module imports nothing from chatbot or Django.
"""

# ── chat session ownership ───────────────────────────────────────────────────
# WebSocket system message (event WS_EVENT_SESSION_FORBIDDEN) when a session ID belongs to another user's chat.
WS_MSG_SESSION_FORBIDDEN = 'This chat is not available. Please start a new chat.'
# Sent instead of a bot reply if a chat request reaches the orchestrator for a chat the profile doesn't own.
CHAT_MSG_SESSION_FORBIDDEN = 'This chat is not available.'

# ── MIP admin: check-in rule form (chatbot/admin/mip_admin.py) ──────────────
ADMIN_MSG_MIP_RULE_SYNC_NOTE = (
    'Rules whose code is in chatbot/scripts/bot_setup/mip_check_in_rules.py are rewritten by the next '
    'sync_saathi_bot.py --apply, so change those there. A rule with any other code is kept as it is. '
    'Today the code reads only ASK_START_DATE; the other rules are for the check-in engine.'
)
ADMIN_MSG_MIP_RULE_BROKEN_PLACEHOLDER = 'The message has a broken placeholder: {error}.'
ADMIN_MSG_MIP_RULE_UNKNOWN_PLACEHOLDER = 'Placeholders {unknown} are not allowed. Use only {allowed}.'
ADMIN_MSG_MIP_RULE_CHIPS = 'Quick-reply chips must be a list of non-empty strings, e.g. ["Yes", "Not yet"].'
ADMIN_MSG_MIP_RULE_DAY_OFFSETS = 'Day offsets must be a list of whole numbers of 0 or more, e.g. [1, 2, 9].'
