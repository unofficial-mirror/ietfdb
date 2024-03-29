# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.16 on 2018-10-08 06:02


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('nomcom', '0004_set_show_accepted_nominees_false_on_existing_nomcoms'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='nominee',
            options={'ordering': ['-nomcom__group__acronym', 'person__name'], 'verbose_name_plural': 'Nominees'},
        ),
    ]
