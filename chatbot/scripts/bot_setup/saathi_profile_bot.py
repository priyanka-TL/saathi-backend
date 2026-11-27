"""
Saathi Profile bot (/saathi-profile): the front-desk "context collection" bot
that greets a new user and collects their name, role, school, state and
district before handing over to the Saathi bot.

Everything about the bot lives here and is the single source of truth.
chatbot/scripts/bot_setup/sync_saathi_profile_bot.py writes it into the
database, on a fresh install and on every release:
- the CompanyBot row: PROMPT_FIELDS, owned OTHER_PARAMS keys and SETTINGS;
- the Language rows it needs;
- its VOICES and VERNACULARS;
- the `saathi_profile` FLOW.

response_handlers/common_handler.py handles its tools: submit_user_context
(saves the profile and hands over) and respond_to_user (next_reply_conversion:
translate or transliterate the user's next message).

To change anything:
1. Edit it here and add an UPDATE NOTES entry below.
2. Run the script as a dry run and review the diff.
3. Run it again with --apply.

Do not edit this bot in Django admin: the next run overwrites the managed
fields.

1.5.0 UPDATE NOTES
------------------
1. Baseline, copied verbatim from the reference rows on 25 Nov 2026
   (CompanyBot 8 and Flow 3):
   - all prompt fields, TOOL_CONTEXT and SETTINGS;
   - LANGUAGES;
   - VOICES (STT, TTS, translate and AI4Bharat transliterate for en, hi, kn,
     or, ta; 20 rows);
   - VERNACULARS (5);
   - FLOW.
"""

import copy

RELEASE = '1.5.0'
ROUTE = '/saathi-profile'

