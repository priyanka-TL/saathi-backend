"""
Save a MIP when its plan is downloaded (Saathi 1.5.0, BE-03a).

Called from CommonResponseHandler._handle_freeflow_function_call right after
the download_file tool's PDF and DOCX are rendered, i.e. when Saathi sends
"Your plan is ready to download" with both files, whether or not the user
then clicks a download button.

A MIP is created only when the plan is complete (goal, objective, duration,
action steps and success indicators), both files exist, and the chat is below
its MIP limit (CompanyBot.other_params.mip.max_per_chat; when the bot sets none,
settings.MIP_DEFAULT_MAX_PER_CHAT from env MIP_DEFAULT_MAX_PER_CHAT, 1 when unset). The plan
is stored in English, from the LLM's original tool arguments, the same
internal language as CompanyChat.message; MipDetails.language records the
chat language, and the rendered files are in that language.

next_check_in_rule / next_check_in_on stay empty here (BE-03b fills them).
"""
import logging
from typing import NamedTuple

from django.conf import settings
from django.db import transaction

from chatbot.constants.constants import (
    DAYS_PER_MONTH, DAYS_PER_WEEK, DEFAULT_LANGUAGE_CODE, DURATION_UNIT_MONTH_PREFIX, DURATION_UNIT_WEEK_PREFIX,
    LOGGER_NAME, MIP_ARG_ACTION_PLAN, MIP_ARG_DURATION, MIP_ARG_GOAL, MIP_ARG_OBJECTIVE, MIP_ARG_SUCCESS_INDICATORS,
    MIP_ARG_TITLE, MIP_BARE_NUMBER_RE, MIP_DURATION_DAYS_MAX, MIP_DURATION_DAYS_MIN,
    MIP_DURATION_RE, MIP_FIELDS, MIP_FILE_DOCX, MIP_FILE_PDF, MIP_HISTORY_REASON_CREATED, MIP_MAX_PER_CHAT_KEY,
    MIP_NUMBER_RE, MIP_OTHER_PARAMS_KEY, MIP_RULE_ASK_START_DATE, MIP_RULE_PLACEHOLDER_END_DATE,
    MIP_RULE_PLACEHOLDER_START_DATE, MIP_RULE_PLACEHOLDER_TITLE, MIP_STEP_ACTION, MIP_STEP_STATUS,
    MIP_STEP_STATUS_PENDING, MIP_STEP_WEEK,
)
from chatbot.models import ChatSession, Mip, MipCheckInRule, MipDetails
from chatbot.models.enums import MipStatus

logger = logging.getLogger(LOGGER_NAME)

# Read from the schema, so they follow the model if a max_length changes.
_TITLE_MAX = Mip._meta.get_field('title').max_length
_LANGUAGE_MAX = MipDetails._meta.get_field('language').max_length


class MipFollowup(NamedTuple):
    """What the download handler sends after it saved a MIP."""
    mip: Mip
    question: tuple | None   # (message, chips) of the start-date question; None without an ASK_START_DATE rule
    extra: dict              # extra_content that switches the chat's MIP indicator on


def is_mip_download(arguments):
    """True when the download_file call carries any MIP-only field (knowledge downloads carry none)."""
    if not isinstance(arguments, dict):
        return False
    return any(arguments.get(field) not in (None, '', [], {}) for field in MIP_FIELDS)


def _valid_days(days):
    return days if days is not None and MIP_DURATION_DAYS_MIN <= days <= MIP_DURATION_DAYS_MAX else None


def parse_duration_days(duration, action_plan=None):
    """
    Plan length in days: "30 days" -> 30, "4 weeks" -> 28, "1 month" -> 30, "30" -> 30.
    Falls back to the last action step's week label x 7 ("Week 3–4" -> 28).
    Returns None when neither can be read (the plan then counts as incomplete).
    """
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        return _valid_days(int(duration))
    if isinstance(duration, str):
        match = MIP_DURATION_RE.search(duration)
        if match:
            number, unit = int(match.group(1)), match.group(2).lower()
            if unit.startswith(DURATION_UNIT_WEEK_PREFIX):
                number *= DAYS_PER_WEEK
            elif unit.startswith(DURATION_UNIT_MONTH_PREFIX):
                number *= DAYS_PER_MONTH
            return _valid_days(number)
        bare = MIP_BARE_NUMBER_RE.match(duration)
        if bare:
            return _valid_days(int(bare.group(1)))

    if isinstance(action_plan, list) and action_plan:
        last = action_plan[-1]
        week = last.get(MIP_STEP_WEEK) if isinstance(last, dict) else None
        numbers = [int(n) for n in MIP_NUMBER_RE.findall(str(week or ''))]
        if numbers:
            return _valid_days(max(numbers) * DAYS_PER_WEEK)
    return None


