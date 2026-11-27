"""
Django admin for MIP tracking (Saathi 1.5.0, BE-02; design: Plans/SAATHI_1.5.0_MIP_Tracking_README.md 4.8).

Who may change what (superusers only; everyone else, moderators included, only views):
- MIP: the tracking fields (title, status, dates, check-ins, flags). Identity (ID, profile,
  user ID, chat, company) and timestamps stay locked; status_changed_at follows status.
- Story: every field except its MIP.
- Check-in rules: add and edit. Rules whose code is in
  chatbot/scripts/bot_setup/mip_check_in_rules.py are rewritten by the next
  sync_saathi_bot.py --apply; rules with other codes are left alone by it.
- Plan (MipDetails): read-only, like its generated PDF / DOCX.
Nobody adds MIPs, plans or stories (the chat creates them), deletes anything, or reverts
from History. Admin edits are recorded in History with the user and "Edited in admin".

The MIP page shows the MIP with its plan (MipDetails) and story (MipStory) below it, and
the MIP's History page lists the changes to all three in one timeline; each list has a
History column. Superusers see every row; a moderator sees only their company's rows
(Mip.company), like the other admin screens, History pages included; any other staff user
sees none.
"""
import string

from django import forms
from django.contrib import admin
from django.contrib.admin.utils import unquote
from django.contrib.admin.views.main import PAGE_VAR
from django.contrib.auth import get_permission_codename
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.timezone import localtime
from simple_history.admin import SimpleHistoryAdmin

from chatbot.constants.constants import (
    ADMIN_SAFE_LINK_PREFIXES, MIP_ADMIN_EXPORT_FILENAME, MIP_FILE_DOCX, MIP_FILE_PDF, MIP_HISTORY_REASON_ADMIN_ADDED,
    MIP_HISTORY_REASON_ADMIN_EDITED, MIP_RULE_PLACEHOLDER_END_DATE, MIP_RULE_PLACEHOLDER_START_DATE,
    MIP_RULE_PLACEHOLDER_TITLE, MIP_STEP_ACTION, MIP_STEP_STATUS, MIP_STEP_WEEK, MIP_STORY_ELEMENT_SOURCE,
    MIP_STORY_ELEMENT_TEXT,
)
from chatbot.constants.messages import (
    ADMIN_MSG_MIP_RULE_BROKEN_PLACEHOLDER, ADMIN_MSG_MIP_RULE_CHIPS, ADMIN_MSG_MIP_RULE_DAY_OFFSETS,
    ADMIN_MSG_MIP_RULE_SYNC_NOTE, ADMIN_MSG_MIP_RULE_UNKNOWN_PLACEHOLDER,
)
from chatbot.filter.custom_date_from_filter import CustomAdvanceDateFilter
from chatbot.filter.mip_filters import (
    DateRangeFieldListFilter, LinkedMipCompanyFilter, LinkedMipIdFilter, LinkedMipSessionIdFilter,
    LinkedMipUserIdFilter, MipCompanyFilter, MipIdFilter, MipSessionIdFilter, MipStatusFilter,
    MipUserIdFilter, get_moderator_profile,
)
from chatbot.models import Mip, MipCheckInRule, MipDetails, MipStory
from chatbot.models.enums import MipCheckInSchedule
from chatbot.utils.admin_config.export_mixin import ExportAllFieldsMixin


# ── display helpers (format_html escapes all LLM / user text) ───────────────
def _related(obj, *path):
    """Follow attribute `path` from `obj`; None when a link or a 1:1 row is missing."""
    for name in path:
        try:
            obj = getattr(obj, name)
        except ObjectDoesNotExist:
            return None
        if obj is None:
            return None
    return obj


def _admin_link(obj, label=None):
    """Link to `obj`'s admin page, labelled `label` (default str(obj))."""
    if obj is None:
        return None
    opts = obj._meta
    url = reverse(f'admin:{opts.app_label}_{opts.model_name}_change', args=[obj.pk])
    return format_html('<a href="{}">{}</a>', url, obj if label in (None, '') else label)


def _file_links(files):
    """{"pdf": url, "docx": url} as links; a value that isn't an http(s) URL is shown as text."""
    if not isinstance(files, dict) or not files:
        return None
    parts = []
    for name, url in files.items():
        if isinstance(url, str) and url.startswith(ADMIN_SAFE_LINK_PREFIXES):
            parts.append(format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                                     url, str(name).upper()))
        else:
            parts.append(format_html('{}: {}', str(name).upper(), url))
    return format_html_join(' · ', '{}', ((part,) for part in parts))


