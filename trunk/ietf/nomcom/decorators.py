# Copyright The IETF Trust 2012-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import functools

from django.urls import reverse
from django.http import HttpResponseRedirect
from django.utils.http import urlquote


def nomcom_private_key_required(view_func):
    def inner(request, *args, **kwargs):
        year = kwargs.get('year', None)
        if not year:
            raise Exception('View decorated with nomcom_private_key_required must receive a year argument')
        if not 'NOMCOM_PRIVATE_KEY_%s' % year in request.session:
            return HttpResponseRedirect('%s?back_to=%s' % (reverse('ietf.nomcom.views.private_key', None, args=(year, )), urlquote(request.get_full_path())))
        else:
            return view_func(request, *args, **kwargs)
    return functools.update_wrapper(inner, view_func)
