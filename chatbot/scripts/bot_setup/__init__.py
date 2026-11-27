"""
Bot setup: one independent script per Saathi bot, plus the data each one writes.

The data modules here are the single source of truth for the Saathi bots. Each
describes one bot completely:
- prompt fields and tool schema (PROMPT_FIELDS);
- owned other_params keys (OTHER_PARAMS);
- settings (SETTINGS);
- languages (LANGUAGES, with an `enabled` flag);
- voices (VOICES: STT, TTS, translate, transliterate);
- per-language vernaculars (VERNACULARS);
- optionally its flow (FLOW).

The data modules are:
    saathi_bot.py              /saathi-bot + flow `saathi`
    mip_check_in_rules.py      the `saathi` flow's MIP check-in rules
    saathi_media_templates.py  the `saathi` flow's MIP_PDF / MIP_DOCX (files in media/)
    saathi_profile_bot.py      /saathi-profile + flow `saathi_profile`
    mip_summary_bot.py         /saathi-mip-summary
    saathi_title_bot.py        /saathi-title

One script per bot writes its data into the database:
    sync_saathi_title_bot.py        /saathi-title
    sync_saathi_mip_summary_bot.py  /saathi-mip-summary
    sync_saathi_profile_bot.py      /saathi-profile, flow saathi_profile
    sync_saathi_bot.py              /saathi-bot, flow saathi, check-in rules, media templates

The scripts work the same way on a fresh database and on an existing one:
- they create what is missing and update only what differs from the files;
- they never delete anything;
- they leave unchanged rows untouched, so no history row is written for them.

Fresh install: run them in the order above. The `saathi` flow links the title
and story bots by route.

The shared logic is in engine.py. The pure comparison helpers are in compare.py.

To change a bot:
1. Edit its data module and add an UPDATE NOTES entry.
2. Run its script as a dry run and review the diff.
3. Run it again with --apply.

Do not edit these bots in Django admin: the next run overwrites the managed
fields. Nothing in the data modules touches the database.
"""
