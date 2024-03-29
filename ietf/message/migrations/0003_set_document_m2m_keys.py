# Copyright The IETF Trust 2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-05-21 14:27


from __future__ import absolute_import, print_function, unicode_literals

import sys

from tqdm import tqdm

from django.db import migrations


def forward(apps, schema_editor):

    Document                    = apps.get_model('doc','Document')
    Message                     = apps.get_model('message', 'Message')
    MessageDocs                 = apps.get_model('message', 'MessageDocs')


    # Document id fixup ------------------------------------------------------------

    objs = Document.objects.in_bulk()
    nameid = { o.name: o.id for id, o in objs.items() }

    sys.stderr.write('\n')

    sys.stderr.write(' %s.%s:\n' % (Message.__name__, 'related_docs'))
    count = 0
    for m in tqdm(Message.objects.all()):
        for d in m.related_docs.all():
            count += 1;
            MessageDocs.objects.get_or_create(message=m, document_id=nameid[d.name])
    sys.stderr.write(' %s MessageDocs objects created\n' % (count, ))

def reverse(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('message', '0002_add_message_docs2_m2m'),
        ('doc', '0014_set_document_docalias_id'),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
