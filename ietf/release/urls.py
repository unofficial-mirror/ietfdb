# Copyright The IETF Trust 2015-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

from django.views.generic import TemplateView

from ietf.release import views
from ietf.utils.urls import url

urlpatterns = [
    url(r'^$',  views.release),
    url(r'^(?P<version>[0-9.]+.*)/$',  views.release),
    url(r'^about/?$',  TemplateView.as_view(template_name='release/about.html')),
    url(r'^stats/?$',  views.stats),
]

