"""
MIP summary bot (/saathi-mip-summary): turns what a school leader told Saathi
while working on one MIP into a small structured `tracking_summary`.

Everything about the bot lives here and is the single source of truth.
chatbot/scripts/bot_setup/sync_saathi_mip_summary_bot.py writes it into the
CompanyBot row: PROMPT_FIELDS, OTHER_PARAMS and SETTINGS, on create and on
every update. The `saathi` flow links it as its story_bot by route
(saathi_bot.py: FLOW['story_bot_route']), which sync_saathi_bot.py manages.

Caller (BE-12, not built yet): GET /api/mip/<id>/story-context/ runs this bot
over the MIP's plan plus the chat messages since the MIP was created, and
returns the tool arguments as `tracking_summary` to Mitra. Design: Plans/
SAATHI_1.5.0_MIP_Tracking_README.md sections 5.2 and 10.3.

To change the prompt or settings, follow these steps:
1. Edit CONTEXT / TOOL_CONTEXT / SETTINGS here.
2. Add an UPDATE NOTES entry.
3. Run sync_saathi_mip_summary_bot.py as a dry run and review the diff.
4. Run it again with --apply.

1.5.0 UPDATE NOTES
------------------
1. New bot.
   - Input: PLAN + CONVERSATION (English, as stored in CompanyChat.message /
     translated_message).
   - Output: one call to submit_tracking_summary with challenges,
     help_requests, adaptations (change + reason) and outcomes.
   - Structured output goes through a tool, the same pattern as the title bot
     (generate_title).
2. Settings are modelled on the title bot: SIMPLE, no streaming, no vector
   search, no web search, temperature 0.
3. Moved to chatbot/scripts/bot_setup/ with its own script
   (sync_saathi_mip_summary_bot.py).
   - CREATE_SETTINGS was renamed SETTINGS and is now also applied to an
     existing row.
   - LANGUAGES, VOICES and VERNACULARS are empty: this bot is only called by
     the backend, never shown to a user.
"""

RELEASE = '1.5.0'
ROUTE = '/saathi-mip-summary'

CONTEXT = """\
# SAATHI — MIP Tracking Summary

You summarise what a school leader shared with Saathi while working on one Micro-Improvement Plan (MIP). Your summary is handed to Mitra, which uses it to help the school leader create a celebratory report of their improvement journey. Mitra will not ask the school leader again about anything you capture, so accuracy matters more than completeness.

## Input
You receive one user message with two parts:
1. **PLAN** — the MIP as generated: title, goal, objective, duration, action steps (with their current status), success indicators, and the start and end dates when known.
2. **CONVERSATION** — the messages between the school leader ("User") and Saathi ("Saathi") in this chat since the MIP was created, oldest first, in English.

## What to extract
- **challenges** — problems, obstacles or difficulties the user said they faced while carrying out the plan.
- **help_requests** — what the user asked Saathi for help with (advice, clarification, ideas). Describe the need, not Saathi's answer.
- **adaptations** — changes the user said they made to how they carried out the plan, each with the reason they gave: `{"change": "...", "reason": "..."}`. If the user gave no reason, use `""` for `reason`.
- **outcomes** — results, effects or progress the user reported (what changed for students, teachers, parents or the school), including partial results.

## Rules
- Use only what the **user** stated in the CONVERSATION. The PLAN only helps you understand what the user refers to; it is never evidence that something happened.
- Never invent, infer or embellish. If you are not sure the user said it, leave it out.
- Ignore Saathi's own suggestions unless the user confirmed they acted on them.
- Ignore greetings, quick-reply chips, start-date or end-date exchanges, and small talk.
- Write each item as one short, plain English sentence (about 25 words at most), in the third person (for example "Parent attendance at meetings improved."). Keep numbers, grades and programme names exactly as the user gave them.
- Leave out personal data that is not needed: no phone numbers, email addresses or student names.
- Merge duplicates. At most 5 items per list, most important first.
- If nothing was learned for a list, return an empty list. A summary with all four lists empty is a valid answer.

## Output
Always call the `submit_tracking_summary` tool exactly once with all four lists. Do not reply with plain text.

## Example
PLAN: title "Improving Homework Completion — Grades 6–8"; goal "Improve homework completion in Grades 6–8"; duration 30 days; steps: 1. Class teachers start a homework diary (Week 1, COMPLETED) 2. Head teacher holds a parent meeting (Week 2, COMPLETED) 3. Weekly diary check by class teachers (Week 2–4, PENDING).

CONVERSATION:
User: We started the homework diary last week but many parents don't check it. How can I get them to look at it?
Saathi: A short parent meeting to explain the diary often helps. Would you like a simple agenda?
User: We held the parent meeting on Saturday and 40 parents came. We also moved the diary check to Monday because Fridays were too busy.
User: Completion went from 40% to 55% in two weeks.

submit_tracking_summary arguments:
{"challenges": ["Many parents were not checking the homework diary."],
 "help_requests": ["Ways to get parents to check the homework diary."],
 "adaptations": [{"change": "Moved the weekly diary check from Friday to Monday.", "reason": "Fridays were too busy."}],
 "outcomes": ["A parent meeting was held and 40 parents attended.", "Homework completion rose from 40% to 55% in two weeks."]}"""

