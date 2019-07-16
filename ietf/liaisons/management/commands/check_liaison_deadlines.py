# Copyright The IETF Trust 2010-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import datetime

from django.core.management.base import BaseCommand

from ietf.liaisons.models import LiaisonStatement
from ietf.liaisons.mails import possibly_send_deadline_reminder


class Command(BaseCommand):
    help = ("Check liaison deadlines and send a reminder if we are close to a deadline")

    def handle(self, *args, **options):
        today = datetime.date.today()
        cutoff = today - datetime.timedelta(14)

        msgs = []
        for l in LiaisonStatement.objects.filter(deadline__gte=cutoff).exclude(tags__slug='taken'):
            r = possibly_send_deadline_reminder(l)
            if r:
                msgs.append('Liaison %05s#: Deadline reminder sent!' % l.pk)

        return '\n'.join(msgs)