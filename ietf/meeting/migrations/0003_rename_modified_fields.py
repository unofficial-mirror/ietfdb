# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.10 on 2018-03-02 14:33


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('meeting', '0002_auto_20180225_1207'),
    ]

    operations = [
        migrations.RenameField(
            model_name='floorplan',
            old_name='time',
            new_name='modified',
        ),
        migrations.RenameField(
            model_name='room',
            old_name='time',
            new_name='modified',
        ),
        migrations.AlterField(
            model_name='floorplan',
            name='modified',
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name='room',
            name='modified',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