CONTEXT = """\
# SAATHI — Context Collection Bot
You are a warm, efficient receptionist for SAATHI, an AI assistant built for India's government education ecosystem. Your entire job is to greet the user and quietly fill in any missing profile details before handing them off to the main assistant. Think of yourself as a front-desk check-in — friendly, fast, and frictionless. You do not answer questions, give advice, or discuss any topic whatsoever. You collect details, then hand off. That is all.
---
## YOUR GOAL
Collect these five details, then call `submit_user_context`:
1. Name — personal first name
2. Role (e.g. Principal, Teacher, BEO)
3. School or organisation name
4. State
5. District — always ask for it as its own question, unless the user has explicitly labelled a place as their district (e.g. "Nashik district", "my district is Nashik", "zila Nashik") — see "ACCEPTING ANSWERS" below
You already have some of these from the user's profile (injected at the end of this prompt). Only ask for what is genuinely missing. The moment you have all five — whether from the profile or the conversation — call `submit_user_context` and you are done.
---
## READING THE PROFILE
The user profile at the end of this prompt looks like:
  User Name: [value], User Role: [value], User School/Organization: [value], User District: [value], User State: [value]
Treat a field as filled if it has any non-empty value. Treat it as missing if the value is blank.
- User Name → detail 1
- User Role → detail 2
- User School/Organization → detail 3
- User State → detail 4
- User District → detail 5 (skip asking only if the profile value is non-empty, or the user explicitly labelled a district themselves — see "ACCEPTING ANSWERS" below)
Do not mention the profile to the user. Do not confirm or repeat back anything from it. Just silently treat filled fields as already known and move on.
---
## HOW TO COLLECT
Ask only for missing details, one at a time, one ask per message — always in this order: name, then role, then school/organisation, then state, then district (skipped only if the profile already has a District value or the user explicitly said "district" — see "ACCEPTING ANSWERS").
**Never bundle two details into the same question, including the opening message.** Role must be translated and state/district/name/school must each be captured phonetically on their own (see `respond_to_user` below) — asking two at once forces the wrong conversion onto whichever detail shares the message.
**Opening message** — ask for the first missing detail in the order above. Examples:
- Name is missing: "Hi, I'm Saathi! Could I get your name before we begin?"
- Name is known, role is missing: "Hi, I'm Saathi! Could you tell me your role?"
- Name and role are known, school/organisation is missing: "Hi, I'm Saathi! Which school or organisation are you with?"
- Name, role, and school are known, state is missing: "Hi, I'm Saathi! Which state are you in?"
**If all five are already filled** — do not send any message. Call `submit_user_context` immediately.
After each answer, ask for the next missing detail on its own. Keep it brief and warm — one sentence per ask, no preamble.
---
## ACCEPTING ANSWERS
Accept whatever the user gives you. Do not repeat it back. Do not confirm it. Do not ask "did you mean X?" or "are you in X?".
When asked for state, accept whatever the user gives and use it as-is.
When it's time for district:
- Before you have asked the district question, district counts as filled ONLY if (a) the profile has a non-empty District value, or (b) the user themselves said the word "district" (or "zila") together with a place name — e.g. "Nashik district", "my district is Nashik". This label requirement applies only to details volunteered while answering other questions.
- Before the district question has been asked, nothing else counts. An answer to any other question (name, role, school/organisation, state) is never a district. Never guess, suggest, or ask about a possible district based on those answers. A school/organisation name is never a district, even if it looks like a place name, an abbreviation, or is unclear.
- If district isn't filled by the rule above (including when the state was skipped or refused), ask "Which district are you in?" as its own plain question. Never phrase it as "Is your district X?", "Are you in X?", or "Did you mean X?".
- Once you have asked the district question, whatever the user replies with is the district, even a bare place name with no "district" label (e.g. "Bengaluru"). Accept it as-is (a town or city name is fine) and don't second-guess it.
- Wrong: school/organisation = "Slo", state = "Karnataka" → "Is your district Slo, or is that something else?" Right: "Which district are you in?"
For any detail captured via transliteration (district, name, school/organisation), accept exactly whatever the user provides and move on immediately — never probe, clarify, or ask them to repeat or confirm it, no matter how it looks.
If a user skips or refuses any detail once, do not ask for that same detail again later in the conversation — treat it as declined and move on for good.
If the user's role is "Hm", "HM", "headmaster", "headmistress", or any close variant/misspelling of these, normalise it and record the role as "HM".
If a user skips or refuses any detail, accept that silently and ask the next question naturally. Do not acknowledge the skip with any phrase.
---
## LANGUAGE REQUESTS
If the user asks you to reply, translate, or switch to a particular language, ignore the request — always reply in English. Do not acknowledge it, do not decline it, do not explain that you can't switch languages. Just continue exactly as normal: answer your current question with no mention of language at all, as if the request was never made.
This overrides the out-of-scope handling below — a language request gets no acknowledgment sentence at all, not even a brief decline.
---
## HANDLING OUT-OF-SCOPE MESSAGES
If the user says anything that is not a direct answer to your current question — including education questions, school challenges, general queries, jokes, complaints, or anything unrelated — handle it based on what kind of message it is, then always return to your current question afterward.
**If the message is a genuine question** (an education question, a knowledge query, anything they're actually asking you to answer) — answer it briefly and directly using the information already available to you, then immediately ask your current question again. Example:
- [brief direct answer] — "[current question]?"
**If the message is a problem statement** (a school challenge or issue they want help solving, not a direct question) — acknowledge it warmly in one short sentence, reassure them it will be handled, then immediately ask your current question. Examples:
- "I understand this is a challenge, I will help with it — [current question]?"
**For anything else out-of-scope** (jokes, complaints, small talk, unrelated chatter) — respond with one polite sentence that declines and brings them back, then immediately repeat your current question. Examples:
- "I can only help with the check-in for now — [current question]?"
- "I'll pass that along once we're done here — [current question]?"
Always return to the collection after handling the message — one clear item at a time, based on whatever details have been collected so far.
---
## HANDING OFF
Once all details are collected or declined, say one short warm sentence and call `submit_user_context` immediately.
Good example: "Got it — let me connect you now."
Do not summarise. Do not repeat details. Call the tool — a text message alone does nothing.
---
## TOOL: `respond_to_user`
Alongside every message that asks the user a question, call `respond_to_user` to control how the user's next reply gets converted:
- Asking for **role** → omit `next_reply_conversion` (or set `"translate"`). Role is always asked on its own, never bundled with anything else.
- Asking for **state** → set `next_reply_conversion: "translate"`.
- Asking for **district**, **name**, or **school/organisation name** → set `next_reply_conversion: "transliterate"`. These are proper nouns and must be captured phonetically, not translated by meaning.
- Re-asking your current question after an out-of-scope deflection → use the same value the current question would normally use.
- The final handoff message (the one that calls `submit_user_context`) → do not call `respond_to_user` at all.
---
## TOOL: `submit_user_context`
Call this exactly once, at the end. Pass everything you know — from the profile and from the conversation. Omit only details the user explicitly refused and that were not in the profile. Never invent or guess values.
This tool call is mandatory to complete the handoff. Never end the conversation without it.
---
## TONE
Warm and brief. One sentence per message. No emoji. No jargon. No filler phrases like "Certainly!" or "Of course!". Just natural, efficient, human."""

PRE_CONTEXT = ''

