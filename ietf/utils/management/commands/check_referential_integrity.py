# Copyright The IETF Trust 2015-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import six

from tqdm import tqdm

import django
django.setup()

from django.apps import apps
from django.core.management.base import BaseCommand #, CommandError
from django.core.exceptions import FieldError
from django.db.models.fields.related import ForeignKey, OneToOneField

import debug                            # pyflakes:ignore

class Command(BaseCommand):
    help = "Check all models for referential integrity."

    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)
        verbose = verbosity > 1

        def check_field(field):
            try:
                foreign_model = field.related_model
            except Exception:
                debug.pprint('dir(field)')
                raise
            if verbosity > 1:
                six.print_("    %s -> %s.%s" % (field.name,foreign_model.__module__,foreign_model.__name__), end=' ')
            used = set(field.model.objects.values_list(field.name,flat=True))
            used.discard(None)
            exists = set(foreign_model.objects.values_list('pk',flat=True))
            if verbosity > 1:
                if used - exists:
                    six.print_("  ** Bad key values:",list(used - exists))
                else:
                    six.print_("  ok")
            else:
                if used - exists:
                    six.print_("\n%s.%s.%s -> %s.%s  ** Bad key values:" % (model.__module__,model.__name__,field.name,foreign_model.__module__,foreign_model.__name__),list(used - exists))

        def check_reverse_field(field):
            try:
                foreign_model = field.related_model
            except Exception:
                debug.pprint('dir(field)')
                raise
            if foreign_model == field.model:
                return
            foreign_field_name  = field.remote_field.name
            foreign_accessor_name = field.remote_field.get_accessor_name()
            if verbosity > 1:
                six.print_("    %s <- %s -> %s.%s" % (field.model.__name__, field.remote_field.through._meta.db_table, foreign_model.__module__, foreign_model.__name__), end=' ')
            try:
                used = set(foreign_model.objects.values_list(foreign_field_name, flat=True))
            except FieldError:
                try:
                    used = set(foreign_model.objects.values_list(foreign_accessor_name, flat=True))
                except FieldError:
                    six.print_("    ** Warning: could not find reverse name for %s.%s -> %s.%s" % (field.model.__module__, field.model.__name__, foreign_model.__name__, foreign_field_name), end=' ')
            used.discard(None)
            exists = set(field.model.objects.values_list('pk',flat=True))
            if verbosity > 1:
                if used - exists:
                    six.print_("  ** Bad key values:\n    ",list(used - exists))
                else:
                    six.print_("  ok")
            else:
                if used - exists:
                    six.print_("\n%s.%s <- %s -> %s.%s  ** Bad key values:\n    " % (field.model.__module__, field.model.__name__, field.remote_field.through._meta.db_table, foreign_model.__module__, foreign_model.__name__), list(used - exists))

        for conf in tqdm([ c for c in apps.get_app_configs() if c.name.startswith('ietf.')], desc='apps', disable=verbose):
            if verbosity > 1:
                six.print_("Checking", conf.name)
            for model in tqdm(list(conf.get_models()), desc='models', disable=verbose):
                if model._meta.proxy:
                    continue
                if verbosity > 1:
                    six.print_("  %s.%s" % (model.__module__,model.__name__))
                for field in [f for f in model._meta.fields if isinstance(f, (ForeignKey, OneToOneField)) ]: 
                    check_field(field)
                for field in [f for f in model._meta.many_to_many ]: 
                    check_field(field)
                    check_reverse_field(field)
