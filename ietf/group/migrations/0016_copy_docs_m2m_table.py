# Copyright The IETF Trust 2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-05-27 05:57
from __future__ import unicode_literals

import sys, time

from tqdm import tqdm

from django.db import migrations


def forward(apps, schema_editor):

    GroupMilestone              = apps.get_model('group', 'GroupMilestone')
    GroupMilestoneDocs          = apps.get_model('group', 'GroupMilestoneDocs')
    GroupMilestoneHistory       = apps.get_model('group', 'GroupMilestoneHistory')
    GroupMilestoneHistoryDocs   = apps.get_model('group', 'GroupMilestoneHistoryDocs')

    # Document id fixup ------------------------------------------------------------

    sys.stderr.write('\n')

    sys.stderr.write(' %s.%s:\n' % (GroupMilestone.__name__, 'docs'))
    for m in tqdm(GroupMilestone.objects.all()):
        m.docs.set([ d.document for d in GroupMilestoneDocs.objects.filter(groupmilestone=m) ])

    sys.stderr.write(' %s.%s:\n' % (GroupMilestoneHistory.__name__, 'docs'))
    for m in tqdm(GroupMilestoneHistory.objects.all()):
        m.docs.set([ d.document for d in GroupMilestoneHistoryDocs.objects.filter(groupmilestonehistory=m) ])


def reverse(apps, schema_editor):
    pass

def timestamp(apps, schema_editor):
    sys.stderr.write('\n %s' % time.strftime('%Y-%m-%d %H:%M:%S'))

class Migration(migrations.Migration):

    dependencies = [
        ('group', '0015_2_add_docs_m2m_table'),
    ]

    operations = [
        #migrations.RunPython(forward, reverse),
        migrations.RunPython(timestamp, timestamp),
        migrations.RunSQL(
            "INSERT INTO group_groupmilestone_docs SELECT * FROM group_groupmilestonedocs;",
            ""
        ),
        migrations.RunPython(timestamp, timestamp),
        migrations.RunSQL(
            "INSERT INTO group_groupmilestonehistory_docs SELECT * FROM group_groupmilestonehistorydocs;",
            ""
        ),
        migrations.RunPython(timestamp, timestamp),
    ]
