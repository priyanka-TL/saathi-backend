"""
List filters for the MIP admin screens (chatbot/admin/mip_admin.py).

The Mip screen filters on its own fields; the MipDetails / MipStory screens filter
on the same fields through their `mip` link. The "Linked..." subclasses only add
the `mip__` prefix.

Typed boxes (MIP ID, user ID, chat session ID) and the date ranges render as inputs
inside Jazzmin's filter form (templates/admin/filters/), so both a plain submit and the
Search-button script in templates/admin/filter.html put their values in the URL. An
empty value means "no filter", and the "x" link next to a filled box clears it.
"""
from django.contrib import admin
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.utils import get_last_value_from_parameters
from django.core.exceptions import ValidationError

from chatbot.constants.constants import (
    ADMIN_DATE_RANGE_FILTER_TEMPLATE, ADMIN_TEXT_INPUT_FILTER_TEMPLATE, MIP_FILTER_PARAM_COMPANY, MIP_FILTER_PARAM_MIP_ID,
    MIP_FILTER_PARAM_SESSION_ID, MIP_FILTER_PARAM_USER_ID,
)
from chatbot.models import Company, Profile, ProfileType

LINKED_MIP_PREFIX = 'mip__'   # ORM path from MipDetails / MipStory to their Mip


def get_moderator_profile(request):
    """The admin user's MODERATOR profile (matched by email, as on the other admin screens), else None."""
    email = getattr(request.user, 'email', None)
    if not email:
        return None
    return (Profile.objects.select_related('company')
            .filter(email=email, profile_type=ProfileType.MODERATOR).first())


# ── typed boxes ──────────────────────────────────────────────────────────────
class TextInputFilter(admin.SimpleListFilter):
    """A free-text box matched exactly against `lookup_prefix + lookup` (surrounding spaces ignored)."""
    template = ADMIN_TEXT_INPUT_FILTER_TEMPLATE
    lookup_prefix = ''
    lookup = None

    def lookups(self, request, model_admin):
        return ()

    def has_output(self):
        return True

    def get_facet_counts(self, pk_attname, filtered_qs):
        return {}

    def choices(self, changelist):
        # One entry only: its query_string is the "clear this box" link.
        yield {
            'selected': not self.typed_value(),
            'query_string': changelist.get_query_string(remove=[self.parameter_name]),
            'display': self.title,
        }

    def typed_value(self):
        return (self.value() or '').strip()

    def queryset(self, request, queryset):
        value = self.typed_value()
        if not value:
            return queryset
        return queryset.filter(**{f'{self.lookup_prefix}{self.lookup}': value})


class MipIdFilter(TextInputFilter):
    title = 'MIP ID'
    parameter_name = MIP_FILTER_PARAM_MIP_ID
    lookup = 'pk'

    def queryset(self, request, queryset):
        value = self.typed_value()
        if value and not value.isdigit():
            return queryset.none()   # a MIP ID is a number; anything else matches nothing
        return super().queryset(request, queryset)


class MipUserIdFilter(TextInputFilter):
    title = 'User ID'
    parameter_name = MIP_FILTER_PARAM_USER_ID
    lookup = 'user_id'


class MipSessionIdFilter(TextInputFilter):
    title = 'Chat session ID'
    parameter_name = MIP_FILTER_PARAM_SESSION_ID
    lookup = 'chat_session__session'


class LinkedMipIdFilter(MipIdFilter):
    lookup_prefix = LINKED_MIP_PREFIX


class LinkedMipUserIdFilter(MipUserIdFilter):
    lookup_prefix = LINKED_MIP_PREFIX


class LinkedMipSessionIdFilter(MipSessionIdFilter):
    lookup_prefix = LINKED_MIP_PREFIX


# ── company ──────────────────────────────────────────────────────────────────
class MipCompanyFilter(admin.SimpleListFilter):
    """Company of the MIP (Mip.company). Superusers pick any company; a moderator sees only their own."""
    title = 'Company'
    parameter_name = MIP_FILTER_PARAM_COMPANY
    lookup_prefix = ''

    def lookups(self, request, model_admin):
        if request.user.is_superuser:
            return list(Company.objects.order_by('name').values_list('id', 'name'))
        profile = get_moderator_profile(request)
        if profile and profile.company_id:
            return [(profile.company_id, profile.company.name)]
        return []

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        if not value.isdigit():
            return queryset.none()
        return queryset.filter(**{f'{self.lookup_prefix}company_id': value})


class LinkedMipCompanyFilter(MipCompanyFilter):
    lookup_prefix = LINKED_MIP_PREFIX


# ── MIP status (Mip screen) ──────────────────────────────────────────────────
class MipStatusFilter(admin.ChoicesFieldListFilter):
    """
    Mip.status (Generated / Started / Paused / Completed / Closed), labelled "MIP status" so it
    isn't read as the story's status. Use as list_filter = [('status', MipStatusFilter)].
    """

    def __init__(self, field, request, params, model, model_admin, field_path):
        super().__init__(field, request, params, model, model_admin, field_path)
        self.title = 'MIP status'


# ── date range ───────────────────────────────────────────────────────────────
class DateRangeFieldListFilter(admin.FieldListFilter):
    """
    From / to date boxes for a DateField: `<field>__gte` and `<field>__lte`, both inclusive.
    Use as list_filter = [('start_date', DateRangeFieldListFilter)]. An invalid date shows
    the admin's "invalid filter" message instead of an error page.
    """
    template = ADMIN_DATE_RANGE_FILTER_TEMPLATE

    def __init__(self, field, request, params, model, model_admin, field_path):
        self.lookup_kwarg_gte = f'{field_path}__gte'
        self.lookup_kwarg_lte = f'{field_path}__lte'
        self.value_gte = (get_last_value_from_parameters(params, self.lookup_kwarg_gte) or '').strip()
        self.value_lte = (get_last_value_from_parameters(params, self.lookup_kwarg_lte) or '').strip()
        super().__init__(field, request, params, model, model_admin, field_path)

    def expected_parameters(self):
        return [self.lookup_kwarg_gte, self.lookup_kwarg_lte]

    def get_facet_counts(self, pk_attname, filtered_qs):
        return {}

    def choices(self, changelist):
        # One entry only: its query_string is the "clear both dates" link.
        yield {
            'selected': not (self.value_gte or self.value_lte),
            'query_string': changelist.get_query_string(remove=self.expected_parameters()),
            'display': self.title,
        }

    def queryset(self, request, queryset):
        lookups = {key: value for key, value in ((self.lookup_kwarg_gte, self.value_gte),
                                                 (self.lookup_kwarg_lte, self.value_lte)) if value}
        if not lookups:
            return queryset
        try:
            return queryset.filter(**lookups)
        except (ValueError, ValidationError) as e:
            raise IncorrectLookupParameters(e)
