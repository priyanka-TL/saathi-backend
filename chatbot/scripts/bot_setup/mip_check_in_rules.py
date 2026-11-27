"""
MIP check-in rules for the `saathi` flow (MipCheckInRule rows). This is the
single source of truth. chatbot/scripts/bot_setup/sync_saathi_bot.py writes
every field of every rule below on each run:
- rows are matched by flow + code;
- rows not listed here are reported, never deleted.

Each rule is one kind of question Saathi asks about a MIP. message_template
is English and may use only {title}, {start_date} and {end_date}; it and the
chips are translated to the user's language when sent. Lower priority is
asked first when several are due the same day (check-ins before report
reminders). Design: Plans/SAATHI_1.5.0_MIP_Tracking_README.md section 4.6;
copy from stories 2103, 2104 and 2118.

Read today: ASK_START_DATE (chatbot/services/mip/creation.py, right after a
MIP is created). The others are used by the check-in engine (BE-04/05/07).

1.5.0 UPDATE NOTES
------------------
1. New: the 8 default rules and STORY_BOT_ROUTE (Flow.story_bot).
2. Moved to chatbot/scripts/bot_setup/.
   - STORY_BOT_ROUTE was removed: the flow's story bot is now part of the
     flow spec (saathi_bot.py: FLOW['story_bot_route']).
   - FLOW_ROUTE must equal saathi_bot.FLOW['flow_route'] (the script checks
     this).
3. Cadence after the Plans/messages.md review (25 Nov 2026). No rule removed.
   The repeating questions no longer stop after fixed days or a number of
   asks: they repeat until the user answers or opts out.
   - REASK_START_DATE: daily (was days 1, 2, 9, 16, max 4). Stops when a
     start date is saved, tracking is declined, or the MIP is completed,
     paused or closed.
   - WEEKLY_CHECK_IN: weekly (was days 7, 14, max 2). Starts when the user
     says it is still in progress after the timeline ended; stops when it is
     completed or the user can't complete it (closed).
   - ASK_END_DATE: daily (was days 1, 2, 9, 16, max 4). Only when the user
     said done but skipped the completion-date question. Stops when a date is
     saved or the user won't give one. Dates that can't be read after 2
     tries in the chat complete the MIP without a date, and this is not asked.
   - REPORT_REMINDER: daily (was days 0, 1, 2, 9, 16, max 5). The first ask
     is the report offer sent right after completion. Stops on Share now,
     on decline, or when the report is generated.
   - REPORT_CONTINUE: daily (was days 1, 2, 9, 16, max 4). Once a Mitra
     session exists and the user left before the report was generated.
     Stops when the report is generated or the user doesn't want it.
   EVERY_N_DAYS here counts from the trigger day (first ask on day N) and
   ends on the rule's stop event, not at the plan end; only START_DATE_SET
   rules stop at the plan end. At most one ask per MIP per day.
"""

FLOW_ROUTE = 'saathi'

_START_DATE_CHIPS = ['Today', 'Tomorrow', 'Pick a date']


def _rule(code, name, trigger, schedule_type, *, day_offsets=None, every_n_days=None,
          percent_of_timeline=None, max_asks=None, priority, message_template,
          quick_reply_chips=(), is_active=True):
    return {
        'code': code,
        'name': name,
        'trigger': trigger,
        'schedule_type': schedule_type,
        'day_offsets': day_offsets,
        'every_n_days': every_n_days,
        'percent_of_timeline': percent_of_timeline,
        'max_asks': max_asks,
        'priority': priority,
        'message_template': message_template,
        'quick_reply_chips': list(quick_reply_chips),
        'is_active': is_active,
    }


RULES = [
    _rule(
        'ASK_START_DATE', 'Ask start date', 'MIP_CREATED', 'DAY_OFFSETS',
        day_offsets=[0], max_asks=1, priority=10,
        message_template='When would you like to start working on this MIP?',
        quick_reply_chips=_START_DATE_CHIPS,
    ),
    _rule(
        'REASK_START_DATE', 'Re-ask start date', 'MIP_CREATED', 'EVERY_N_DAYS',
        every_n_days=1, max_asks=None, priority=10,
        message_template='When would you like to start your {title} MIP? Or have you already started?',
        quick_reply_chips=_START_DATE_CHIPS + ['Already started'],
    ),
    _rule(
        'MIDPOINT_CHECK_IN', 'Midpoint progress check', 'START_DATE_SET', 'PERCENT_OF_TIMELINE',
        percent_of_timeline=50, max_asks=1, priority=20,
        message_template="You're halfway through your {title} MIP. How is it going so far?",
    ),
    _rule(
        'END_CHECK_IN', 'End of timeline check', 'START_DATE_SET', 'PERCENT_OF_TIMELINE',
        percent_of_timeline=100, max_asks=1, priority=20,
        message_template='Your {title} MIP has reached its planned end. Have you finished it?',
        quick_reply_chips=["Yes, I've finished", 'Not yet'],
    ),
    _rule(
        'WEEKLY_CHECK_IN', 'Weekly check after the timeline', 'TIMELINE_ENDED', 'EVERY_N_DAYS',
        every_n_days=7, max_asks=None, priority=20,
        message_template='How is your {title} MIP going? Are you still working on it?',
    ),
    _rule(
        'ASK_END_DATE', 'Ask completion date', 'COMPLETION_CLAIMED', 'EVERY_N_DAYS',
        every_n_days=1, max_asks=None, priority=30,
        message_template='You mentioned you finished your {title} MIP. When did you complete it? '
                         'An approximate date is fine.',
        quick_reply_chips=['Today', 'Pick a date'],
    ),
    _rule(
        'REPORT_REMINDER', 'Celebratory report prompt', 'MIP_COMPLETED', 'EVERY_N_DAYS',
        every_n_days=1, max_asks=None, priority=40,
        message_template="You've completed your MIP on {title}. Would you like to create a "
                         'celebratory report of your MIP journey?',
        quick_reply_chips=['Share now', 'Later'],
    ),
    _rule(
        'REPORT_CONTINUE', 'Continue the report', 'STORY_LEFT', 'EVERY_N_DAYS',
        every_n_days=1, max_asks=None, priority=40,
        message_template='You started creating your celebratory report for {title}. Would you like to continue?',
        quick_reply_chips=['Continue', 'Later'],
    ),
]