def _text(value):
    """Non-empty stripped string, joining a list of strings; otherwise None."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
        return '\n'.join(parts) or None
    return None


def build_mip_content(arguments):
    """
    Validated MIP content from the original (English) download_file arguments.
    Returns (content, None) or (None, reason).
    """
    if not isinstance(arguments, dict):
        return None, 'arguments are not an object'

    goal = _text(arguments.get(MIP_ARG_GOAL))
    objective = _text(arguments.get(MIP_ARG_OBJECTIVE))
    if not goal:
        return None, 'goal is missing'
    if not objective:
        return None, 'objective is missing'

    raw_steps = arguments.get(MIP_ARG_ACTION_PLAN)
    if not isinstance(raw_steps, list) or not raw_steps:
        return None, 'action_plan is missing'
    steps = []
    for i, step in enumerate(raw_steps, start=1):
        if isinstance(step, dict):
            action = _text(step.get(MIP_STEP_ACTION))
            week = _text(step.get(MIP_STEP_WEEK)) or ''
        else:
            action, week = _text(step), ''
        if not action:
            return None, f'action step {i} has no action'
        steps.append({MIP_STEP_ACTION: action, MIP_STEP_WEEK: week, MIP_STEP_STATUS: MIP_STEP_STATUS_PENDING})

    raw_indicators = arguments.get(MIP_ARG_SUCCESS_INDICATORS)
    if isinstance(raw_indicators, str):
        raw_indicators = [raw_indicators]
    indicators = [s.strip() for s in raw_indicators or [] if isinstance(s, str) and s.strip()] \
        if isinstance(raw_indicators, list) else []
    if not indicators:
        return None, 'success_indicators is missing'

    duration_days = parse_duration_days(arguments.get(MIP_ARG_DURATION), raw_steps)
    if duration_days is None:
        return None, (f'duration {arguments.get(MIP_ARG_DURATION)!r} could not be read and no week label to '
                      f'fall back on')

    title = _text(arguments.get(MIP_ARG_TITLE)) or goal
    return {
        'title': title[:_TITLE_MAX],
        'goal': goal,
        'objective': objective,
        'duration_days': duration_days,
        'action_steps': steps,
        'success_indicators': indicators,
    }, None


def get_max_per_chat(company_bot):
    """
    MIPs allowed in one chat: CompanyBot.other_params.mip.max_per_chat when it is a positive whole number
    (a numeric string such as "2" is converted), otherwise settings.MIP_DEFAULT_MAX_PER_CHAT
    (env MIP_DEFAULT_MAX_PER_CHAT, 1 when unset).
    """
    params = getattr(company_bot, 'other_params', None)
    mip_config = params.get(MIP_OTHER_PARAMS_KEY) if isinstance(params, dict) else None
    value = mip_config.get(MIP_MAX_PER_CHAT_KEY) if isinstance(mip_config, dict) else None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return settings.MIP_DEFAULT_MAX_PER_CHAT
    return value


def create_mip_from_download(*, chat_session, profile_id, company_bot, content, files, language):
    """
    Create the Mip and its MipDetails in one transaction. Returns the Mip, or
    None when the chat is already at its MIP limit or required data is missing.
    """
    if not chat_session or not chat_session.user_id:
        logger.warning('[mip] chat %s has no user_id; MIP not saved', getattr(chat_session, 'session', None))
        return None
    if not profile_id:
        logger.warning('[mip] no profile for chat %s; MIP not saved', chat_session.session)
        return None
    if not (files or {}).get(MIP_FILE_PDF) or not (files or {}).get(MIP_FILE_DOCX):
        logger.info('[mip] PDF or DOCX missing for chat %s; MIP not saved', chat_session.session)
        return None

    max_per_chat = get_max_per_chat(company_bot)
    with transaction.atomic():
        # Lock the chat row so two downloads at once can't both pass the limit check.
        ChatSession.objects.select_for_update().only('id').get(pk=chat_session.pk)
        existing = Mip.objects.filter(chat_session_id=chat_session.pk).count()
        if existing >= max_per_chat:
            logger.info('[mip] chat %s already has %s MIP(s) (max_per_chat=%s); not creating another',
                        chat_session.session, existing, max_per_chat)
            return None

        mip = Mip(
            profile_id=profile_id,
            user_id=chat_session.user_id,
            chat_session=chat_session,
            title=content['title'],
            status=MipStatus.GENERATED,
        )
        mip._change_reason = MIP_HISTORY_REASON_CREATED
        mip.save()

        details = MipDetails(
            mip=mip,
            goal=content['goal'],
            objective=content['objective'],
            duration_days=content['duration_days'],
            action_steps=content['action_steps'],
            success_indicators=content['success_indicators'],
            files={MIP_FILE_PDF: files[MIP_FILE_PDF], MIP_FILE_DOCX: files[MIP_FILE_DOCX]},
            language=(language or DEFAULT_LANGUAGE_CODE)[:_LANGUAGE_MAX],
        )
        details._change_reason = MIP_HISTORY_REASON_CREATED
        details.save()

    logger.info('[mip] created MIP %s for chat %s (%s days, %s steps, language=%s)',
                mip.pk, chat_session.session, content['duration_days'], len(content['action_steps']), language)
    return mip


class _BlankDefault(dict):
    def __missing__(self, key):
        return ''


def render_rule_message(template, title):
    """Fill a check-in rule's English template; unknown placeholders become '' and a broken template is sent as is."""
    try:
        return (template or '').format_map(_BlankDefault({
            MIP_RULE_PLACEHOLDER_TITLE: title or '',
            MIP_RULE_PLACEHOLDER_START_DATE: '',
            MIP_RULE_PLACEHOLDER_END_DATE: '',
        }))
    except (ValueError, IndexError, AttributeError, KeyError):
        return template or ''


def get_start_date_question(flow, title):
    """(message, chips) from the flow's active ASK_START_DATE rule, or None when there is no such rule."""
    if flow is None:
        return None
    rule = MipCheckInRule.objects.filter(flow=flow, code=MIP_RULE_ASK_START_DATE, is_active=True).first()
    if rule is None:
        return None
    message = render_rule_message(rule.message_template, title).strip()
    if not message:
        return None
    chips = [c for c in (rule.quick_reply_chips or []) if isinstance(c, str) and c.strip()]
    return message, chips