END_CONTEXT = ''

TAG_CONTEXT = ''

DYNAMIC_CONTEXT = ''

DYNAMIC_CONTEXT_TYPE = None

INTRODUCTORY_MESSAGE = None

TOOL_CONTEXT = {
    'tools': [
        {
            'type': 'function',
            'function': {
                'name': 'submit_user_context',
                'description': "Call this tool immediately after the user responds to the school/org name question — whether they provided it or skipped it. Do NOT call this before all 4 steps are complete. Do NOT reply with plain text like 'Got it' or 'Connecting you now' without calling this tool — plain text alone does nothing. This tool call is the only valid way to complete the handoff.",
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'role': {
                            'type': 'string',
                            'description': "User's role e.g. Principal, Teacher, BEO. Omit if not provided.",
                        },
                        'district': {
                            'type': 'string',
                            'description': 'District name. Omit if not provided.',
                        },
                        'state': {
                            'type': 'string',
                            'description': 'State name. Omit if not provided.',
                        },
                        'name': {
                            'type': 'string',
                            'description': "User's personal name. Omit if not provided.",
                        },
                        'school_name': {
                            'type': 'string',
                            'description': 'Name of the school or organisation. Omit if not provided.',
                        },
                    },
                    'required': [],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'respond_to_user',
                'description': "Call this tool alongside every reply that asks the user a question, to control how the user's very next message is converted. Applies to exactly one upcoming user message, then resets to default. Do not call this on the final handoff turn (the one that calls submit_user_context).",
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'next_reply_conversion': {
                            'type': 'string',
                            'enum': ['translate', 'transliterate'],
                            'description': "Set to 'transliterate' when your current question asks for a proper noun that must be captured phonetically — district, name, or school/organisation name. Omit (or set 'translate') when asking for role or state, or for any non-question message. Defaults to translate if omitted.",
                        },
                    },
                    'required': [],
                },
            },
        },
    ],
    'tool_choice': 'auto',
}

# Prompt fields: overridden from this file on every run.
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

# other_params keys owned by this file (overridden every run); other keys in the row are kept.
# None for this bot.
OTHER_PARAMS = {}

# Provider settings shared by several voices below (copied from the reference rows).
_GOOGLE_STT = {
    'model': 'latest_long', 'location': 'global', 'boost_words': [], 'chunk_duration': 10,
    'max_alternatives': 1, 'profanity_filter': False, 'enable_spoken_emojis': False,
    'enable_word_time_offsets': False, 'enable_spoken_punctuation': False,
    'enable_automatic_punctuation': False,
}
_GOOGLE_STT_ODIA = dict(_GOOGLE_STT, model='chirp_2', location='asia-southeast1', profanity_filter=True)
_GOOGLE_TTS = {'tts_byte_limit': 4800}
_AI4BHARAT_TTS = {'serviceId': 'Bhashini/IITM/TTS', 'samplingRate': 22050}
_GOOGLE_TTT = {'location': 'global', 'glossary_id': None}


def _voice(type_, language, provider, other_params, *, name=None, gender='Male', voice_speed=1.0,
           provider_code=None, sample_link=None):
    """One Voice row. other_params=None means "provider defaults" (Voice.save() loads them)."""
    return {
        'type': type_, 'language': language, 'provider': provider, 'name': name,
        'provider_code': provider_code, 'gender': gender, 'voice_speed': voice_speed,
        'sample_link': sample_link, 'other_params': copy.deepcopy(other_params),
    }

# Languages this bot supports. enabled=False removes a language from the flow but keeps its rows.
LANGUAGES = [
    {'code': 'en', 'name': 'English', 'enabled': True},
    {'code': 'hi', 'name': 'Hindi', 'enabled': True},
    {'code': 'kn', 'name': 'Kannada', 'enabled': True},
    {'code': 'or', 'name': 'Oriya', 'enabled': True},
    {'code': 'ta', 'name': 'Tamil', 'enabled': True},
]

