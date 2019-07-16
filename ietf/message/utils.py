# Copyright The IETF Trust 2012-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import re, datetime, email

from django.utils.encoding import force_str

from ietf.utils.mail import send_mail_text, send_mail_mime, get_payload
from ietf.message.models import Message

first_dot_on_line_re = re.compile(r'^\.', re.MULTILINE)

def infer_message(s):
    parsed = email.message_from_string(force_str(s))

    m = Message()
    m.subject = parsed.get("Subject", "")
    m.frm = parsed.get("From", "")
    m.to = parsed.get("To", "")
    m.cc = parsed.get("Cc", "")
    m.bcc = parsed.get("Bcc", "")
    m.reply_to = parsed.get("Reply-To", "")
    m.body = get_payload(parsed)

    return m

def send_scheduled_message_from_send_queue(send_queue):
    message = send_queue.message

    # for some reason, the old Perl code base substituted away . on line starts
    body = first_dot_on_line_re.sub("", message.body)
    
    extra = {}
    if message.reply_to:
        extra['Reply-To'] = message.get('reply_to')

    # announcement.content_type can contain a case-sensitive parts separator,
    # so we need to keep it as is, not lowercased, but we want a lowercased
    # version for the coming comparisons.
    content_type_lowercase = message.content_type.lower()
    if not content_type_lowercase or 'text/plain' in content_type_lowercase:
        send_mail_text(None, message.to, message.frm, message.subject,
                       body, cc=message.cc, bcc=message.bcc)
    elif 'multipart' in content_type_lowercase:
        # make body a real message so we can parse it
        body = ("MIME-Version: 1.0\r\nContent-Type: %s\r\n" % message.content_type) + body
        
        msg = email.message_from_string(force_str(body))
        send_mail_mime(None, message.to, message.frm, message.subject,
                       msg, cc=message.cc, bcc=message.bcc)

    send_queue.sent_at = datetime.datetime.now()
    send_queue.save()

