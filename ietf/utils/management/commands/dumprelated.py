# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import io
import warnings
from collections import OrderedDict

from django.apps import apps
from django.contrib.admin.utils import NestedObjects
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.core.management.utils import parse_apps_and_model_labels
from django.db import DEFAULT_DB_ALIAS, router

import debug                            # pyflakes:ignore
debug.debug = True

class ProxyModelWarning(Warning):
    pass


class Command(BaseCommand):
    help = (
        "Output a database object and its related objects as a fixture of the given format "
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'args', metavar='app_label.ModelName', nargs=1,
            help='Specifies the app_label.ModelName for which to dump objects given by --pks',
        )
        parser.add_argument(
            '--format', default='json', dest='format',
            help='Specifies the output serialization format for fixtures.',
        )
        parser.add_argument(
            '--indent', default=None, dest='indent', type=int,
            help='Specifies the indent level to use when pretty-printing output.',
        )
        parser.add_argument(
            '--database', action='store', dest='database',
            default=DEFAULT_DB_ALIAS,
            help='Nominates a specific database to dump fixtures from. '
                 'Defaults to the "default" database.',
        )
        parser.add_argument(
            '-e', '--exclude', dest='exclude', action='append', default=[],
            help='An app_label or app_label.ModelName to exclude '
                 '(use multiple --exclude to exclude multiple apps/models).',
        )
        parser.add_argument(
            '--natural-foreign', action='store_true', dest='use_natural_foreign_keys', default=False,
            help='Use natural foreign keys if they are available.',
        )
        parser.add_argument(
            '--natural-primary', action='store_true', dest='use_natural_primary_keys', default=False,
            help='Use natural primary keys if they are available.',
        )
        parser.add_argument(
            '-o', '--output', default=None, dest='output',
            help='Specifies file to which the output is written.'
        )
        parser.add_argument(
            '--pks', dest='primary_keys', required=True,
            help="Only dump objects with given primary keys. Accepts a comma-separated "
                 "list of keys. This option only works when you specify one model.",
        )

    def handle(self, *app_labels, **options):
        format = options['format']
        indent = options['indent']
        using = options['database']
        excludes = options['exclude']
        output = options['output']
        show_traceback = options['traceback']
        use_natural_foreign_keys = options['use_natural_foreign_keys']
        use_natural_primary_keys = options['use_natural_primary_keys']
        pks = options['primary_keys']

        if pks:
            primary_keys = [pk.strip() for pk in pks.split(',')]
        else:
            primary_keys = []

        excluded_models, excluded_apps = parse_apps_and_model_labels(excludes)

        if len(app_labels) == 0:
            if primary_keys:
                raise CommandError("You can only use --pks option with one model")
            app_list = OrderedDict(
                (app_config, None) for app_config in apps.get_app_configs()
                if app_config.models_module is not None and app_config not in excluded_apps
            )
        else:
            if len(app_labels) > 1 and primary_keys:
                raise CommandError("You can only use --pks option with one model")
            app_list = OrderedDict()
            for label in app_labels:
                try:
                    app_label, model_label = label.split('.')
                    try:
                        app_config = apps.get_app_config(app_label)
                    except LookupError as e:
                        raise CommandError(str(e))
                    if app_config.models_module is None or app_config in excluded_apps:
                        continue
                    try:
                        model = app_config.get_model(model_label)
                    except LookupError:
                        raise CommandError("Unknown model: %s.%s" % (app_label, model_label))

                    app_list_value = app_list.setdefault(app_config, [])

                    # We may have previously seen a "all-models" request for
                    # this app (no model qualifier was given). In this case
                    # there is no need adding specific models to the list.
                    if app_list_value is not None:
                        if model not in app_list_value:
                            app_list_value.append(model)
                except ValueError:
                    if primary_keys:
                        raise CommandError("You can only use --pks option with one model")
                    # This is just an app - no model qualifier
                    app_label = label
                    try:
                        app_config = apps.get_app_config(app_label)
                    except LookupError as e:
                        raise CommandError(str(e))
                    if app_config.models_module is None or app_config in excluded_apps:
                        continue
                    app_list[app_config] = None

        # Check that the serialization format exists; this is a shortcut to
        # avoid collating all the objects and _then_ failing.
        if format not in serializers.get_public_serializer_formats():
            try:
                serializers.get_serializer(format)
            except serializers.SerializerDoesNotExist:
                pass

            raise CommandError("Unknown serialization format: %s" % format)

        def flatten(l):
            if isinstance(l, list):
                for el in l:
                    if isinstance(el, list):
                        for sub in flatten(el):
                            yield sub
                    else:
                        yield el
            else:
                yield l

        def get_objects(count_only=False):
            """
            Collate the objects to be serialized. If count_only is True, just
            count the number of objects to be serialized.
            """
            models = serializers.sort_dependencies(list(app_list.items()))
            for model in models:
                if model in excluded_models:
                    continue
                if model._meta.proxy and model._meta.proxy_for_model not in models:
                    warnings.warn(
                        "%s is a proxy model and won't be serialized." % model._meta.label,
                        category=ProxyModelWarning,
                    )
                if not model._meta.proxy and router.allow_migrate_model(using, model):
                    objects = model._default_manager

                    queryset = objects.using(using).order_by(model._meta.pk.name)
                    if primary_keys:
                        queryset = queryset.filter(pk__in=primary_keys)
                    if count_only:
                        yield queryset.order_by().count()
                    else:
                        for obj in queryset.iterator():
                            collector = NestedObjects(using=using)
                            collector.collect([obj,])
                            object_list = list(flatten(collector.nested()))
                            object_list.reverse()
                            for o in object_list:
                                yield o

        try:
            self.stdout.ending = None
            progress_output = None
            object_count = 0
            # If dumpdata is outputting to stdout, there is no way to display progress
            if (output and self.stdout.isatty() and options['verbosity'] > 0):
                progress_output = self.stdout
                object_count = sum(get_objects(count_only=True))
            stream = io.open(output, 'w') if output else None
            try:
                serializers.serialize(
                    format, get_objects(), indent=indent,
                    use_natural_foreign_keys=use_natural_foreign_keys,
                    use_natural_primary_keys=use_natural_primary_keys,
                    stream=stream or self.stdout, progress_output=progress_output,
                    object_count=object_count,
                )
            finally:
                if stream:
                    stream.close()
        except Exception as e:
            if show_traceback:
                raise
            raise CommandError("Unable to serialize database: %s" % e)