def _steps_table(steps):
    """MipDetails.action_steps as a No. / Action / Week / Status table."""
    if not isinstance(steps, list) or not steps:
        return None
    rows = format_html_join(
        '', '<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>',
        ((number, step.get(MIP_STEP_ACTION, ''), step.get(MIP_STEP_WEEK, ''), step.get(MIP_STEP_STATUS, ''))
         if isinstance(step, dict) else (number, step, '', '')
         for number, step in enumerate(steps, start=1)),
    )
    return format_html(
        '<table class="table table-sm table-bordered mb-0"><thead><tr>'
        '<th>No.</th><th>Action</th><th>Week</th><th>Status</th></tr></thead><tbody>{}</tbody></table>',
        rows,
    )


def _bullet_list(items):
    if not isinstance(items, list):
        return items or None
    if not items:
        return None
    return format_html('<ul class="mb-0 pl-3">{}</ul>', format_html_join('', '<li>{}</li>', ((i,) for i in items)))


def _captured_elements(elements):
    """MipStory.captured_elements: element name -> text (and where the text came from)."""
    if not isinstance(elements, dict) or not elements:
        return None
    rows = []
    for name, item in elements.items():
        if isinstance(item, dict):
            text, source = item.get(MIP_STORY_ELEMENT_TEXT, ''), item.get(MIP_STORY_ELEMENT_SOURCE)
        else:
            text, source = item, None
        label = str(name).replace('_', ' ').capitalize()
        if source:
            rows.append(format_html('<dt>{}</dt><dd>{} <small class="text-muted">({})</small></dd>',
                                    label, text, source))
        else:
            rows.append(format_html('<dt>{}</dt><dd>{}</dd>', label, text))
    return format_html('<dl class="mb-0">{}</dl>', format_html_join('', '{}', ((row,) for row in rows)))


def _naive_local(value):
    """Datetime for Excel (no timezone support): local time, tzinfo dropped."""
    return localtime(value).replace(tzinfo=None) if value else ''


def _can_view_mips(request):
    opts = Mip._meta
    return request.user.has_perm(f'{opts.app_label}.{get_permission_codename("view", opts)}')


# ── mixins ───────────────────────────────────────────────────────────────────
class ReadOnlyAdminMixin:
    """No add, change or delete for anyone; the view page and History stay."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        # simple_history saves a POSTed old version with only a view permission check, so
        # every MIP screen refuses the POST itself: no reverts, editors included.
        # Viewing an old version (GET) stays.
        if request.method == 'POST':
            raise PermissionDenied
        return super().history_form_view(request, object_id, version_id, extra_context)

    def revert_disabled(self, request, obj=None):
        # Hides simple_history's Revert button and its "choose a date to revert" text.
        return True


class SuperuserEditMixin(ReadOnlyAdminMixin):
    """Superusers may change rows (no add or delete); everyone else only views."""

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        # Django saves the row on every Save, even when only an inline changed;
        # skip that save so History gets no empty "Changed" row.
        if change and not form.has_changed():
            return
        obj._change_reason = MIP_HISTORY_REASON_ADMIN_EDITED if change else MIP_HISTORY_REASON_ADMIN_ADDED
        super().save_model(request, obj, form, change)


class SuperuserAddEditMixin(SuperuserEditMixin):
    """Superusers may also add rows (still no delete)."""

    def has_add_permission(self, request, obj=None):
        return request.user.is_superuser


class EditFieldsMixin:
    """
    Editors (change permission) see `edit_fields`, of which `locked_fields` stay read-only;
    viewers see `view_fields`, all read-only (rendered versions of the JSON fields).
    """
    view_fields = edit_fields = locked_fields = ()

    def get_fields(self, request, obj=None):
        return self.edit_fields if self.has_change_permission(request, obj) else self.view_fields

    def get_readonly_fields(self, request, obj=None):
        return self.locked_fields if self.has_change_permission(request, obj) else self.view_fields


class MipCompanyScopeMixin:
    """
    Rows a user may see: superuser -> all; moderator -> their company's rows
    (`company_lookup`, the ORM path to the Company); anyone else -> none.
    """
    company_lookup = 'company'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        profile = get_moderator_profile(request)
        if profile and profile.company_id:
            return qs.filter(**{self.company_lookup: profile.company_id})
        return qs.none()

    # simple_history's History pages read the history table directly (the list falls back to
    # it when the row isn't in get_queryset), so both must check the row is visible first.
    def history_view(self, request, object_id, extra_context=None):
        self._require_visible(request, object_id)
        return super().history_view(request, object_id, extra_context)

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        self._require_visible(request, object_id)
        return super().history_form_view(request, object_id, version_id, extra_context)

    def _require_visible(self, request, object_id):
        try:
            visible = self.get_queryset(request).filter(pk=unquote(object_id)).exists()
        except (ValueError, ValidationError):   # a non-numeric ID in the URL
            visible = False
        if not visible:
            raise Http404


class HistoryLinkColumnMixin:
    """A History column in the list, linking to the row's History page."""

    @admin.display(description='History')
    def history_link(self, obj):
        opts = obj._meta
        url = reverse(f'admin:{opts.app_label}_{opts.model_name}_history', args=[obj.pk])
        return format_html('<a href="{}">History</a>', url)


