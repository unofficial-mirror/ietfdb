# Copyright The IETF Trust 2018-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import factory
import random

from ietf.mailinglists.models import List

class ListFactory(factory.DjangoModelFactory):
    class Meta:
        model = List

    name = factory.Sequence(lambda n: "list-name-%s" % n)
    description = factory.Faker('sentence', nb_words=10)
    advertised = factory.LazyAttribute(lambda obj: random.randint(0, 1))

    
