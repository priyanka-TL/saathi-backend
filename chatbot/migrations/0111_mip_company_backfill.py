"""
MIP company migration 2/2 (Saathi 1.5.0): backfill Mip.company for existing MIPs.

Forward (data only, chatbot_mip): sets company_id from the MIP's chat
(chat_session -> company_bot -> company) on every MIP whose company_id is NULL.
Idempotent: rows that already have a company are not touched, so running it twice is safe.
MIPs whose chat has no bot stay NULL. One UPDATE ... SET company_id = (subquery);
chatbot_mip holds one row per generated plan, so no batching is needed.
No chatbot_historicalmip rows are written (models from apps.get_model don't fire simple_history).

Reverse (noop, on purpose): the column itself is dropped by reversing 0110; clearing it
here first would only lose values that this migration can derive again.

Rollback:  python manage.py migrate chatbot 0110_mip_company
"""
from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_mip_company(apps, schema_editor):
    Mip = apps.get_model('chatbot', 'Mip')
    ChatSession = apps.get_model('chatbot', 'ChatSession')
    company_of_chat = ChatSession.objects.filter(pk=OuterRef('chat_session_id')).values('company_bot__company_id')[:1]
    Mip.objects.filter(company__isnull=True).update(company_id=Subquery(company_of_chat))


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0110_mip_company'),
    ]

    operations = [
        migrations.RunPython(backfill_mip_company, migrations.RunPython.noop),
    ]