class LinkedMipColumnsMixin:
    """List columns for MipDetails / MipStory: their MIP and its user, chat and company."""

    @admin.display(description='MIP', ordering='mip_id')
    def mip_link(self, obj):
        return _admin_link(obj.mip)

    @admin.display(description='User ID', ordering='mip__user_id')
    def mip_user_id(self, obj):
        return obj.mip.user_id

    @admin.display(description='Chat session ID', ordering='mip__chat_session__session')
    def mip_session(self, obj):
        return obj.mip.chat_session.session

    @admin.display(description='Company', ordering='mip__company__name')
    def mip_company(self, obj):
        return obj.mip.company


class MipDetailsDisplayMixin:
    """Rendered plan fields, shared by the MIP page's plan section and the MIP details screen."""

    @admin.display(description='Action steps')
    def action_steps_table(self, obj):
        return _steps_table(obj.action_steps)

    @admin.display(description='Success indicators')
    def success_indicators_list(self, obj):
        return _bullet_list(obj.success_indicators)

    @admin.display(description='Plan files')
    def plan_files(self, obj):
        return _file_links(obj.files)


class MipStoryDisplayMixin:
    """Rendered story fields, shared by the MIP page's story section and the MIP story screen."""

    @admin.display(description='Captured elements')
    def captured_elements_view(self, obj):
        return _captured_elements(obj.captured_elements)

    @admin.display(description='Captured elements (as shown)')
    def captured_elements_preview(self, obj):
        # Editors' read-only preview beside the JSON box (which has the "Captured elements" label).
        return self.captured_elements_view(obj)

    @admin.display(description='Report files')
    def report_files(self, obj):
        return _file_links(obj.files)


# ── MIP page sections ────────────────────────────────────────────────────────
class _MipSectionInline(ReadOnlyAdminMixin, admin.StackedInline):
    can_delete = False
    extra = 0
    max_num = 1

    def has_view_permission(self, request, obj=None):
        # Whoever may see the MIP sees its plan and story; no separate permission needed.
        return _can_view_mips(request)


class MipDetailsInline(MipDetailsDisplayMixin, _MipSectionInline):
    model = MipDetails
    verbose_name = verbose_name_plural = 'Plan (MIP details)'
    fields = ('goal', 'objective', 'duration_days', 'language', 'action_steps_table',
              'success_indicators_list', 'plan_files', 'created_at')
    readonly_fields = fields


# Story fields: rendered for viewers; for editors (superusers) raw JSON boxes, each followed
# by its rendered preview of the saved value.
_STORY_VIEW_FIELDS = ('status', 'provider', 'external_session_id', 'language', 'captured_elements_view',
                      'story_text', 'report_files', 'generated_at', 'created_at', 'updated_at')
_STORY_EDIT_FIELDS = ('status', 'provider', 'external_session_id', 'language', 'captured_elements',
                      'captured_elements_preview', 'story_text', 'files', 'report_files', 'generated_at',
                      'created_at', 'updated_at')
_STORY_LOCKED_FIELDS = ('captured_elements_preview', 'report_files', 'created_at', 'updated_at')


class MipStoryInline(EditFieldsMixin, SuperuserEditMixin, MipStoryDisplayMixin, _MipSectionInline):
    model = MipStory
    verbose_name = verbose_name_plural = 'Improvement story'
    view_fields = _STORY_VIEW_FIELDS
    edit_fields = _STORY_EDIT_FIELDS
    locked_fields = _STORY_LOCKED_FIELDS


