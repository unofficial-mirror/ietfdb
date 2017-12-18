# -*- coding: utf-8 -*-
# Generated by Django 1.10.7 on 2017-05-22 08:19
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nomcom', '0013_position_nomination_feedback_switches'),
    ]

    operations = [
        migrations.AlterField(
            model_name='position',
            name='is_open',
            field=models.BooleanField(default=False, help_text=b'Set is_open when the nomcom is working on a position. Clear it when an appointment is confirmed.', verbose_name=b'Is open'),
        ),
    ]