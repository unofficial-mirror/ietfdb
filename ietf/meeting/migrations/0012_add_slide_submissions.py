# Copyright The IETF Trust 2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.20 on 2019-03-23 07:41


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations, models
import django.db.models.deletion
import ietf.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('person', '0009_auto_20190118_0725'),
        ('meeting', '0011_auto_20190114_0550'),
    ]

    operations = [
        migrations.CreateModel(
            name='SlideSubmission',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('filename', models.CharField(max_length=255)),
                ('apply_to_all', models.BooleanField(default=False)),
                ('session', ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='meeting.Session')),
                ('submitter', ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='person.Person')),
            ],
        ),
    ]
