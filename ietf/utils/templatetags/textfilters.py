# Copyright The IETF Trust 2016-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import bleach

from django import template
from django.template.defaultfilters import stringfilter
from django.utils.html import escape
from django.utils.safestring import mark_safe

import debug                            # pyflakes:ignore

from ietf.utils.text import xslugify as _xslugify, texescape

register = template.Library()

@register.filter(is_safe=True)
@stringfilter
def xslugify(value):
    """
    Converts to ASCII. Converts spaces to hyphens. Removes characters that
    aren't alphanumerics, underscores, slashes, or hyphens. Converts to
    lowercase.  Also strips leading and trailing whitespace.
    """
    return _xslugify(value)

@register.filter(is_safe=True)
@stringfilter
def format(format, values):
    if not isinstance(values, dict):
        tmp = {}
        for f in values._meta.fields:
            tmp[f.name] = getattr(values, f.name)
        values = tmp
    return format.format(**values)

# ----------------------------------------------------------------------

# from django.utils.safestring import mark_safe
# class TeXEscapeNode(template.Node):
#     """TeX escaping, rather than html escaping.
# 
#     Mostly, this tag is _not_ the right thing to use in a template that produces TeX
#     markup, as it will escape all the markup characters, which is not what you want.
#     Use the '|texescape' filter instead on text fragments where escaping is needed
#     """
#     def __init__(self, nodelist):
#         self.nodelist = nodelist
# 
#     def render(self, context):
#         saved_autoescape = context.autoescape
#         context.autoescape = False
#         text = self.nodelist.render(context)
#         text = texescape(text)
#         context.autoescape = saved_autoescape
#         return mark_safe(text)
# 
# @register.tag('texescape')
# def do_texescape(parser, token):
#     args = token.contents.split()
#     if len(args) != 1:
#         raise TemplateSyntaxError("'texescape' tag takes no arguments.")
#     nodelist = parser.parse(('endtexescape',))
#     parser.delete_first_token()
#     return TeXEscapeNode(nodelist)
    
@register.filter('texescape')
@stringfilter
def texescape_filter(value):
    "A TeX escape filter"
    return texescape(value)
    
@register.filter
@stringfilter
def linkify(value):
    text = mark_safe(bleach.linkify(escape(value)))
    return text