# ── screens ──────────────────────────────────────────────────────────────────
# What the MIP's History page lists: (Part column label, model, history field holding the MIP id).
_MIP_HISTORY_PARTS = (('MIP', Mip, 'id'), ('Plan', MipDetails, 'mip_id'), ('Story', MipStory, 'mip_id'))


@admin.register(Mip)
class MipAdmin(MipCompanyScopeMixin, SuperuserEditMixin, HistoryLinkColumnMixin, ExportAllFieldsMixin,
               SimpleHistoryAdmin):
    company_lookup = 'company'
    object_history_list_template = 'admin/mip/history_timeline_list.html'
    list_display = ('id', 'title', 'user_id', 'company', 'status', 'story_status', 'start_date', 'end_date',
                    'next_check_in', 'chat_link', 'created_at', 'history_link')
    list_display_links = ('id', 'title')
    list_select_related = ('company', 'next_check_in_rule', 'chat_session', 'story')
    list_filter = (
        MipIdFilter, MipUserIdFilter, MipSessionIdFilter,
        CustomAdvanceDateFilter,
        ('start_date', DateRangeFieldListFilter),
        ('end_date', DateRangeFieldListFilter),
        ('status', MipStatusFilter),
        MipCompanyFilter,
        'is_tracking_declined',
        ('next_check_in_rule', admin.RelatedOnlyFieldListFilter),
    )
    # id__exact compares the ID as text, so a non-numeric search term is safe.
    search_fields = ('id__exact', 'title', 'user_id', 'chat_session__session', 'profile__first_name',
                     'profile__email')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 50
    fieldsets = (
        ('MIP', {'fields': ('id', 'title', 'status', 'status_changed_at', 'company', 'user_id', 'profile_link',
                            'chat_link', 'created_at', 'updated_at')}),
        ('Timeline', {'fields': ('start_date', 'planned_end_date', 'end_date')}),
        ('Check-ins', {'fields': ('next_check_in_rule', 'next_check_in_on', 'check_in_count', 'last_check_in_at',
                                  'is_tracking_declined', 'help_note_shown')}),
    )
    # Locked for editors too: identity, links and timestamps (status_changed_at follows status).
    # Viewers see every field read-only.
    readonly_fields = ('id', 'status_changed_at', 'company', 'user_id', 'profile_link', 'chat_link', 'created_at',
                       'updated_at')
    inlines = (MipDetailsInline, MipStoryInline)

    export_filename = MIP_ADMIN_EXPORT_FILENAME
    extra_export_fields = {
        'chat_session_id': lambda mip: mip.chat_session.session,
        'company_name': lambda mip: mip.company.name if mip.company else '',
        'plan_goal': lambda mip: _related(mip, 'details', 'goal') or '',
        'plan_objective': lambda mip: _related(mip, 'details', 'objective') or '',
        'plan_duration_days': lambda mip: _related(mip, 'details', 'duration_days') or '',
        'plan_language': lambda mip: _related(mip, 'details', 'language') or '',
        'plan_pdf': lambda mip: (_related(mip, 'details', 'files') or {}).get(MIP_FILE_PDF, ''),
        'plan_docx': lambda mip: (_related(mip, 'details', 'files') or {}).get(MIP_FILE_DOCX, ''),
        'story_status': lambda mip: _related(mip, 'story', 'status') or '',
        'story_provider': lambda mip: _related(mip, 'story', 'provider') or '',
        'story_generated_at': lambda mip: _naive_local(_related(mip, 'story', 'generated_at')),
        'story_pdf': lambda mip: (_related(mip, 'story', 'files') or {}).get(MIP_FILE_PDF, ''),
    }

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('company', 'chat_session', 'profile', 'next_check_in_rule')

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change=change, **kwargs)
        rule_field = form.base_fields.get('next_check_in_rule')
        if rule_field is not None and obj is not None and obj.company_id:
            # Only the MIP's company's rules (codes repeat across flows), plus its current one.
            rule_field.queryset = rule_field.queryset.filter(
                Q(flow__bot__company=obj.company_id) | Q(pk=obj.next_check_in_rule_id))
        return form

    def save_model(self, request, obj, form, change):
        if 'status' in form.changed_data:
            obj.status_changed_at = timezone.now()
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        # No HistoryRequestMiddleware, so pass the user (and reason) to the story's History row.
        for inline_form in formset.forms:
            inline_form.instance._history_user = request.user
            inline_form.instance._change_reason = MIP_HISTORY_REASON_ADMIN_EDITED
        super().save_formset(request, form, formset, change)

    def render_history_view(self, request, template, context, **kwargs):
        # history_view has already found the MIP (company-scoped) and checked permissions;
        # this swaps its MIP-only list for the MIP + plan + story timeline.
        if template == self.object_history_template:
            context.update(self._history_timeline(request, context['object']))
        return super().render_history_view(request, template, context, **kwargs)

    def _history_timeline(self, request, mip):
        records = []
        for label, model, mip_field in _MIP_HISTORY_PARTS:
            model_admin = self.admin_site._registry[model]
            part = list(model_admin.get_history_queryset(request, model.history, mip_field, mip.pk))
            # Changes are diffed within one model's records, before the parts are merged.
            model_admin.set_history_delta_changes(request, part)
            opts = model._meta
            can_view = model_admin.has_view_permission(request)
            for record in part:
                record.timeline_part = label
                record.timeline_url = reverse(
                    f'admin:{opts.app_label}_{opts.model_name}_simple_history', args=[record.id, record.history_id],
                ) if can_view else ''
            records.extend(part)
        records.sort(key=lambda record: record.history_date, reverse=True)

        paginator = Paginator(records, self.history_list_per_page)
        page_obj = paginator.get_page(request.GET.get(PAGE_VAR))
        return {
            'page_obj': page_obj,
            'page_range': paginator.get_elided_page_range(page_obj.number),
            'pagination_required': paginator.count > self.history_list_per_page,
        }

    @admin.display(description='Story', ordering='story__status')
    def story_status(self, obj):
        story = _related(obj, 'story')
        return story.get_status_display() if story else None

    @admin.display(description='Next check-in', ordering='next_check_in_on')
    def next_check_in(self, obj):
        if not obj.next_check_in_rule_id:
            return None
        return f'{obj.next_check_in_rule.code} @ {obj.next_check_in_on}'

    @admin.display(description='Chat', ordering='chat_session__session')
    def chat_link(self, obj):
        return _admin_link(obj.chat_session, obj.chat_session.session)

    @admin.display(description='Profile')
    def profile_link(self, obj):
        profile = obj.profile
        return _admin_link(profile, profile.first_name or profile.userid or f'Profile {profile.pk}')


