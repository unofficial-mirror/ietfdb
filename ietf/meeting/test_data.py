# Copyright The IETF Trust 2013-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import datetime

from django.utils.text import slugify

import debug                            # pyflakes:ignore


from ietf.doc.factories import DocumentFactory
from ietf.group.models import Group
from ietf.meeting.models import (Meeting, Room, TimeSlot, Session, Schedule, SchedTimeSessAssignment,
    ResourceAssociation, SessionPresentation, UrlResource)
from ietf.meeting.helpers import create_interim_meeting
from ietf.name.models import RoomResourceName
from ietf.person.models import Person
from ietf.utils.test_data import make_test_data

def make_interim_meeting(group,date,status='sched'):
    system_person = Person.objects.get(name="(System)")
    time = datetime.datetime.combine(date, datetime.time(9))
    meeting = create_interim_meeting(group=group,date=date)
    session = Session.objects.create(meeting=meeting, group=group,
        attendees=10, requested_by=system_person, status_id=status,
        requested_duration=datetime.timedelta(minutes=20),
        remote_instructions='http://webex.com',
        scheduled=datetime.datetime.now(),type_id="session")
    slot = TimeSlot.objects.create(
        meeting=meeting,
        type_id="session",
        duration=session.requested_duration,
        time=time)
    SchedTimeSessAssignment.objects.create(
        timeslot=slot,
        session=session,
        schedule=session.meeting.agenda)
    # agenda
    name = "agenda-%s-%s-%s" % (meeting.number, group.acronym, "01")
    rev = '00'
    file = "%s-%s.txt" % (name, rev)
    doc = DocumentFactory.create(name=name, type_id='agenda', title="Agenda",
        uploaded_filename=file, group=group, rev=rev, states=[('draft','active')])
    pres = SessionPresentation.objects.create(session=session, document=doc, rev=doc.rev)
    session.sessionpresentation_set.add(pres)
    # minutes
    name = "minutes-%s-%s" % (meeting.number, time.strftime("%Y%m%d%H%M"))
    rev = '00'
    file = "%s-%s.txt" % (name, rev)
    doc = DocumentFactory.create(name=name, type_id='minutes', title="Minutes",
        uploaded_filename=file, group=group, rev=rev, states=[('draft','active')])
    pres = SessionPresentation.objects.create(session=session, document=doc, rev=doc.rev)
    session.sessionpresentation_set.add(pres)
    # slides
    title = "Slideshow"

    name = "slides-%s-sessa-%s" % (meeting.number, slugify(title))
    rev = '00'
    file = "%s-%s.txt" % (name, rev)
    doc = DocumentFactory.create(name=name, type_id='slides', title=title,
        uploaded_filename=file, group=group, rev=rev,
        states=[('slides','active'), ('reuse_policy', 'single')])
    pres = SessionPresentation.objects.create(session=session, document=doc, rev=doc.rev)
    session.sessionpresentation_set.add(pres)
    #
    return meeting

