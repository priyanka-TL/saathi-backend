"""
Constants for the chatbot app.

New code defines its constants here and imports them from this module; it
does not hardcode them or define them again elsewhere. Values that already
have a home are used from there instead of being copied here: model choices
in chatbot/models/enums.py (MipStatus, ChatStatus, VoiceType, ...) and
provider slugs in chatbot/constants/provider_slugs.py. The scripts under
chatbot/scripts/ keep their own constants.

This module imports nothing from chatbot or Django, so any module (models
and shikshalokam_mohini/settings.py included) can import it without a
circular import.
"""
import re

# ── logging ──────────────────────────────────────────────────────────────────
LOGGER_NAME = 'django'

# ── chat session ownership ───────────────────────────────────────────────────
# A chat (ChatSession) may only be used by the profile that owns it; see
# chatbot/services/session_ownership.py. The WebSocket answers a foreign session ID
# with this system event (message text: chatbot/constants/messages.py) and closes the socket.
WS_EVENT_SESSION_FORBIDDEN = 'session_forbidden'
# ChatSession fields that decide whose chat it is; set at creation, never changed by a REST update.
CHAT_SESSION_OWNER_FIELDS = ('session', 'profile', 'user_id', 'company_bot')

# ── chat messages ────────────────────────────────────────────────────────────
INITIATED_BY_AI = 'AI'               # save_in_company_db(initiated_by=...) for a bot message
FINISH_REASON_STOP = 'stop'          # finish_reason of a complete bot message
DEFAULT_LANGUAGE_CODE = 'en'         # chat language when none is known

# Keys of a bot message's extra_content (read by saathi-frontend).
EXTRA_CONTENT_KEY = 'extra_content'  # CompanyChat.other_params key that holds the extra_content
QUICK_REPLY_CHIPS_KEY = 'quick_reply_chips'
MIP_IDS_KEY = 'mip_ids'
MIP_INDICATOR_KEY = 'mip_indicator'  # the chat's MIP indicator (red dot): {session, show}
MIP_INDICATOR_SESSION_KEY = 'session'
MIP_INDICATOR_SHOW_KEY = 'show'

# ── MIP: download_file tool arguments ────────────────────────────────────────
# The tool schema is in chatbot/scripts/bot_setup/saathi_bot.py (TOOL_CONTEXT).
MIP_ARG_TITLE = 'title'
MIP_ARG_GOAL = 'goal'
MIP_ARG_OBJECTIVE = 'objective'
MIP_ARG_DURATION = 'duration'
MIP_ARG_ACTION_PLAN = 'action_plan'
MIP_ARG_SUCCESS_INDICATORS = 'success_indicators'
# Arguments only a MIP download carries (a knowledge download carries none of them).
MIP_FIELDS = (MIP_ARG_GOAL, MIP_ARG_OBJECTIVE, MIP_ARG_DURATION, MIP_ARG_ACTION_PLAN, MIP_ARG_SUCCESS_INDICATORS)

# One action step, in the tool's action_plan and in MipDetails.action_steps.
MIP_STEP_ACTION = 'action'
MIP_STEP_WEEK = 'week'
MIP_STEP_STATUS = 'status'
MIP_STEP_STATUS_PENDING = 'PENDING'

# MipDetails.files keys.
MIP_FILE_PDF = 'pdf'
MIP_FILE_DOCX = 'docx'

# CompanyBot.other_params['mip'] (set by chatbot/scripts/bot_setup/saathi_bot.py OTHER_PARAMS).
MIP_OTHER_PARAMS_KEY = 'mip'
MIP_MAX_PER_CHAT_KEY = 'max_per_chat'

MIP_HISTORY_REASON_CREATED = 'MIP generated'            # simple_history change reason
MIP_READY_MESSAGE = 'Your plan is ready to download.'   # download message of a complete MIP

# Plan length (MipDetails.duration_days).
MIP_DURATION_DAYS_MIN = 1
MIP_DURATION_DAYS_MAX = 32767        # PositiveSmallIntegerField upper bound
DAYS_PER_WEEK = 7
DAYS_PER_MONTH = 30
DURATION_UNIT_WEEK_PREFIX = 'w'      # "week", "wk", "w"
DURATION_UNIT_MONTH_PREFIX = 'mo'    # "month", "mo"
MIP_DURATION_RE = re.compile(r'(\d+)\s*-?\s*(days?|d|weeks?|wks?|w|months?|mo)\b', re.IGNORECASE)
MIP_BARE_NUMBER_RE = re.compile(r'^\s*(\d+)\s*$')
MIP_NUMBER_RE = re.compile(r'\d+')

# ── MIP check-in rules (MipCheckInRule) ──────────────────────────────────────
# Rows are set up by chatbot/scripts/bot_setup/mip_check_in_rules.py.
MIP_RULE_ASK_START_DATE = 'ASK_START_DATE'
# Placeholders a rule's message_template may use.
MIP_RULE_PLACEHOLDER_TITLE = 'title'
MIP_RULE_PLACEHOLDER_START_DATE = 'start_date'
MIP_RULE_PLACEHOLDER_END_DATE = 'end_date'
