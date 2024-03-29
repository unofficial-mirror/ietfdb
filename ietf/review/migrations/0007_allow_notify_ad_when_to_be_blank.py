# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.17 on 2018-12-06 13:16


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0006_historicalreviewersettings'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reviewteamsettings',
            name='notify_ad_when',
            field=models.ManyToManyField(blank=True, related_name='reviewteamsettings_notify_ad_set', to='name.ReviewResultName'),
        ),
    ]
