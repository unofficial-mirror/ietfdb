# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Generated by Django 1.11.10 on 2018-02-20 10:52


from __future__ import absolute_import, print_function, unicode_literals

from django.db import migrations, models
import django.db.models.deletion
import ietf.utils.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('message', '0001_initial'),
        ('name', '0001_initial'),
        ('person', '0001_initial'),
        ('doc', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='IprDisclosureBase',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('compliant', models.BooleanField(default=True, verbose_name='Complies to RFC3979')),
                ('holder_legal_name', models.CharField(max_length=255)),
                ('notes', models.TextField(blank=True, verbose_name='Additional notes')),
                ('other_designations', models.CharField(blank=True, max_length=255, verbose_name='Designations for other contributions')),
                ('submitter_name', models.CharField(blank=True, max_length=255)),
                ('submitter_email', models.EmailField(blank=True, max_length=254)),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('title', models.CharField(blank=True, max_length=255)),
            ],
        ),
        migrations.CreateModel(
            name='IprDocRel',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sections', models.TextField(blank=True)),
                ('revisions', models.CharField(blank=True, max_length=16)),
            ],
        ),
        migrations.CreateModel(
            name='IprEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('desc', models.TextField()),
                ('response_due', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-time', '-id'],
            },
        ),
        migrations.CreateModel(
            name='RelatedIpr',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('relationship', ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='name.DocRelationshipName')),
            ],
        ),
        migrations.CreateModel(
            name='GenericIprDisclosure',
            fields=[
                ('iprdisclosurebase_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='ipr.IprDisclosureBase')),
                ('holder_contact_name', models.CharField(max_length=255)),
                ('holder_contact_email', models.EmailField(max_length=254)),
                ('holder_contact_info', models.TextField(blank=True, help_text='Address, phone, etc.')),
                ('statement', models.TextField()),
            ],
            bases=('ipr.iprdisclosurebase',),
        ),
        migrations.CreateModel(
            name='HolderIprDisclosure',
            fields=[
                ('iprdisclosurebase_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='ipr.IprDisclosureBase')),
                ('ietfer_name', models.CharField(blank=True, max_length=255)),
                ('ietfer_contact_email', models.EmailField(blank=True, max_length=254)),
                ('ietfer_contact_info', models.TextField(blank=True)),
                ('patent_info', models.TextField()),
                ('has_patent_pending', models.BooleanField(default=False)),
                ('holder_contact_email', models.EmailField(max_length=254)),
                ('holder_contact_name', models.CharField(max_length=255)),
                ('holder_contact_info', models.TextField(blank=True, help_text='Address, phone, etc.')),
                ('licensing_comments', models.TextField(blank=True)),
                ('submitter_claims_all_terms_disclosed', models.BooleanField(default=False)),
                ('licensing', ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='name.IprLicenseTypeName')),
            ],
            bases=('ipr.iprdisclosurebase',),
        ),
        migrations.CreateModel(
            name='LegacyMigrationIprEvent',
            fields=[
                ('iprevent_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='ipr.IprEvent')),
            ],
            bases=('ipr.iprevent',),
        ),
        migrations.CreateModel(
            name='NonDocSpecificIprDisclosure',
            fields=[
                ('iprdisclosurebase_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='ipr.IprDisclosureBase')),
                ('holder_contact_name', models.CharField(max_length=255)),
                ('holder_contact_email', models.EmailField(max_length=254)),
                ('holder_contact_info', models.TextField(blank=True, help_text='Address, phone, etc.')),
                ('patent_info', models.TextField()),
                ('has_patent_pending', models.BooleanField(default=False)),
                ('statement', models.TextField()),
            ],
            bases=('ipr.iprdisclosurebase',),
        ),
        migrations.CreateModel(
            name='ThirdPartyIprDisclosure',
            fields=[
                ('iprdisclosurebase_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='ipr.IprDisclosureBase')),
                ('ietfer_name', models.CharField(max_length=255)),
                ('ietfer_contact_email', models.EmailField(max_length=254)),
                ('ietfer_contact_info', models.TextField(blank=True, help_text='Address, phone, etc.')),
                ('patent_info', models.TextField()),
                ('has_patent_pending', models.BooleanField(default=False)),
            ],
            bases=('ipr.iprdisclosurebase',),
        ),
        migrations.AddField(
            model_name='relatedipr',
            name='source',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='relatedipr_source_set', to='ipr.IprDisclosureBase'),
        ),
        migrations.AddField(
            model_name='relatedipr',
            name='target',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='relatedipr_target_set', to='ipr.IprDisclosureBase'),
        ),
        migrations.AddField(
            model_name='iprevent',
            name='by',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='person.Person'),
        ),
        migrations.AddField(
            model_name='iprevent',
            name='disclosure',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='ipr.IprDisclosureBase'),
        ),
        migrations.AddField(
            model_name='iprevent',
            name='in_reply_to',
            field=ietf.utils.models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='irtoevents', to='message.Message'),
        ),
        migrations.AddField(
            model_name='iprevent',
            name='message',
            field=ietf.utils.models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='msgevents', to='message.Message'),
        ),
        migrations.AddField(
            model_name='iprevent',
            name='type',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='name.IprEventTypeName'),
        ),
        migrations.AddField(
            model_name='iprdocrel',
            name='disclosure',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='ipr.IprDisclosureBase'),
        ),
        migrations.AddField(
            model_name='iprdocrel',
            name='document',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='doc.DocAlias'),
        ),
        migrations.AddField(
            model_name='iprdisclosurebase',
            name='by',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='person.Person'),
        ),
        migrations.AddField(
            model_name='iprdisclosurebase',
            name='docs',
            field=models.ManyToManyField(through='ipr.IprDocRel', to='doc.DocAlias'),
        ),
        migrations.AddField(
            model_name='iprdisclosurebase',
            name='rel',
            field=models.ManyToManyField(through='ipr.RelatedIpr', to='ipr.IprDisclosureBase'),
        ),
        migrations.AddField(
            model_name='iprdisclosurebase',
            name='state',
            field=ietf.utils.models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='name.IprDisclosureStateName'),
        ),
    ]