PRE_CONTEXT = ''
END_CONTEXT = ''
TAG_CONTEXT = ''
DYNAMIC_CONTEXT = ''
DYNAMIC_CONTEXT_TYPE = None
INTRODUCTORY_MESSAGE = None

_STRING_LIST = {'type': 'array', 'items': {'type': 'string'}}

TOOL_CONTEXT = {
    'tools': [
        {
            'type': 'function',
            'function': {
                'name': 'submit_tracking_summary',
                'description': 'Always call this tool exactly once with the tracking summary. Do not respond with plain text.',
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'challenges': dict(_STRING_LIST, description='Problems or obstacles the user said they faced. [] if none.'),
                        'help_requests': dict(_STRING_LIST, description='What the user asked Saathi for help with. [] if none.'),
                        'adaptations': {
                            'type': 'array',
                            'description': 'Changes the user made to how they carried out the plan, with their reason. [] if none.',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'change': {'type': 'string', 'description': 'What the user changed.'},
                                    'reason': {'type': 'string', 'description': 'Why, as the user said it; "" if not given.'},
                                },
                                'required': ['change', 'reason'],
                            },
                        },
                        'outcomes': dict(_STRING_LIST, description='Results or progress the user reported. [] if none.'),
                    },
                    'required': ['challenges', 'help_requests', 'adaptations', 'outcomes'],
                },
            },
        },
    ],
    'tool_choice': 'auto',
}

# Prompt fields: overridden from this file on every sync run.
PROMPT_FIELDS = {
    'context': CONTEXT,
    'pre_context': PRE_CONTEXT,
    'end_context': END_CONTEXT,
    'tag_context': TAG_CONTEXT,
    'dynamic_context': DYNAMIC_CONTEXT,
    'dynamic_context_type': DYNAMIC_CONTEXT_TYPE,
    'introductory_message': INTRODUCTORY_MESSAGE,
    'tool_context': TOOL_CONTEXT,
}

# other_params keys owned by this file; none for this bot.
OTHER_PARAMS = {}

# Not user-facing: no languages, voices or vernaculars.
LANGUAGES = []
VOICES = []
VERNACULARS = []

# CompanyBot settings, applied on create and on every update (provider_keys /
# llm_key never live here: credentials stay in .env / admin).
SETTINGS = {
    'name': 'SAATHI MIP Summary Bot',
    'bot_type': 'SIMPLE',
    'strategy': None,
    'provider': 'anthropic',
    'llm_model': 'claude-haiku-4-5',
    'gateway_provider': 'openrouter',
    'gateway_model': 'anthropic/claude-haiku-4.5',
    'gateway_sub_provider': None,
    'max_token': 2048,
    'bot_temperature': 0.0,
    'top_k': 2,
    'filter_score': 0.8,
    'connect_timeout': 5.0,
    'read_timeout': 30.0,
    'chat_history_limit': 1000,
    'stream': False,
    'use_vector_service': False,
    'enable_web_search': False,
    'web_search_context_size': 'medium',
    'enable_cache': False,
    'cache_ttl': None,
    'cache_targets': [],
}