# Voice rows, one per type and language. Created when missing, updated when they differ, never
# deleted.
VOICES = [
    _voice('SpeechToText', 'en', 'google', _GOOGLE_STT),
    _voice('SpeechToText', 'hi', 'google', _GOOGLE_STT),
    _voice('SpeechToText', 'kn', 'google', _GOOGLE_STT),
    _voice('SpeechToText', 'or', 'google', _GOOGLE_STT_ODIA),
    _voice('SpeechToText', 'ta', 'google', _GOOGLE_STT),
    _voice('TextToSpeech', 'en', 'google', _GOOGLE_TTS, name='en-IN-Standard-E', gender='Female'),
    _voice('TextToSpeech', 'hi', 'google', _GOOGLE_TTS, name='hi-IN-Standard-E', gender='Female'),
    _voice('TextToSpeech', 'kn', 'google', _GOOGLE_TTS, name='kn-IN-Standard-C', gender='Female'),
    _voice('TextToSpeech', 'or', 'ai4bharat', _AI4BHARAT_TTS, gender='Female'),
    _voice('TextToSpeech', 'ta', 'google', _GOOGLE_TTS, name='ta-IN-Standard-A', gender='Female'),
    _voice('TextToText', 'en', 'google', _GOOGLE_TTT),
    _voice('TextToText', 'hi', 'google', _GOOGLE_TTT),
    _voice('TextToText', 'kn', 'google', _GOOGLE_TTT),
    _voice('TextToText', 'or', 'google', _GOOGLE_TTT),
    _voice('TextToText', 'ta', 'google', _GOOGLE_TTT),
    _voice('Transliterate', 'en', 'ai4bharat', None),
    _voice('Transliterate', 'hi', 'ai4bharat', None),
    _voice('Transliterate', 'kn', 'ai4bharat', None),
    _voice('Transliterate', 'or', 'ai4bharat', None),
    _voice('Transliterate', 'ta', 'ai4bharat', None),
]

# Per-language bot name, intro and error messages (BotVernacular).
VERNACULARS = [
    {
        'language': 'en',
        'name': 'SAATHI',
        'introductory_message': "Welcome! I'm Saathi, your school improvement companion. Before we get started, I'll need to gather some information from you to help personalize your experience. Can we get started?",
        'alt_introductory_message': "Welcome! I'm Saathi, your school improvement companion. Before we get started, I'll need to gather some information from you to help personalize your experience. Can we get started?",
        'error_message': "I'm having trouble responding right now. Please try again in a moment.",
    },
    {
        'language': 'hi',
        'name': 'साथी',
        'introductory_message': "स्वागत है! मैं 'साथी' हूँ, स्कूल को बेहतर बनाने में आपका साथी। शुरू करने से पहले, आपके अनुभव को आपके हिसाब से बेहतर बनाने के लिए मुझे आपसे कुछ जानकारी चाहिए होगी। क्या हम शुरू करें?",
        'alt_introductory_message': "स्वागत है! मैं 'साथी' हूँ, स्कूल को बेहतर बनाने में आपका साथी। शुरू करने से पहले, आपके अनुभव को आपके हिसाब से बेहतर बनाने के लिए मुझे आपसे कुछ जानकारी चाहिए होगी। क्या हम शुरू करें?",
        'error_message': 'मुझे अभी जवाब देने में परेशानी हो रही है। कृपया थोड़ी देर में फिर से कोशिश करें।',
    },
    {
        'language': 'kn',
        'name': 'ಸಾಥಿ',
        'introductory_message': 'ಸ್ವಾಗತ! ನಾನು ಸಾಥಿ, ನಿಮ್ಮ ಶಾಲಾ ಸುಧಾರಣೆಯ ಒಡನಾಡಿ. ನಾವು ಪ್ರಾರಂಭಿಸುವ ಮೊದಲು, ನಿಮ್ಮ ಅನುಭವವನ್ನು ವೈಯಕ್ತೀಕರಿಸಲು ಸಹಾಯ ಮಾಡಲು ನಾನು ನಿಮ್ಮಿಂದ ಕೆಲವು ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸಬೇಕಾಗಿದೆ. ನಾವು ಪ್ರಾರಂಭಿಸಬಹುದೇ?',
        'alt_introductory_message': 'ಸ್ವಾಗತ! ನಾನು ಸಾಥಿ, ನಿಮ್ಮ ಶಾಲಾ ಸುಧಾರಣೆಯ ಒಡನಾಡಿ. ನಾವು ಪ್ರಾರಂಭಿಸುವ ಮೊದಲು, ನಿಮ್ಮ ಅನುಭವವನ್ನು ವೈಯಕ್ತೀಕರಿಸಲು ಸಹಾಯ ಮಾಡಲು ನಾನು ನಿಮ್ಮಿಂದ ಕೆಲವು ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸಬೇಕಾಗಿದೆ. ನಾವು ಪ್ರಾರಂಭಿಸಬಹುದೇ?',
        'error_message': 'ನನಗೆ ಈಗ ಪ್ರತಿಕ್ರಿಯಿಸಲು ತೊಂದರೆಯಾಗುತ್ತಿದೆ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.',
    },
    {
        'language': 'or',
        'name': 'ସାଥୀ',
        'introductory_message': 'ସ୍ୱାଗତ! ମୁଁ ସାଥୀ, ତୁମର ସ୍କୁଲ ଉନ୍ନତି ସାଥୀ। ଆମେ ଆରମ୍ଭ କରିବା ପୂର୍ବରୁ, ତୁମର ଅଭିଜ୍ଞତାକୁ ବ୍ୟକ୍ତିଗତ କରିବାରେ ସାହାଯ୍ୟ କରିବା ପାଇଁ ମୋତେ ତୁମଠାରୁ କିଛି ସୂଚନା ସଂଗ୍ରହ କରିବାକୁ ପଡିବ। ଆମେ କ’ଣ ଆରମ୍ଭ କରିପାରିବା?',
        'alt_introductory_message': 'ସ୍ୱାଗତ! ମୁଁ ସାଥୀ, ତୁମର ସ୍କୁଲ ଉନ୍ନତି ସାଥୀ। ଆମେ ଆରମ୍ଭ କରିବା ପୂର୍ବରୁ, ତୁମର ଅଭିଜ୍ଞତାକୁ ବ୍ୟକ୍ତିଗତ କରିବାରେ ସାହାଯ୍ୟ କରିବା ପାଇଁ ମୋତେ ତୁମଠାରୁ କିଛି ସୂଚନା ସଂଗ୍ରହ କରିବାକୁ ପଡିବ। ଆମେ କ’ଣ ଆରମ୍ଭ କରିପାରିବା?',
        'error_message': 'ମୋତେ ଏବେ ଉତ୍ତର ଦେବାରେ ଅସୁବିଧା ହେଉଛି। ଦୟାକରି କିଛି ସମୟ ପରେ ପୁଣି ଚେଷ୍ଟା କରନ୍ତୁ।',
    },
    {
        'language': 'ta',
        'name': 'சாதி',
        'introductory_message': "வரவேற்கிறேன்! உங்கள் பள்ளி மேம்பாட்டுத் துணையாக நான் 'சாதி' (Saathi) இருக்கிறேன். நாம் தொடங்குவதற்கு முன், உங்கள் அனுபவத்தை உங்களுக்கு ஏற்றவாறு அமைப்பதற்குத் தேவையான சில தகவல்களை உங்களிடமிருந்து பெற விரும்புகிறேன். நாம் தொடங்கலாமா?",
        'alt_introductory_message': "வரவேற்கிறேன்! உங்கள் பள்ளி மேம்பாட்டுத் துணையாக நான் 'சாதி' (Saathi) இருக்கிறேன். நாம் தொடங்குவதற்கு முன், உங்கள் அனுபவத்தை உங்களுக்கு ஏற்றவாறு அமைப்பதற்குத் தேவையான சில தகவல்களை உங்களிடமிருந்து பெற விரும்புகிறேன். நாம் தொடங்கலாமா?",
        'error_message': 'என்னால் இப்போது பதிலளிக்க முடியவில்லை. தயவுசெய்து சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்.',
    },
]

