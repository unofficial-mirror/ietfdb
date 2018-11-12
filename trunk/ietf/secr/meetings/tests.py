import datetime
import os
import shutil
from pyquery import PyQuery
from StringIO import StringIO

import debug         # pyflakes:ignore

from django.conf import settings
from django.urls import reverse

from ietf.group.models import Group, GroupEvent
from ietf.meeting.models import Meeting, Room, TimeSlot, SchedTimeSessAssignment, Session
from ietf.meeting.test_data import make_meeting_test_data
from ietf.person.models import Person
from ietf.secr.meetings.forms import get_times
from ietf.utils.mail import outbox
from ietf.utils.test_utils import TestCase 


class SecrMeetingTestCase(TestCase):
    def setUp(self):
        self.proceedings_dir = self.tempdir('proceedings')
        self.saved_secr_proceedings_dir = settings.SECR_PROCEEDINGS_DIR
        settings.SECR_PROCEEDINGS_DIR = self.proceedings_dir
        self.saved_agenda_path = settings.AGENDA_PATH
        settings.AGENDA_PATH = self.proceedings_dir
        
        self.bluesheet_dir = self.tempdir('bluesheet')
        self.bluesheet_path = os.path.join(self.bluesheet_dir,'blue_sheet.rtf')
        self.saved_secr_blue_sheet_path = settings.SECR_BLUE_SHEET_PATH
        settings.SECR_BLUE_SHEET_PATH = self.bluesheet_path

        self.materials_dir = self.tempdir('materials')
        
    def tearDown(self):
        settings.SECR_PROCEEDINGS_DIR = self.saved_secr_proceedings_dir
        settings.AGENDA_PATH = self.saved_agenda_path
        settings.SECR_BLUE_SHEET_PATH = self.saved_secr_blue_sheet_path
        shutil.rmtree(self.proceedings_dir)
        shutil.rmtree(self.bluesheet_dir)
        shutil.rmtree(self.materials_dir)

    def test_main(self):
        "Main Test"
        meeting = make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.main')
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'meeting':meeting.number})
        url = reverse('ietf.secr.meetings.views.view', kwargs={'meeting_id':meeting.number})
        self.assertRedirects(response,url)

    def test_view(self):
        "View Test"
        meeting = make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.view', kwargs={'meeting_id':meeting.number})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        q = PyQuery(response.content)
        self.assertEqual(len(q('#id_schedule_selector option')),3)
         
    def test_add_meeting(self):
        "Add Meeting"
        meeting = make_meeting_test_data()
        number = int(meeting.number) + 1
        count = Meeting.objects.count()
        url = reverse('ietf.secr.meetings.views.add')
        post_data = dict(number=number,city='Toronto',date='2014-07-20',country='CA',
                         time_zone='America/New_York',venue_name='Hilton',
                         days=6,
                         venue_addr='100 First Ave',
                         idsubmit_cutoff_day_offset_00=13,
                         idsubmit_cutoff_day_offset_01=20,
                         idsubmit_cutoff_time_utc     =datetime.timedelta(hours=23, minutes=59, seconds=59),
                         idsubmit_cutoff_warning_days =datetime.timedelta(days=21),
                         submission_start_day_offset=90,
                         submission_cutoff_day_offset=26,
                         submission_correction_day_offset=50,
                     )
        self.client.login(username='secretary', password='secretary+password')
        response = self.client.post(url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Meeting.objects.count(),count + 1)
        new_meeting = Meeting.objects.get(number=number)
        
        # ensure new schedule is populated with specials sessions from previous meeting
        self.assertTrue(new_meeting.agenda)
        self.assertTrue(meeting.agenda.assignments.filter(timeslot__type='break').count() > 0)
        self.assertEqual(
            meeting.agenda.assignments.filter(timeslot__type='break').count(),
            new_meeting.agenda.assignments.filter(timeslot__type='break').count()
        )
        self.assertTrue(meeting.agenda.assignments.filter(timeslot__type='reg').count() > 0)
        self.assertEqual(
            meeting.agenda.assignments.filter(timeslot__type='reg').count(),
            new_meeting.agenda.assignments.filter(timeslot__type='reg').count()
        )
        self.assertEqual(new_meeting.attendees, None)

    def test_edit_meeting(self):
        "Edit Meeting"
        Meeting.objects.create(number=1,
                               type_id='ietf',
                               date=datetime.datetime(2014,7,20),
                           )
        url = reverse('ietf.secr.meetings.views.edit_meeting',kwargs={'meeting_id':1})
        post_data = dict(number='1',date='2014-07-20',city='Toronto',
                         days=7,
                         idsubmit_cutoff_day_offset_00=13,
                         idsubmit_cutoff_day_offset_01=20,
                         idsubmit_cutoff_time_utc     =datetime.timedelta(hours=23, minutes=59, seconds=59),
                         idsubmit_cutoff_warning_days =datetime.timedelta(days=21),
                         submission_start_day_offset=90,
                         submission_cutoff_day_offset=26,
                         submission_correction_day_offset=50,
                         attendees=1234,
                    )
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.post(url, post_data,follow=True)
        self.assertEqual(response.status_code, 200)
        meeting = Meeting.objects.get(number=1)
        self.assertEqual(meeting.city,'Toronto')
        self.assertEqual(meeting.attendees,1234)

    def test_blue_sheets_upload(self):
        "Test Bluesheets"
        meeting = make_meeting_test_data()
        os.makedirs(os.path.join(self.proceedings_dir,str(meeting.number),'bluesheets'))
        
        url = reverse('ietf.secr.meetings.views.blue_sheet',kwargs={'meeting_id':meeting.number})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # test upload
        group = Group.objects.filter(type='wg',state='active').first()
        file = StringIO('dummy bluesheet')
        file.name = "bluesheets-%s-%s.pdf" % (meeting.number,group.acronym)
        files = {'file':file}
        response = self.client.post(url, files)
        self.assertEqual(response.status_code, 302)
        path = os.path.join(settings.SECR_PROCEEDINGS_DIR,str(meeting.number),'bluesheets')
        self.assertEqual(len(os.listdir(path)),1)

    def test_blue_sheets_generate(self):
        meeting = make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.blue_sheet_generate',kwargs={'meeting_id':meeting.number})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(os.path.exists(self.bluesheet_path))
        
    def test_notifications(self):
        "Test Notifications"
        meeting = make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.notifications',kwargs={'meeting_id':42})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        q = PyQuery(response.content)
        self.assertEqual(q('#id_notification_list').html(),'ames, mars')
        
        # test that only changes since last notification show up
        mars_group = Group.objects.get(acronym='mars')
        ames_group = Group.objects.get(acronym='ames')
        now = datetime.datetime.now()
        then = datetime.datetime.now()+datetime.timedelta(hours=1)
        person = Person.objects.get(name="(System)")
        GroupEvent.objects.create(group=mars_group,time=now,type='sent_notification',
                                  by=person,desc='sent scheduled notification for %s' % meeting)
        ss = meeting.agenda.assignments.get(session__group=ames_group)
        ss.modified = then
        ss.save()
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        q = PyQuery(response.content)
        self.assertEqual(q('#id_notification_list').html(),'ames')
        
        # test that email goes out
        mailbox_before = len(outbox)
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(outbox), mailbox_before + 1)
        
    def test_meetings_select(self):
        make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.select',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
    
    def test_meetings_rooms(self):
        meeting = make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.rooms',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        q = PyQuery(response.content)
        self.assertEqual(len(q("#id_rooms_table tr input[type='checkbox']")),meeting.room_set.count())
        
        # test delete
        # first unschedule sessions so we can delete
        SchedTimeSessAssignment.objects.filter(schedule=meeting.agenda).delete()
        SchedTimeSessAssignment.objects.filter(schedule=meeting.unofficial_schedule).delete()
        self.client.login(username="secretary", password="secretary+password")
        post_dict = {
            'room-TOTAL_FORMS':  q('input[name="room-TOTAL_FORMS"]').val(),
            'room-INITIAL_FORMS': q('input[name="room-INITIAL_FORMS"]').val(),
        }
        for i in range(meeting.room_set.count()):
            for attr in ['meeting','id','name','capacity','DELETE']:
                key = 'room-%d-%s' % (i,attr)
                post_dict[key] = q('input[name="%s"]' % key).val()
        response = self.client.post(url, post_dict)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Room.objects.filter(meeting=meeting).count(),0)
        
    def test_meetings_times(self):
        make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.times',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {
            'day': 0,
            'time':'08:00',
            'duration':'01:00',
            'name':'Test Morning Session'
        }, follow=True)
        self.assertRedirects(response, url)
        self.assertTrue('Test Morning Session' in response.content)

    def test_meetings_times_delete(self):
        meeting = make_meeting_test_data()
        qs = TimeSlot.objects.filter(meeting=meeting,type='session')
        before = qs.count()
        expected_deletion_count = qs.filter(time=qs.first().time).count() 
        url = reverse('ietf.secr.meetings.views.times_delete',kwargs={
            'meeting_id':meeting.number,
            'schedule_name':meeting.agenda.name,
            'time':qs.first().time.strftime("%Y:%m:%d:%H:%M")
        })
        redirect_url = reverse('ietf.secr.meetings.views.times',kwargs={
            'meeting_id':meeting.number,
            'schedule_name':meeting.agenda.name
        })
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'post':'yes'})
        self.assertRedirects(response, redirect_url)
        after = TimeSlot.objects.filter(meeting=meeting,type='session').count()
        self.assertEqual(after,before - expected_deletion_count)
        
    def test_meetings_times_edit(self):
        meeting = make_meeting_test_data()
        timeslot = TimeSlot.objects.filter(meeting=meeting,type='session').first()
        url = reverse('ietf.secr.meetings.views.times_edit',kwargs={
            'meeting_id':42,
            'schedule_name':'test-agenda',
            'time':timeslot.time.strftime("%Y:%m:%d:%H:%M")
        })
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.post(url, {
            'day':'1',
            'time':'08:00',
            'duration':'09:00',
            'name':'Testing'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(TimeSlot.objects.filter(meeting=meeting,name='Testing'))
        
    def test_meetings_nonsession(self):
        make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.non_session',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
    
    def test_meetings_nonsession_add_valid(self):
        meeting = make_meeting_test_data()
        room = meeting.room_set.first()
        group = Group.objects.get(acronym='secretariat')
        url = reverse('ietf.secr.meetings.views.non_session',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.post(url, {
            'day':'1',
            'time':'08:00',
            'duration':'02:00',
            'name':'Testing',
            'short':'test',
            'type':'reg',
            'group':group.pk,
            'location': room.pk,
        })
        print response.content
        self.assertRedirects(response, url)
        session = Session.objects.filter(meeting=meeting, name='Testing').first()
        self.assertTrue(session)

        self.assertEqual(session.timeslotassignments.first().timeslot.location, room)

    def test_meetings_nonsession_add_invalid(self):
        make_meeting_test_data()
        group = Group.objects.get(acronym='secretariat')
        url = reverse('ietf.secr.meetings.views.non_session',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.post(url, {
            'day':'1',
            'time':'08:00',
            'duration':'10',
            'name':'Testing',
            'short':'test',
            'type':'reg',
            'group':group.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue('invalid format' in response.content)

    def test_meetings_nonsession_edit(self):
        meeting = make_meeting_test_data()
        session = meeting.session_set.exclude(name='').first()   # get first non-session session
        timeslot = session.official_timeslotassignment().timeslot
        url = reverse('ietf.secr.meetings.views.non_session_edit',kwargs={'meeting_id':42,'schedule_name':meeting.agenda.name,'slot_id':timeslot.pk})
        redirect_url = reverse('ietf.secr.meetings.views.non_session',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        new_time = timeslot.time + datetime.timedelta(days=1)
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {
            'name':'IETF Hackathon',
            'short':'hackathon',
            'location':timeslot.location.id,
            'group':session.group.id,
            'time':new_time.strftime('%H:%M'),
            'duration':'01:00',
            'day':'2',
            'type':'other',
        })
        self.assertRedirects(response, redirect_url)
        timeslot = session.official_timeslotassignment().timeslot
        self.assertEqual(timeslot.time,new_time)

    def test_meetings_non_session_delete(self):
        meeting = make_meeting_test_data()
        slot = meeting.agenda.assignments.filter(timeslot__type='reg').first().timeslot
        url = reverse('ietf.secr.meetings.views.non_session_delete', kwargs={'meeting_id':meeting.number,'schedule_name':meeting.agenda.name,'slot_id':slot.id})
        target = reverse('ietf.secr.meetings.views.non_session', kwargs={'meeting_id':meeting.number,'schedule_name':meeting.agenda.name})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'post':'yes'})
        self.assertRedirects(response, target)
        self.assertFalse(meeting.agenda.assignments.filter(timeslot=slot))

    def test_meetings_non_session_cancel(self):
        meeting = make_meeting_test_data()
        slot = meeting.agenda.assignments.filter(timeslot__type='reg').first().timeslot
        url = reverse('ietf.secr.meetings.views.non_session_cancel', kwargs={'meeting_id':meeting.number,'schedule_name':meeting.agenda.name,'slot_id':slot.id})
        redirect_url = reverse('ietf.secr.meetings.views.non_session', kwargs={'meeting_id':meeting.number,'schedule_name':meeting.agenda.name})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'post':'yes'})
        self.assertRedirects(response, redirect_url)
        session = slot.sessionassignments.filter(schedule=meeting.agenda).first().session
        self.assertEqual(session.status_id, 'canceled')

    def test_meetings_select_group(self):
        make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.select_group',kwargs={'meeting_id':42,'schedule_name':'test-agenda'})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        q = PyQuery(response.content)
        self.assertEqual(len(q("#id_scheduled_sessions")),1)

    def test_meetings_schedule(self):
        meeting = make_meeting_test_data()
        url = reverse('ietf.secr.meetings.views.schedule',kwargs={
            'meeting_id':meeting.number,
            'schedule_name':meeting.agenda.name,
            'acronym':'mars'
        })
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_meetings_session_edit(self):
        meeting = make_meeting_test_data()
        session = Session.objects.filter(meeting=meeting,group__acronym='mars').first()
        url = reverse('ietf.secr.meetings.views.session_edit', kwargs={'meeting_id':meeting.number,'schedule_name':meeting.agenda.name,'session_id':session.id})
        redirect_url = reverse('ietf.secr.meetings.views.select_group', kwargs={'meeting_id':meeting.number,'schedule_name':meeting.agenda.name})
        self.client.login(username="secretary", password="secretary+password")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'agenda_note':'TEST'})
        self.assertRedirects(response, redirect_url)
        session = Session.objects.get(id=session.id)
        self.assertEqual(session.agenda_note, 'TEST')
        response = self.client.post(url, {'agenda_note':'TEST','submit':'Cancel'})
        self.assertRedirects(response, redirect_url)
        session = Session.objects.get(id=session.id)
        self.assertEqual(session.status.slug, 'canceled')

    # ----------------------
    # Unit Tests
    # -----------------------
    def test_get_times(self):
        meeting = make_meeting_test_data()
        timeslot = meeting.timeslot_set.filter(type='session').first()
        day = (timeslot.time.weekday() + 1) % 7 + 1  # fix up to match django __week_day filter
        times = get_times(meeting,day)
        values = [ x[0] for x in times ]
        self.assertTrue(times)
        self.assertTrue(timeslot.time.strftime('%H%M') in values)