@admin.register(MipDetails)
class MipDetailsAdmin(MipCompanyScopeMixin, ReadOnlyAdminMixin, HistoryLinkColumnMixin, LinkedMipColumnsMixin,
                      MipDetailsDisplayMixin, SimpleHistoryAdmin):
    company_lookup = 'mip__company'
    list_display = ('mip_link', 'mip_user_id', 'mip_session', 'mip_company', 'duration_days', 'language',
                    'created_at', 'history_link')
    list_select_related = ('mip__company', 'mip__chat_session')
    list_filter = (
        LinkedMipIdFilter, LinkedMipUserIdFilter, LinkedMipSessionIdFilter,
        CustomAdvanceDateFilter,
        LinkedMipCompanyFilter,
        'language',
    )
    search_fields = ('mip__id__exact', 'mip__title', 'mip__user_id', 'mip__chat_session__session', 'goal')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 50
    fields = ('mip_link', 'goal', 'objective', 'duration_days', 'language', 'action_steps_table',
              'success_indicators_list', 'plan_files', 'created_at', 'updated_at')
    readonly_fields = fields


@admin.register(MipStory)
class MipStoryAdmin(MipCompanyScopeMixin, EditFieldsMixin, SuperuserEditMixin, HistoryLinkColumnMixin,
                    LinkedMipColumnsMixin, MipStoryDisplayMixin, SimpleHistoryAdmin):
    company_lookup = 'mip__company'
    list_display = ('mip_link', 'mip_user_id', 'mip_session', 'mip_company', 'status', 'provider',
                    'external_session_id', 'language', 'generated_at', 'created_at', 'history_link')
    list_select_related = ('mip__company', 'mip__chat_session')
    list_filter = (
        LinkedMipIdFilter, LinkedMipUserIdFilter, LinkedMipSessionIdFilter,
        CustomAdvanceDateFilter,
        LinkedMipCompanyFilter,
        'status',
        'provider',
    )
    search_fields = ('mip__id__exact', 'mip__user_id', 'mip__chat_session__session', 'external_session_id')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 50
    view_fields = ('mip_link',) + _STORY_VIEW_FIELDS
    edit_fields = ('mip_link',) + _STORY_EDIT_FIELDS
    locked_fields = ('mip_link',) + _STORY_LOCKED_FIELDS


