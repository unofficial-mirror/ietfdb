# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.15 on 2018-09-26 11:10


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nomcom', '0002_auto_20180918_0550'),
    ]

    operations = [
        migrations.AddField(
            model_name='nomcom',
            name='show_accepted_nominees',
            field=models.BooleanField(default=True, help_text=b'Show accepted nominees on the public nomination page', verbose_name=b'Show accepted nominees'),
        ),
    ]
