# Copyright The IETF Trust 2014-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def render_message_for_history(msg):
    """Format message for display in history.  Suppress the 'To' line for incoming responses
    """
    if msg.to.startswith('ietf-ipr+'):
        return format_html('Date: {}<br>From: {}<br>Subject: {}<br>Cc: {}<br><br>{}',
            msg.time,msg.frm,msg.subject,msg.cc,msg.body)
    else:
        return format_html('Date: {}<br>From: {}<br>To: {}<br>Subject: {}<br>Cc: {}<br><br>{}',
            msg.time,msg.frm,msg.to,msg.subject,msg.cc,msg.body)


@register.filter
def to_class_name(value):
    return value.__class__.__name__
