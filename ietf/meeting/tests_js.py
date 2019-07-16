# Copyright The IETF Trust 2014-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import sys
import time
from pyquery import PyQuery 
from unittest import skipIf

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse as urlreverse
#from django.test.utils import override_settings

import debug                            # pyflakes:ignore

from ietf.doc.factories import DocumentFactory
from ietf.group import colors
from ietf.meeting.factories import SessionFactory
from ietf.meeting.test_data import make_meeting_test_data
from ietf.meeting.models import SchedTimeSessAssignment
from ietf.utils.test_runner import set_coverage_checking
from ietf.utils.pipe import pipe
from ietf import settings

skip_selenium = getattr(settings,'SKIP_SELENIUM',None)
skip_message  = ""
if skip_selenium:
    skip_message = "settings.SKIP_SELENIUM = %s" % skip_selenium
else:
    try:
        from selenium import webdriver
        from selenium.webdriver.common.action_chains import ActionChains
    except ImportError as e:
        skip_selenium = True
        skip_message = "Skipping selenium tests: %s" % e
    code, out, err = pipe('phantomjs -v')
    if not code == 0:
        skip_selenium = True
        skip_message = "Skipping selenium tests: 'phantomjs' executable not found."
    if skip_selenium:
        sys.stderr.write("     "+skip_message+'\n')

def condition_data():
        make_meeting_test_data()
        colors.fg_group_colors['FARFUT'] = 'blue'
        colors.bg_group_colors['FARFUT'] = 'white'

   
@skipIf(skip_selenium, skip_message)
class ScheduleEditTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        set_coverage_checking(False)
        super(ScheduleEditTests, cls).setUpClass()        

    @classmethod
    def tearDownClass(cls):
        super(ScheduleEditTests, cls).tearDownClass()
        set_coverage_checking(True)

    def setUp(self):
        self.driver = webdriver.PhantomJS(port=0, service_log_path=settings.TEST_GHOSTDRIVER_LOG_PATH)
        self.driver.set_window_size(1024,768)
        condition_data()

    def tearDown(self):
        self.driver.close()

    def debugSnapshot(self,filename='debug_this.png'):
        self.driver.execute_script("document.body.bgColor = 'white';")
        self.driver.save_screenshot(filename)

    def absreverse(self,*args,**kwargs):
        return '%s%s'%(self.live_server_url,urlreverse(*args,**kwargs))

    def login(self):
        url = '%s%s'%(self.live_server_url, urlreverse('ietf.ietfauth.views.login'))
        self.driver.get(url)
        self.driver.find_element_by_name('username').send_keys('plain')
        self.driver.find_element_by_name('password').send_keys('plain+password')
        self.driver.find_element_by_xpath('//button[@type="submit"]').click()
    
    def testUnschedule(self):
        
        self.assertEqual(SchedTimeSessAssignment.objects.filter(session__meeting__number=72,session__group__acronym='mars',schedule__name='test-agenda').count(),1)

        self.login()
        url = self.absreverse('ietf.meeting.views.edit_agenda',kwargs=dict(num='72',name='test-agenda',owner='plain@example.com'))
        self.driver.get(url)

        q = PyQuery(self.driver.page_source)
        self.assertEqual(len(q('#sortable-list #session_1')),0)

        element = self.driver.find_element_by_id('session_1')
        target  = self.driver.find_element_by_id('sortable-list')
        ActionChains(self.driver).drag_and_drop(element,target).perform()

        q = PyQuery(self.driver.page_source)
        self.assertTrue(len(q('#sortable-list #session_1'))>0)

        time.sleep(0.1) # The API that modifies the database runs async
        self.assertEqual(SchedTimeSessAssignment.objects.filter(session__meeting__number=72,session__group__acronym='mars',schedule__name='test-agenda').count(),0)

@skipIf(skip_selenium, skip_message)
class SlideReorderTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        set_coverage_checking(False)
        super(SlideReorderTests, cls).setUpClass()        

    @classmethod
    def tearDownClass(cls):
        super(SlideReorderTests, cls).tearDownClass()
        set_coverage_checking(True)

    def setUp(self):
        self.driver = webdriver.PhantomJS(port=0, service_log_path=settings.TEST_GHOSTDRIVER_LOG_PATH)
        self.driver.set_window_size(1024,768)
        self.session = SessionFactory(meeting__type_id='ietf')
        self.session.sessionpresentation_set.create(document=DocumentFactory(type_id='slides',name='one'),order=1)
        self.session.sessionpresentation_set.create(document=DocumentFactory(type_id='slides',name='two'),order=2)
        self.session.sessionpresentation_set.create(document=DocumentFactory(type_id='slides',name='three'),order=3)

    def tearDown(self):
        self.driver.close()

    def absreverse(self,*args,**kwargs):
        return '%s%s'%(self.live_server_url,urlreverse(*args,**kwargs))

    def secr_login(self):
        url = '%s%s'%(self.live_server_url, urlreverse('ietf.ietfauth.views.login'))
        self.driver.get(url)
        self.driver.find_element_by_name('username').send_keys('secretary')
        self.driver.find_element_by_name('password').send_keys('secretary+password')
        self.driver.find_element_by_xpath('//button[@type="submit"]').click()

    #@override_settings(DEBUG=True)
    def testReorderSlides(self):
        return
        url = self.absreverse('ietf.meeting.views.session_details',
                  kwargs=dict(
                      num=self.session.meeting.number,
                      acronym = self.session.group.acronym,))
        self.secr_login()
        self.driver.get(url)        
        #debug.show('unicode(self.driver.page_source)')
        second = self.driver.find_element_by_css_selector('#slides tr:nth-child(2)')
        third = self.driver.find_element_by_css_selector('#slides tr:nth-child(3)')
        ActionChains(self.driver).drag_and_drop(second,third).perform()

        time.sleep(0.1) # The API that modifies the database runs async
        names=self.session.sessionpresentation_set.values_list('document__name',flat=True) 
        self.assertEqual(list(names),['one','three','two'])

# The following are useful debugging tools

# If you add this to a LiveServerTestCase and run just this test, you can browse
# to the test server with the data loaded by setUp() to debug why, for instance,
# a particular view isn't giving you what you expect
#    def testJustSitThere(self):
#        time.sleep(10000)

# The LiveServerTestCase server runs in a mode like production - it hides crashes with the
# user-friendly message about mail being sent to the maintainers, and eats that mail.
# Loading the page that crashed with just a TestCase will at least let you see the
# traceback.
#
#from ietf.utils.test_utils import TestCase
#class LookAtCrashTest(TestCase):
#    def setUp(self):
#        condition_data()
#
#    def testOpenSchedule(self):
#        url = urlreverse('ietf.meeting.views.edit_agenda', kwargs=dict(num='72',name='test-agenda'))
#        r = self.client.get(url)