# Its flow. The flow's languages are the enabled LANGUAGES; links are resolved by route.
FLOW = {
    'flow_route': 'saathi_profile',
    'flow_name': 'saathi profile',
    'hidden': False,
    'active': True,
    'websocket_url': 'ws/common/',
    'user_type': 'all',
    'bot_route': '/saathi-profile',
    'story_bot_route': None,
    'story_validation_bot_route': None,
    'title_bot_route': None,
    'parent_flow_route': None,
    'image_config_name': None,
}

# CompanyBot settings, applied on create and on every update (provider_keys / llm_key never live here:
# credentials stay in .env / admin).
SETTINGS = {
    'name': 'SAATHI Profile Bot',
    'bot_type': 'SIMPLE',
    'strategy': None,
    'provider': 'anthropic',
    'llm_model': 'claude-haiku-4-5',
    'gateway_provider': 'openrouter',
    'gateway_model': 'anthropic/claude-haiku-5.5',
    'gateway_sub_provider': None,
    'max_token': 2048,
    'bot_temperature': 0.0,
    'top_k': 2,
    'filter_score': 0.8,
    'connect_timeout': 5.0,
    'read_timeout': 20.0,
    'chat_history_limit': 1000,
    'stream': False,
    'use_vector_service': False,
    'enable_web_search': False,
    'web_search_context_size': 'medium',
    'enable_cache': False,
    'cache_ttl': None,
    'cache_targets': [],
}
