# Release 1.5.0 — MIP Tracking Schema

**Service:** saathi-backend

> ⚠️ **Back up the database before deploying.** This release runs migrations
> `0105`–`0108`. They only create new tables.

---

## Table of Contents

- [What's New](#whats-new)
- [Data Model](#data-model)
- [Migrations](#migrations)
- [Dependencies](#dependencies)
- [Deployment](#deployment)
- [Bot setup scripts](#bot-setup-scripts)
- [References](#references)

---

## What's New

- **MIP tracking**: four new tables (`Mip`, `MipDetails`, `MipStory`,
  `MipCheckInRule`) store each user's MIP (the improvement plan Saathi
  generates), its status, its story report and its check-in rules.
- **MIP saved on plan creation**: when Saathi offers the plan for download
  (PDF + DOCX), the MIP is saved, whether or not the user downloads it.
  - Only a complete plan with both files is saved.
  - A chat holds one MIP by default. The bot's `CompanyBot.other_params.mip.max_per_chat`
    sets the limit; a bot without it uses the env var `MIP_DEFAULT_MAX_PER_CHAT`
    (1 when unset).
  - Saathi then asks for the start date (Today / Tomorrow / Pick a date).
- **Bot configuration in code**: the four Saathi bots (title, MIP summary,
  profile, saathi) are now set up from `chatbot/scripts/bot_setup/`, one script
  per bot, covering prompt, tools, settings, languages, voices, vernaculars,
  flows, the 8 MIP check-in rules and the MIP media templates. The same command
  sets up a fresh database or updates an existing one; it is a dry run unless
  you pass `--apply`. See [Bot setup scripts](#bot-setup-scripts).
- **New MIP summary bot**: `/saathi-mip-summary`, linked to the `saathi` flow as
  its `story_bot`.
- No API, admin or Celery changes.

---

## Data Model

<details markdown="1">
<summary>New tables</summary>

| Model | Table | Holds | Relations |
|---|---|---|---|
| `MipCheckInRule` | `chatbot_mipcheckinrule` | Check-in question config: trigger, schedule, message, chips | `flow` → `Flow` |
| `Mip` | `chatbot_mip` | One row per generated MIP: owner, chat, status, dates, next check-in | `profile`, `chat_session`, `next_check_in_rule` |
| `MipDetails` | `chatbot_mipdetails` | Plan content: goal, objective, duration, action steps, files | 1:1 `mip` |
| `MipStory` | `chatbot_mipstory` | Story report from an external provider (Mitra) | 1:1 `mip` |

Foreign keys to existing tables are `PROTECT`: once MIP rows exist, the linked
`Flow`, `Profile` or `ChatSession` can't be deleted.

Source: [`chatbot/models/mip_models.py`](../../../../chatbot/models/mip_models.py)

</details>

<details markdown="1">
<summary>Constraints & indexes</summary>

| Table | Rule |
|---|---|
| `chatbot_mipcheckinrule` | Unique (`flow`, `code`). Only the parameter for `schedule_type` is set. |
| `chatbot_mip` | `STARTED` needs `start_date`. `end_date` is set only when `COMPLETED`. `next_check_in_rule` and `next_check_in_on` are both set or both empty. |
| `chatbot_mipdetails` | `duration_days > 0`. `files` has `pdf` and `docx`. |
| `chatbot_mipstory` | `GENERATED` needs `files.pdf` and `generated_at`. A session ID needs a provider. Unique (`provider`, `external_session_id`). |

Indexes on `chatbot_mip`: (`user_id`, `status`), and a partial index on
`next_check_in_on` for rows with a check-in due.

</details>

<details markdown="1">
<summary>Enums</summary>

| Enum | Values |
|---|---|
| `MipStatus` | `GENERATED`, `STARTED`, `PAUSED`, `COMPLETED`, `CLOSED` |
| `MipStoryStatus` | `DECLINED`, `IN_PROGRESS`, `GENERATED` |
| `MipStoryProvider` | `MITRA` |
| `MipCheckInTrigger` | `MIP_CREATED`, `START_DATE_SET`, `TIMELINE_ENDED`, `COMPLETION_CLAIMED`, `MIP_COMPLETED`, `STORY_LEFT` |
| `MipCheckInSchedule` | `DAY_OFFSETS`, `EVERY_N_DAYS`, `PERCENT_OF_TIMELINE` |

Source: [`chatbot/models/enums.py`](../../../../chatbot/models/enums.py)

</details>

---

## Migrations

<details markdown="1">
<summary>0105–0108</summary>

| Migration | Creates |
|---|---|
| `0105_mip_check_in_rule` | `chatbot_mipcheckinrule` + history |
| `0106_mip` | `chatbot_mip` + history |
| `0107_mip_details` | `chatbot_mipdetails` + history |
| `0108_mip_story` | `chatbot_mipstory` + history |

All four are `CreateModel` only: no data migration, no change to existing
tables. Source: [`chatbot/migrations/`](../../../../chatbot/migrations/)

</details>

---

## Dependencies

- **Python packages**: none added (`pyproject.toml` and `uv.lock` unchanged).
- **Django**: `>=5.1` for `CheckConstraint(condition=...)`; `uv.lock` pins 5.2.
- **PostgreSQL**: required for the JSON key constraints (`has_key` / `has_keys`).
- **Env vars**: one new: `MIP_DEFAULT_MAX_PER_CHAT=1` (default, as in `sample.env`). It is
  the MIP limit per chat for a bot whose `other_params.mip` sets no `max_per_chat`. Add
  it to the backend's environment config before deploying
  ([pre-deploy step 3](#pre-deploy-checklist)); when it is missing or empty the backend also uses 1.
  The value is converted to a number and is never less than 1 (0 or a negative value
  means 1). A value that is not a whole number stops the backend and celery at startup
  (`ValueError`).
- **Secrets, services, systemd units**: unchanged.

---

## Deployment

### Pre-deploy checklist

1.  **Confirm the database is at `0104_media_template`** (on the backend host):

    ```bash
    cd /opt/deployment/backend/saathi-service/saathi-backend-service
    uv run --frozen python manage.py showmigrations chatbot | tail -1
    # [X] 0104_media_template
    ```

2.  **Back up the database.**

    <details markdown="1">
    <summary>pg_dump using the current <code>.env</code> (needs <code>postgresql-client</code>)</summary>

    ```bash
    cd /opt/deployment/backend/saathi-service/saathi-backend-service
    set -a; source <(grep -E '^(DATABASE_|PG_SSL_MODE)' .env); set +a
    PGPASSWORD="$DATABASE_PASSWORD" PGSSLMODE="$PG_SSL_MODE" pg_dump -Fc \
      -h "$DATABASE_HOST" -p "$DATABASE_PORT" -U "$DATABASE_USER" "$DATABASE_NAME" \
      > ~/saathi_pre_1.5.0_$(date +%Y%m%d%H%M).dump
    ```

    </details>

3.  **Add the new `.env` value.** Every deploy writes `.env` from the backend's
    environment config, so add the value to that config, not to `.env` on the
    host (a host edit is lost on the next deploy).

    | Key | Value |
    |---|---|
    | `MIP_DEFAULT_MAX_PER_CHAT` | `1` |

    Keep every other key as it is. After the deploy, [deploy step 3](#deploy-steps)
    checks that the value reached `.env`.

### Deploy steps

1.  **Deploy the `release-1.5.0_RC<n>` tag** with one of the options below.

    **Option A: Jenkins (standard).** Run the backend deploy job with `repo_ref`
    set to the tag. The job runs [`deploy/ansible/deploy.yml`](../../../../deploy/ansible/deploy.yml):
    it applies the migrations, restarts `saathi-uvicorn` and `saathi-celery`,
    and waits for `/health/` to return 200.

    <details markdown="1">
    <summary>What the playbook does</summary>

    1. Clones `repo_ref` into `/opt/deployment/backend/saathi-service/releases/<timestamp>`.
    2. Fetches `config/secrets.json` (Vault `saathi_gcp_credentials`) and runs `uv sync --frozen`.
    3. Replaces the live directory `.../saathi-backend-service` with the release.
    4. Writes `.env` from Vault `saathi_backend_env` via [`scripts/json2env.sh`](../../../../scripts/json2env.sh).
    5. Runs `manage.py migrate --noinput`.
    6. Renders the systemd units from [`deploy/ansible/templates/`](../../../../deploy/ansible/templates/) and restarts both services.
    7. Polls `http://localhost:9000/health/` (20 tries, 5 s apart).

    There is no automatic rollback. Ports, worker counts and Vault paths are the
    `vars:` in `deploy.yml`; Jenkins can override them.

    </details>

    **Option B: Manual.** Use when Jenkins is unavailable. Run the playbook's
    steps by hand on the backend host, as root.

    <details markdown="1">
    <summary>Manual deployment steps (needs <code>git</code>, <code>uv</code>, <code>jq</code>, <code>curl</code> and the Vault token)</summary>

    ```bash
    sudo -i

    PROJECT=/opt/deployment/backend/saathi-service
    LIVE=$PROJECT/saathi-backend-service
    REL=$PROJECT/releases/$(date +%Y%m%d%H%M%S)
    REPO_URL=<repo_url>              # same value Jenkins passes
    REPO_REF=release-1.5.0_RC<n>
    VAULT=<vault_address>            # deploy.yml vars.vault_address (ends with /)
    TOKEN=$(cat /opt/deployment/.token)

    # 1. Fetch the release, secrets and .env (live site untouched)
    git clone --branch "$REPO_REF" "$REPO_URL" "$REL"
    mkdir -p "$REL/config"
    curl -sk "${VAULT}saathi_gcp_credentials" -H "X-Vault-Token: $TOKEN" \
      | jq '.data.data' > "$REL/config/secrets.json"
    curl -sk "${VAULT}saathi_backend_env" -H "X-Vault-Token: $TOKEN" \
      | jq '.data.data' | bash "$REL/scripts/json2env.sh" > "$REL/.env"

    # 2. Stop services and swap in the release
    #    (deletes the old logs/, as the playbook does; copy them first if needed)
    systemctl stop saathi-uvicorn saathi-celery
    rm -rf "$LIVE" && mv "$REL" "$LIVE"
    cd "$LIVE" && mkdir -p logs

    # 3. Install dependencies and apply migrations 0105–0108
    uv sync --frozen
    uv run --frozen python manage.py migrate --noinput

    # 4. Start services
    systemctl start saathi-uvicorn saathi-celery
    ```

    The systemd units are unchanged. On a new host, render both templates from
    [`deploy/ansible/templates/`](../../../../deploy/ansible/templates/) with the
    `deploy.yml` vars, write them to `/etc/systemd/system/`, then run
    `systemctl daemon-reload` and `systemctl enable saathi-uvicorn saathi-celery`.

    </details>

2.  **Sync the bots.** On the backend host, from the live directory, run the
    four [bot setup scripts](#bot-setup-scripts) **in this order**:
    1. title
    2. MIP summary
    3. profile
    4. saathi (its flow links the title and story bots by route)

    For each script:
    1. Run a **dry run** and review the output.
    2. Run again with `--apply`.
    3. Run the dry run again: it must report `0 to create, 0 to update`.

    ```bash
    cd /opt/deployment/backend/saathi-service/saathi-backend-service
    uv run --frozen python chatbot/scripts/bot_setup/sync_saathi_title_bot.py --company <slug>          # review
    uv run --frozen python chatbot/scripts/bot_setup/sync_saathi_title_bot.py --company <slug> --apply  # answer y
    # same for sync_saathi_mip_summary_bot.py, sync_saathi_profile_bot.py, then sync_saathi_bot.py
    ```

    The expected first run is listed per script below.

    > **Stop rule.** The files overwrite the managed fields: prompts, settings,
    > voices, vernaculars, flow fields, rules and templates. If a dry run shows
    > a change you didn't plan, production has config that isn't in the files.
    > Stop, add that config to the file (with an UPDATE NOTES entry), and
    > dry-run again before `--apply`.

3.  **Verify.** Migrations applied, the new `.env` value in place, both services
    active, `/health/` returns 200:

    ```bash
    cd /opt/deployment/backend/saathi-service/saathi-backend-service
    uv run --frozen python manage.py showmigrations chatbot | tail -4   # 0105–0108 all [X]
    grep '^MIP_DEFAULT_MAX_PER_CHAT=' .env                              # MIP_DEFAULT_MAX_PER_CHAT=1
    uv run --frozen python manage.py shell -c \
      "from django.conf import settings; print(settings.MIP_DEFAULT_MAX_PER_CHAT)"   # 1
    systemctl is-active saathi-uvicorn saathi-celery
    curl -fsS http://localhost:9000/health/
    ```

### Rollback

Redeploy the previous release tag with Jenkins or the manual steps. Its code
doesn't use the new tables, so they can stay in place.

Revert the bot configuration written by the setup scripts in one of two ways:
- **Per object**, from the Django admin **History**. The reason is
  `<script> 1.5.0`, e.g. `sync_saathi_bot 1.5.0`.
- **From an earlier version of the files:** run
  `git checkout <rev> -- chatbot/scripts/bot_setup/`, then the scripts with
  `--apply`.

Each `--apply` writes a before-snapshot to `logs/bot_setup/<script>/`. Rows the
scripts *created* (e.g. missing voices or vernaculars) have no earlier version;
delete them in admin if needed.

To drop the tables as well, run
`uv run --frozen python manage.py migrate chatbot 0104_media_template`
**before** redeploying, while the 1.5.0 code is still live (the previous
checkout doesn't include `0105`–`0108`). This deletes all MIP rows.

---

## Bot setup scripts

`chatbot/scripts/bot_setup/` holds **one independent script per bot** and the
data each one writes.

### How every script behaves

| Object | Matched by | Missing | Differs from the file | Never |
|---|---|---|---|---|
| Language | ISO code | created | name kept (the table is shared) | renamed / deleted |
| Bot (CompanyBot) | company + route | created | prompt fields, tools, owned `other_params` keys and settings updated | `provider_keys` / `llm_key` touched |
| Voice (STT / TTS / translate / transliterate) | bot + type + language | created | differing fields updated | deleted; fallback rows touched |
| Vernacular (name, intro, error message) | bot + language | created | differing fields updated | deleted |
| Flow | `flow_route` | created | only differing fields updated; links to other bots resolved by route | link cleared because its bot is missing |
| Check-in rule (`saathi` flow) | flow + code | created | updated | deleted |
| Media template (`saathi` flow) | flow + type | created | updated; a stored file is reused when the bytes match, else uploaded under a new name | stored file overwritten; deleted |

Each language has an `enabled` flag: `True` puts it in the flow's `languages`
list (what users can pick); `False` removes it from the list but keeps its
voice and vernacular rows.

Rows in the database but not in the file are listed as `[not managed]` and left
alone. Unchanged rows are not saved, so they get no history row.

**Dry run is the default:** the script prints every change and writes nothing.
With `--apply`, the script:
1. prints the plan;
2. asks once for the bot part (languages, settings, prompts, voices,
   vernaculars) and once for the flow part (flow, rules, templates); `--yes`
   answers yes to both for non-interactive runs;
3. writes a before-snapshot to `logs/bot_setup/<script>/`;
4. saves everything in one transaction, re-reads every written row and prints
   `VERIFIED`. Any mismatch rolls everything back.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Done, also when a WARNING was printed |
| 1 | Error, nothing written: bad file, unknown company, flow owned by another company, verification failed |
| 2 | Refused, nothing written: breaking tool change without `--allow-breaking`, or no terminal and no `--yes` |

**WARNINGs never stop a run.** They are printed and logged when:
- a voice's provider has no `Provider` row: the voice is skipped and runtime
  falls back to the English voice;
- a flow link's bot doesn't exist yet: the WARNING names the script to run first;
- a stored template file can't be read: it is not compared or replaced.

| Command (from `saathi-backend`) | |
|---|---|
| `uv run --frozen python chatbot/scripts/bot_setup/<script>.py --company <slug>` | dry run |
| `… --apply` | write, asking first |
| `… --apply --yes` | write without asking |
| `… --apply --allow-breaking` | also allow removing a tool or parameter, or making one required |
| Locally: `./dev.sh backend-script chatbot/scripts/bot_setup/<script>.py --company shikshalokamstaging` | |

### 1. `sync_saathi_title_bot.py`: `/saathi-title`

Writes `saathi_title_bot.py`: the bot's prompt, the `generate_title` tool and
settings, the Language rows, and 15 voices (STT, TTS and translate for en, hi,
kn, or, ta). The TextToText voices translate chat titles for non-English chats
(`celery_tasks/title_tasks.py`). It has no flow.

Expected first run: bot UNCHANGED, or UPDATED only where production differs
(review per the stop rule); missing voices CREATED.

### 2. `sync_saathi_mip_summary_bot.py`: `/saathi-mip-summary`

Writes `mip_summary_bot.py`: the bot's prompt, the `submit_tracking_summary`
tool and settings. No languages, voices or flow; the backend calls it directly.

Expected first run: bot CREATED.

### 3. `sync_saathi_profile_bot.py`: `/saathi-profile` + flow `saathi_profile`

Writes `saathi_profile_bot.py`: the bot's prompt, the `submit_user_context` /
`respond_to_user` tools and settings, the Language rows, 20 voices (STT, TTS,
translate, and AI4Bharat transliterate for names and places), 5 vernaculars and
the `saathi_profile` flow.

Expected first run: bot and flow UNCHANGED, or UPDATED only where production
differs; missing voices and vernaculars CREATED.

### 4. `sync_saathi_bot.py`: `/saathi-bot` + flow `saathi`

Writes:
- from `saathi_bot.py`: the bot's prompt, tools, `other_params.mip`, settings,
  the Language rows, 15 voices, 5 vernaculars and the `saathi` flow (links
  `title_bot` → `/saathi-title` and `story_bot` → `/saathi-mip-summary` by
  route);
- the 8 check-in rules from `mip_check_in_rules.py`;
- MIP_PDF / MIP_DOCX from `saathi_media_templates.py` (files in `media/`).

Expected first run:
- `/saathi-bot` UPDATED: `context`, `tool_context`, `other_params`, plus
  `gateway_model` if production isn't on `anthropic/claude-haiku-5.5`. The
  prompt diff should only be the `### download_file` bot_message bullets and the
  `bot_message` description, with `tools: contract unchanged`.
- Flow `saathi` UPDATED: `story_bot`, plus `title_bot` if not linked.
- 8 rules CREATED.
- Templates UNCHANGED or UPDATED.

### Fresh install

On a new environment with no bot rows:
1. Run `./dev.sh setup backend` (on a server: `migrate` + `prepare_db`) for the
   tables, Provider rows and company. The seed isn't needed.
2. Run the four scripts with `--apply` in the order above.
3. Re-run each dry run: all must report `0 to create, 0 to update`.

If `sync_saathi_bot.py` runs before the title and MIP summary bots exist, it
creates the flow anyway, prints a WARNING naming the missing script, and links
those bots on its next run.

---

## References

- Deployment: [`deploy.yml`](../../../../deploy/ansible/deploy.yml),
  [systemd templates](../../../../deploy/ansible/templates/),
  [`json2env.sh`](../../../../scripts/json2env.sh)
- Code: [`mip_models.py`](../../../../chatbot/models/mip_models.py),
  [`enums.py`](../../../../chatbot/models/enums.py),
  [migrations](../../../../chatbot/migrations/),
  [`services/mip/creation.py`](../../../../chatbot/services/mip/creation.py),
  [`common_handler.py`](../../../../chatbot/services/response_handlers/common_handler.py)
- Bot configuration (source of truth): [`chatbot/scripts/bot_setup/`](../../../../chatbot/scripts/bot_setup/)
  (data modules, shared [`engine.py`](../../../../chatbot/scripts/bot_setup/engine.py), the four
  `sync_saathi_*.py` scripts). To keep a copy of a bot's current config, export
  it in Django admin (Company bots → select → **Export selected bots**).
- Future changes:
  1. Edit the bot's data module in `chatbot/scripts/bot_setup/` (prompt,
     settings, voices, languages, flow) and add an UPDATE NOTES entry.
  2. Run that bot's script as a dry run and review the diff.
  3. Run it again with `--apply`.

  Don't edit these bots in admin: the next run overwrites the managed fields.
- Docs: [Models](../models.md), [Enums](../../../backend/enums.md),
  [Developer Setup](../../../setup/developer_setup.md)
