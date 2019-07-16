# Copyright The IETF Trust 2013-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import os

from django.conf import settings
from django.db import models
from django.utils.encoding import python_2_unicode_compatible

from ietf.meeting.models import Meeting


class InterimManager(models.Manager):
    '''A custom manager to limit objects to type=interim'''
    def get_queryset(self):
        return super(InterimManager, self).get_queryset().filter(type='interim')
        
class InterimMeeting(Meeting):
    '''
    This class is a proxy of Meeting.  It's purpose is to provide extra methods that are 
    useful for an interim meeting, to help in templates.  Most information is derived from 
    the session associated with this meeting.  We are assuming there is only one.
    '''
    class Meta:
        proxy = True
        
    objects = InterimManager()
    
    def group(self):
        return self.session_set.all()[0].group

    def agenda(self):                   # pylint: disable=method-hidden
        session = self.session_set.all()[0]
        agendas = session.materials.exclude(states__slug='deleted').filter(type='agenda')
        if agendas:
            return agendas[0]
        else:
            return None
            
    def minutes(self):
        session = self.session_set.all()[0]
        minutes = session.materials.exclude(states__slug='deleted').filter(type='minutes')
        if minutes:
            return minutes[0]
        else:
            return None
        
    def get_proceedings_path(self, group=None):
        return os.path.join(self.get_materials_path(),'proceedings.html')
    
    def get_proceedings_url(self, group=None):
        '''
        If the proceedings file doesn't exist return empty string.  For use in templates.
        '''
        if os.path.exists(self.get_proceedings_path()):
            url = "%sproceedings/%s/proceedings.html" % (
                settings.IETF_HOST_URL,
                self.number)
            return url
        else:
            return ''

@python_2_unicode_compatible
class Registration(models.Model):
    rsn = models.AutoField(primary_key=True)
    fname = models.CharField(max_length=255)
    lname = models.CharField(max_length=255)
    company = models.CharField(max_length=255)
    country = models.CharField(max_length=2)
    
    def __str__(self):
        return "%s %s" % (self.fname, self.lname)
    class Meta:
        db_table = 'registrations'
