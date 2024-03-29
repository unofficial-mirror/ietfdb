# Copyright The IETF Trust 2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.16 on 2019-01-09 09:02


from __future__ import absolute_import, print_function, unicode_literals

import json
import re

from django.db import migrations

import debug                            # pyflakes:ignore

def forward(apps, schema_editor):
    GroupFeatures = apps.get_model('group', 'GroupFeatures')
    for f in GroupFeatures.objects.all():
        for a in ['material_types', 'admin_roles', 'matman_roles', 'role_order']:
            v = getattr(f, a, None)
            if v != None:
                v = re.sub(r'[][\\"\' ]+', '', v)
                v = v.split(',')
                v = json.dumps(v)
                setattr(f, a, v)
        f.save()
# This migration changes existing data fields in an incompatible manner, and
# would not be interleavable if we hadn't added compatibility code in
# Group.features() beforehand.  With that patched in, we permit interleaving.
forward.interleavable = True            # type: ignore # https://github.com/python/mypy/issues/2087

def reverse(apps, schema_editor):
    GroupFeatures = apps.get_model('group', 'GroupFeatures')
    for f in GroupFeatures.objects.all():
        for a in ['material_types', 'admin_roles', 'matman_roles', 'role_order']:
            v = getattr(f, a, None)
            if v != None:
                v = getattr(f, a)
                v = json.loads(v)
                v = ','.join(v)
                setattr(f, a, v)
        f.save()

class Migration(migrations.Migration):

    dependencies = [
        ('group', '0004_add_group_feature_fields'),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