# Placeholders a rule's message may use (the same ones sync_saathi_bot.py allows).
_RULE_PLACEHOLDERS = (MIP_RULE_PLACEHOLDER_TITLE, MIP_RULE_PLACEHOLDER_START_DATE, MIP_RULE_PLACEHOLDER_END_DATE)


def _braced(names):
    return ', '.join(f'{{{name}}}' for name in names)


class MipCheckInRuleAdminForm(forms.ModelForm):
    """
    The checks sync_saathi_bot.py runs on its rules, so admin can't save a rule it would refuse.
    "Exactly one schedule parameter" and "code unique per flow" are model constraints,
    which the form validates too.
    """

    class Meta:
        model = MipCheckInRule
        fields = '__all__'

    def clean_message_template(self):
        template = self.cleaned_data['message_template']
        try:
            names = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
        except ValueError as exc:
            raise forms.ValidationError(ADMIN_MSG_MIP_RULE_BROKEN_PLACEHOLDER.format(error=exc))
        unknown = names - set(_RULE_PLACEHOLDERS)
        if unknown:
            raise forms.ValidationError(ADMIN_MSG_MIP_RULE_UNKNOWN_PLACEHOLDER.format(
                unknown=_braced(sorted(unknown)), allowed=_braced(_RULE_PLACEHOLDERS)))
        return template

    def clean_quick_reply_chips(self):
        chips = self.cleaned_data.get('quick_reply_chips')
        if chips in (None, ''):
            return []
        if not isinstance(chips, list) or not all(isinstance(chip, str) and chip.strip() for chip in chips):
            raise forms.ValidationError(ADMIN_MSG_MIP_RULE_CHIPS)
        return chips

    def clean_day_offsets(self):
        offsets = self.cleaned_data.get('day_offsets')
        if offsets is None:
            return None
        if not isinstance(offsets, list) or not all(
                isinstance(day, int) and not isinstance(day, bool) and day >= 0 for day in offsets):
            raise forms.ValidationError(ADMIN_MSG_MIP_RULE_DAY_OFFSETS)
        return offsets


@admin.register(MipCheckInRule)
class MipCheckInRuleAdmin(MipCompanyScopeMixin, SuperuserAddEditMixin, HistoryLinkColumnMixin, SimpleHistoryAdmin):
    """
    Superusers may add and edit rules. Rules whose code is in
    chatbot/scripts/bot_setup/mip_check_in_rules.py are rewritten by the next
    sync_saathi_bot.py --apply (the form says so); others are left alone by it.
    """
    form = MipCheckInRuleAdminForm
    fieldsets = (
        ('Check-in rule', {   # named: Jazzmin shows a description only on a named fieldset
            'description': ADMIN_MSG_MIP_RULE_SYNC_NOTE,
            'fields': ('flow', 'code', 'name', 'is_active', 'trigger', 'schedule_type', 'day_offsets',
                       'every_n_days', 'percent_of_timeline', 'max_asks', 'priority', 'message_template',
                       'quick_reply_chips', 'created_at', 'updated_at'),
        }),
    )
    readonly_fields = ('created_at', 'updated_at')
    company_lookup = 'flow__bot__company'
    list_display = ('code', 'name', 'flow', 'trigger', 'schedule_type', 'schedule_value', 'max_asks', 'priority',
                    'is_active', 'history_link')
    list_select_related = ('flow',)
    list_filter = (('flow', admin.RelatedOnlyFieldListFilter), 'trigger', 'schedule_type', 'is_active')
    search_fields = ('code', 'name')
    ordering = ('flow', 'priority', 'code')

    @admin.display(description='Schedule value')
    def schedule_value(self, obj):
        if obj.schedule_type == MipCheckInSchedule.DAY_OFFSETS:
            return f"days {', '.join(str(day) for day in obj.day_offsets or [])}"
        if obj.schedule_type == MipCheckInSchedule.EVERY_N_DAYS:
            return f'every {obj.every_n_days} days'
        if obj.schedule_type == MipCheckInSchedule.PERCENT_OF_TIMELINE:
            return f'{obj.percent_of_timeline}% of timeline'
        return None
