# Copyright The IETF Trust 2012-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import io
import sys
import os

from django.core.management.base import BaseCommand
from django.conf import settings
from django.template.loader import render_to_string

from ietf.doc.models import Document

def write(fn, new):
    try:
        f = io.open(fn)
        old = f.read().decode('utf-8')
        f.close
    except IOError:
        old = ""
    if old.strip() != new.strip():
        sys.stdout.write(os.path.basename(fn)+'\n')
        f = io.open(fn, "wb")
        f.write(new.encode('utf-8'))
        f.close()

class Command(BaseCommand):
    help = ('Generate draft bibxml files, for xml2rfc references')

    def handle(self, *args, **options):
        documents = Document.objects.filter(type__slug='draft')
        bibxmldir = os.path.join(settings.BIBXML_BASE_PATH, 'bibxml3')
        if not os.path.exists(bibxmldir):
            os.makedirs(bibxmldir)
        for doc in documents:
            ref_text = render_to_string('doc/bibxml.xml', {'doc': doc, 'doc_bibtype':'I-D'})
            ref_file_name = os.path.join(bibxmldir, 'reference.I-D.%s.xml' % (doc.name, ))
            ref_rev_file_name = os.path.join(bibxmldir, 'reference.I-D.%s-%s.xml' % (doc.name, doc.rev))
            write(ref_file_name, ref_text)
            write(ref_rev_file_name, ref_text)
                