# Copyright The IETF Trust 2012-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import os
import string
from docutils.core import publish_string
from docutils.utils import SystemMessage
import debug                            # pyflakes:ignore

from django.template.loaders.base import Loader as BaseLoader
from django.template.base import Template as DjangoTemplate, TemplateEncodingError # type: ignore (FIXME: remove when Django 2)
from django.template.exceptions import TemplateDoesNotExist
from django.utils.encoding import smart_text

from ietf.dbtemplate.models import DBTemplate


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RST_TEMPLATE = os.path.join(BASE_DIR, 'resources/rst.txt')


class Template(object):

    def __init__(self, template_string, origin=None, name='<Unknown Template>'):
        try:
            template_string = smart_text(template_string)
        except UnicodeDecodeError:
            raise TemplateEncodingError("Templates can only be constructed from unicode or UTF-8 strings.")
        self.template_string = string.Template(template_string)
        self.name = name

    def render(self, context):
        raise NotImplementedError


class PlainTemplate(Template):

    def render(self, context):
        context_dict = {}
        for d in context.dicts:
            context_dict.update(d)
        return self.template_string.safe_substitute(context_dict)


class RSTTemplate(PlainTemplate):

    def render(self, context):
        interpolated_string = super(RSTTemplate, self).render(context)
        try:
            return publish_string(source=interpolated_string,
                                  writer_name='html',
                                  settings_overrides={
                                      'input_encoding': 'unicode',
                                      'output_encoding': 'unicode',
                                      'embed_stylesheet': False,
                                      'xml_declaration': False,
                                      'template': RST_TEMPLATE,
                                      'halt_level': 2,
                                  })
        except SystemMessage as e:
            e.message = e.message.replace('<string>:', 'line ')
            args = list(e.args)
            args[0] = args[0].replace('<string>:', 'line ')
            e.args = tuple(args)
            raise e

class Loader(BaseLoader):
    def __init__(self, engine):
        super(Loader, self).__init__(engine)
        self.is_usable = True

    def load_template(self, template_name, template_dirs=None):
        try:
            template = DBTemplate.objects.get(path=template_name)
            if template.type.slug == 'rst':
                return (RSTTemplate(template.content), template)
            elif template.type.slug == 'django':
                return (DjangoTemplate(template.content), template)
            return (PlainTemplate(template.content), template)
        except DBTemplate.DoesNotExist:
            raise TemplateDoesNotExist(template_name)


_loader = Loader(engine='django')


def load_template_source(template_name, template_dirs=None):
    # For backwards compatibility
    import warnings
    warnings.warn(
        "'ietf.dbtemplate.template.load_template_source' is deprecated; use 'ietf.dbtemplate.template.Loader' instead.",
        PendingDeprecationWarning
    )
    return _loader.load_template_source(template_name, template_dirs)
load_template_source.is_usable = True   # type: ignore # https://github.com/python/mypy/issues/2087
