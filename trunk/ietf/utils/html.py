# Copyright The IETF Trust 2010-2019, All Rights Reserved
# -*- coding: utf-8 -*-
# Taken from http://code.google.com/p/soclone/source/browse/trunk/soclone/utils/html.py
"""Utilities for working with HTML."""


from __future__ import absolute_import, print_function, unicode_literals

import bleach
import copy
import lxml.etree
import lxml.html
import lxml.html.clean
import six

import debug                            # pyflakes:ignore

from django.utils.functional import keep_lazy

acceptable_tags = ('a', 'abbr', 'acronym', 'address', 'b', 'big',
    'blockquote', 'body', 'br', 'caption', 'center', 'cite', 'code', 'col',
    'colgroup', 'dd', 'del', 'dfn', 'dir', 'div', 'dl', 'dt', 'em', 'font',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'hr', 'html', 'i', 'ins', 'kbd',
    'li', 'ol', 'p', 'pre', 'q', 's', 'samp', 'small', 'span', 'strike', 'style',
    'strong', 'sub', 'sup', 'table', 'title', 'tbody', 'td', 'tfoot', 'th', 'thead',
    'tr', 'tt', 'u', 'ul', 'var')

acceptable_protocols = ['http', 'https', 'mailto', 'xmpp', ]

def unescape(text):
    """
    Returns the given text with ampersands, quotes and angle brackets decoded
    for use in URLs.

    This function undoes what django.utils.html.escape() does
    """
    return text.replace('&#39;', "'").replace('&quot;', '"').replace('&gt;', '>').replace('&lt;', '<' ).replace('&amp;', '&')

@keep_lazy(str)
def remove_tags(html, tags):
    """Returns the given HTML sanitized, and with the given tags removed."""
    allowed = set(acceptable_tags) - set([ t.lower() for t in tags ])
    return bleach.clean(html, tags=allowed)

# ----------------------------------------------------------------------
# Html fragment cleaning

bleach_cleaner = bleach.sanitizer.Cleaner(tags=acceptable_tags, protocols=acceptable_protocols, strip=True)

def sanitize_fragment(html):
    return bleach_cleaner.clean(html)

# ----------------------------------------------------------------------
# Page cleaning


class Cleaner(lxml.html.clean.Cleaner):
    charset = 'utf-8'
    # Copied from lxml 4.2.0 and modified to insert charset meta:
    def clean_html(self, html):
        result_type = type(html)
        if isinstance(html, six.string_types):
            doc = lxml.html.fromstring(html)
        else:
            doc = copy.deepcopy(html)
        self(doc)
        head = doc.find('head')
        if head != None:
            meta = lxml.etree.Element('meta', charset=self.charset)
            meta.tail = '\n'
            head.insert(0, meta)
        return lxml.html._transform_result(result_type, doc)

# We will be saving as utf-8 later, so set that in the meta tag.
lxml_cleaner = Cleaner(allow_tags=acceptable_tags, remove_unknown_tags=None, style=False, page_structure=False, charset='utf-8')

def sanitize_document(html):
    return lxml_cleaner.clean_html(html)