def make_meeting_test_data(meeting=None):
    if not Group.objects.filter(acronym='mars'):
        make_test_data()
    system_person = Person.objects.get(name="(System)")
    plainman = Person.objects.get(user__username="plain")
    #secretary = Person.objects.get(user__username="secretary") ## not used

    if not meeting:
        meeting = Meeting.objects.get(number="72", type="ietf")
    schedule = Schedule.objects.create(meeting=meeting, owner=plainman, name="test-agenda", visible=True, public=True)
    unofficial_schedule = Schedule.objects.create(meeting=meeting, owner=plainman, name="test-unofficial-agenda", visible=True, public=True)

    # test room
    pname = RoomResourceName.objects.create(name='projector',slug='proj')
    projector = ResourceAssociation.objects.create(name=pname,icon="notfound.png",desc="Basic projector")
    room = Room.objects.create(meeting=meeting, name="Test Room", capacity=123, functional_name="Testing Ground")
    room.session_types.add("session")
    room.resources.add(projector)
    asname = RoomResourceName.objects.get(slug='audiostream')
    UrlResource.objects.create(name=asname, room=room, url='http://ietf{number}streaming.dnsalias.net/ietf/ietf{number}1.m3u'.format(number=meeting.number))

    # other rooms
    breakfast_room = Room.objects.create(meeting=meeting, name="Breakfast Room", capacity=40)
    breakfast_room.session_types.add("lead")
    break_room = Room.objects.create(meeting=meeting, name="Break Area", capacity=500)
    break_room.session_types.add("break")
    reg_room = Room.objects.create(meeting=meeting, name="Registration Area", capacity=500)
    reg_room.session_types.add("reg")

    # slots
    session_date = meeting.date + datetime.timedelta(days=1)
    slot1 = TimeSlot.objects.create(meeting=meeting, type_id="session", location=room,
                                    duration=datetime.timedelta(minutes=30),
                                    time=datetime.datetime.combine(session_date, datetime.time(9, 30)))
    slot2 = TimeSlot.objects.create(meeting=meeting, type_id="session", location=room,
                                    duration=datetime.timedelta(minutes=30),
                                    time=datetime.datetime.combine(session_date, datetime.time(10, 30)))
    breakfast_slot = TimeSlot.objects.create(meeting=meeting, type_id="lead", location=breakfast_room,
                                    duration=datetime.timedelta(minutes=90),
                                    time=datetime.datetime.combine(session_date, datetime.time(7,0)))
    reg_slot = TimeSlot.objects.create(meeting=meeting, type_id="reg", location=reg_room,
                                       duration=datetime.timedelta(minutes=480),
                                       time=datetime.datetime.combine(session_date, datetime.time(9,0)))
    break_slot = TimeSlot.objects.create(meeting=meeting, type_id="break", location=break_room,
                                         duration=datetime.timedelta(minutes=90),
                                         time=datetime.datetime.combine(session_date, datetime.time(7,0)))
    # mars WG
    mars = Group.objects.get(acronym='mars')
    mars_session = Session.objects.create(meeting=meeting, group=mars,
                                          attendees=10, requested_by=system_person, status_id="schedw",
                                          requested_duration=datetime.timedelta(minutes=20),
                                          scheduled=datetime.datetime.now(),type_id="session")
    SchedTimeSessAssignment.objects.create(timeslot=slot1, session=mars_session, schedule=schedule)
    SchedTimeSessAssignment.objects.create(timeslot=slot2, session=mars_session, schedule=unofficial_schedule)

    # ames WG
    ames_session = Session.objects.create(meeting=meeting, group=Group.objects.get(acronym="ames"),
                                          attendees=10, requested_by=system_person, status_id="schedw",
                                          requested_duration=datetime.timedelta(minutes=20),
                                          scheduled=datetime.datetime.now(),type_id="session")
    SchedTimeSessAssignment.objects.create(timeslot=slot2, session=ames_session, schedule=schedule)
    SchedTimeSessAssignment.objects.create(timeslot=slot1, session=ames_session, schedule=unofficial_schedule)

    # IESG breakfast
    iesg_session = Session.objects.create(meeting=meeting, group=Group.objects.get(acronym="iesg"),
                                          name="IESG Breakfast", attendees=25,
                                          requested_by=system_person, status_id="schedw",
                                          requested_duration=datetime.timedelta(minutes=20),
                                          scheduled=datetime.datetime.now(),type_id="lead")
    SchedTimeSessAssignment.objects.create(timeslot=breakfast_slot, session=iesg_session, schedule=schedule)
    # No breakfast on unofficial schedule

    # Registration
    reg_session = Session.objects.create(meeting=meeting, group=Group.objects.get(acronym="secretariat"),
                                         name="Registration", attendees=250,
                                         requested_by=system_person, status_id="schedw",
                                         requested_duration=datetime.timedelta(minutes=480),
                                         scheduled=datetime.datetime.now(),type_id="reg")
    SchedTimeSessAssignment.objects.create(timeslot=reg_slot, session=reg_session, schedule=schedule)
    
    # Break
    break_session = Session.objects.create(meeting=meeting, group=Group.objects.get(acronym="secretariat"),
                                           name="Morning Break", attendees=250,
                                           requested_by=system_person, status_id="schedw",
                                           requested_duration=datetime.timedelta(minutes=30),
                                           scheduled=datetime.datetime.now(),type_id="break")
    SchedTimeSessAssignment.objects.create(timeslot=break_slot, session=break_session, schedule=schedule)

    meeting.agenda = schedule
    meeting.save()

    # Convenience for the tests
    meeting.unofficial_schedule = unofficial_schedule
    

    doc = DocumentFactory.create(name='agenda-72-mars', type_id='agenda', title="Agenda",
        uploaded_filename="agenda-72-mars.txt", group=mars, rev='00', states=[('draft','active')])
    pres = SessionPresentation.objects.create(session=mars_session,document=doc,rev=doc.rev)
    mars_session.sessionpresentation_set.add(pres) # 

    doc = DocumentFactory.create(name='minutes-72-mars', type_id='minutes', title="Minutes",
        uploaded_filename="minutes-72-mars.txt", group=mars, rev='00', states=[('minutes','active')])
    pres = SessionPresentation.objects.create(session=mars_session,document=doc,rev=doc.rev)
    mars_session.sessionpresentation_set.add(pres)

    doc = DocumentFactory.create(name='slides-72-mars-1-active', type_id='slides', title="Slideshow",
        uploaded_filename="slides-72-mars.txt", group=mars, rev='00',
        states=[('slides','active'), ('reuse_policy', 'single')])
    pres = SessionPresentation.objects.create(session=mars_session,document=doc,rev=doc.rev)
    mars_session.sessionpresentation_set.add(pres)

    doc = DocumentFactory.create(name='slides-72-mars-2-deleted', type_id='slides',
        title="Bad Slideshow", uploaded_filename="slides-72-mars-2-deleted.txt", group=mars, rev='00',
        states=[('slides','deleted'), ('reuse_policy', 'single')])
    pres = SessionPresentation.objects.create(session=mars_session,document=doc,rev=doc.rev)
    mars_session.sessionpresentation_set.add(pres)
    
    # Future Interim Meetings
    date = datetime.date.today() + datetime.timedelta(days=365)
    date2 = datetime.date.today() + datetime.timedelta(days=1000)
    ames = Group.objects.get(acronym="ames")

    make_interim_meeting(group=mars,date=date,status='sched')
    make_interim_meeting(group=mars,date=date2,status='apprw')
    make_interim_meeting(group=ames,date=date,status='canceled')
    make_interim_meeting(group=ames,date=date2,status='apprw')

    return meeting



