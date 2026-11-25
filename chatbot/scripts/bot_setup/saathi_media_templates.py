"""
MIP media templates for the `saathi` flow (MediaTemplate rows). They render the
"Download" of a Micro-Improvement Plan: MIP_PDF is HTML/Jinja2 converted by
Gotenberg, MIP_DOCX is a .docx rendered with docxtpl
(chatbot/utils/media_preview/media_creation.py picks them by flow + type).

Single source of truth. chatbot/scripts/bot_setup/sync_saathi_bot.py writes
these on every run:
- rows are matched by flow + type;
- template_name, constants_json and the template text/file are made equal to
  this file;
- rows are never deleted;
- a stored file is never overwritten: if the file already in storage has the
  same bytes, it is reused; otherwise the file is uploaded under a new, unique
  name.

The template files live in media/ next to this file:
- `template_path` is the HTML for a PDF template;
- `file_path` is the .docx for a DOCX template.

constants_json holds the per-language labels used in the documents.

1.5.0 UPDATE NOTES
------------------
1. Baseline, copied verbatim from MediaTemplate 1 (MIP_DOCX) and
   2 (MIP_PDF) on 25 Nov 2026. The DOCX file comes from storage
   (sha256 c454f2f5...); the PDF HTML is stored here with LF line endings.
"""

TEMPLATES = [
    {
        'template_name': 'MIP_PDF',
        'type': 'PDF',
        'template_path': 'media/mip_pdf.template.html',
        'file_path': None,
        'constants_json': {
            'en': {
                'doc_title': 'Improvement Plan',
                'goal_label': 'Goal',
                'week_label': 'Week',
                'timeline_label': 'Timeline',
                'objective_label': 'Objective',
                'action_col_label': 'Action',
                'action_plan_label': 'Action Plan',
                'success_indicators_label': 'Success Indicators',
            },
            'hi': {
                'doc_title': 'सुधार योजना',
                'goal_label': 'लक्ष्य',
                'week_label': 'सप्ताह',
                'timeline_label': 'समयसीमा',
                'objective_label': 'उद्देश्य',
                'action_col_label': 'कार्य',
                'action_plan_label': 'कार्य योजना',
                'success_indicators_label': 'सफलता सूचक',
            },
            'kn': {
                'doc_title': 'ಸುಧಾರಣಾ ಯೋಜನೆ',
                'goal_label': 'ಗುರಿ',
                'week_label': 'ವಾರ',
                'timeline_label': 'ಕಾಲಾವಧಿ',
                'objective_label': 'ಉದ್ದೇಶ',
                'action_col_label': 'ಕ್ರಮ',
                'action_plan_label': 'ಕ್ರಿಯಾ ಯೋಜನೆ',
                'success_indicators_label': 'ಯಶಸ್ಸಿನ ಸೂಚಕಗಳು',
            },
            'or': {
                'doc_title': 'ଉନ୍ନତି ଯୋଜନା',
                'goal_label': 'ଲକ୍ଷ୍ୟ',
                'week_label': 'ସପ୍ତାହ',
                'timeline_label': 'ସମୟସୀମା',
                'objective_label': 'ଉଦ୍ଦେଶ୍ୟ',
                'action_col_label': 'କାର୍ଯ୍ୟ',
                'action_plan_label': 'କାର୍ଯ୍ୟ ଯୋଜନା',
                'success_indicators_label': 'ସଫଳତା ସୂଚକ',
            },
            'ta': {
                'doc_title': 'மேம்பாட்டுத் திட்டம்',
                'goal_label': 'இலக்கு',
                'week_label': 'வாரம்',
                'timeline_label': 'கால அட்டவணை',
                'objective_label': 'நோக்கம்',
                'action_col_label': 'செயல்',
                'action_plan_label': 'செயல் திட்டம்',
                'success_indicators_label': 'வெற்றிக்கான அறிகுறிகள்',
            },
        },
    },
    {
        'template_name': 'MIP_DOCX',
        'type': 'DOCX',
        'template_path': None,
        'file_path': 'media/mip_template_v2_correct.docx',
        'constants_json': {
            'en': {
                'doc_title': 'Micre Improvement Plan',
                'goal_label': 'Aim123',
                'week_label': "day's",
                'timeline_label': 'agenda',
                'objective_label': 'InTenSions',
                'action_col_label': 'Action',
                'action_plan_label': 'Action Plan',
                'success_indicators_label': 'Success Indicator',
            },
            'hi': {
                'doc_title': 'सुधार योजना',
                'goal_label': 'लक्ष्य',
                'week_label': 'सप्ताह',
                'timeline_label': 'समयसीमा',
                'objective_label': 'उद्देश्य',
                'action_col_label': 'कार्य',
                'action_plan_label': 'कार्य योजना',
                'success_indicators_label': 'सफलता सूचक',
            },
            'kn': {
                'doc_title': 'ಸುಧಾರಣಾ ಯೋಜನೆ',
                'goal_label': 'ಗುರಿ',
                'week_label': 'ವಾರ',
                'timeline_label': 'ಕಾಲಾವಧಿ',
                'objective_label': 'ಉದ್ದೇಶ',
                'action_col_label': 'ಕ್ರಮ',
                'action_plan_label': 'ಕ್ರಿಯಾ ಯೋಜನೆ',
                'success_indicators_label': 'ಯಶಸ್ಸಿನ ಸೂಚಕಗಳು',
            },
            'or': {
                'doc_title': 'ଉନ୍ନତି ଯୋଜନା',
                'goal_label': 'ଲକ୍ଷ୍ୟ',
                'week_label': 'ସପ୍ତାହ',
                'timeline_label': 'ସମୟସୀମା',
                'objective_label': 'ଉଦ୍ଦେଶ୍ୟ',
                'action_col_label': 'କାର୍ଯ୍ୟ',
                'action_plan_label': 'କାର୍ଯ୍ୟ ଯୋଜନା',
                'success_indicators_label': 'ସଫଳତା ସୂଚକ',
            },
            'ta': {
                'doc_title': 'மேம்பாட்டுத் திட்டம்',
                'goal_label': 'இலக்கு',
                'week_label': 'வாரம்',
                'timeline_label': 'கால அட்டவணை',
                'objective_label': 'நோக்கம்',
                'action_col_label': 'செயல்',
                'action_plan_label': 'செயல் திட்டம்',
                'success_indicators_label': 'வெற்றிக்கான அறிகுறிகள்',
            },
        },
    },
]
