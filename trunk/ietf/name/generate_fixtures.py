# Copyright The IETF Trust 2011-2019, All Rights Reserved
#!/usr/bin/python

# simple script for exporting name related base data for the tests

# boiler plate
import io
import os, sys
import django

basedir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../..'))
sys.path.insert(0, basedir)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ietf.settings")

django.setup()

# script
from django.core.serializers import serialize

def output(name, seq):
    try:
        f = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures/%s.json" % name), 'w')
        f.write(serialize("json", seq, indent=1))
        f.close()
    except:
        from django.db import connection
        from pprint import pprint
        pprint(connection.queries)
        raise

# pick all name models directly out of the module
objects = []

import inspect
import ietf.name.models
for n in dir(ietf.name.models):
    symbol = getattr(ietf.name.models, n)
    if inspect.isclass(symbol) and issubclass(symbol, ietf.name.models.NameModel):
        if not symbol._meta.abstract:
            objects.extend(symbol.objects.all())


import ietf.doc.models # also pick some other name-like types while we're at it
objects += ietf.doc.models.StateType.objects.all()
objects += ietf.doc.models.State.objects.all()
objects += ietf.doc.models.BallotType.objects.all()

import ietf.group.models
objects += ietf.group.models.GroupFeatures.objects.all()

import ietf.mailtrigger.models
objects += ietf.mailtrigger.models.Recipient.objects.all()
objects += ietf.mailtrigger.models.MailTrigger.objects.all()

import ietf.utils.models
objects += ietf.utils.models.VersionInfo.objects.all()

output("names", objects)

