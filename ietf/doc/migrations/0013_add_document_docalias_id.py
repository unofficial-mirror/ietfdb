# Copyright The IETF Trust 2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-05-08 08:41


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('doc', '0012_add_event_type_closed_review_assignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='docalias',
            name='id',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='document',
            name='id',
            field=models.IntegerField(default=0),
        ),
    ]
