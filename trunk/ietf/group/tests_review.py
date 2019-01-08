import datetime
import debug      # pyflakes:ignore

from pyquery import PyQuery

from django.urls import reverse as urlreverse

from ietf.utils.test_utils import login_testing_unauthorized, TestCase, unicontent, reload_db_objects
from ietf.doc.models import TelechatDocEvent
from ietf.group.models import Role
from ietf.iesg.models import TelechatDate
from ietf.person.models import Email, Person
from ietf.review.models import ReviewRequest, ReviewerSettings, UnavailablePeriod, ReviewSecretarySettings
from ietf.review.utils import (
    suggested_review_requests_for_team,
    review_requests_needing_reviewer_reminder, email_reviewer_reminder,
    review_requests_needing_secretary_reminder, email_secretary_reminder,
    reviewer_rotation_list,
)
from ietf.name.models import ReviewTypeName, ReviewResultName, ReviewRequestStateName
import ietf.group.views
from ietf.utils.mail import outbox, empty_outbox
from ietf.dbtemplate.factories import DBTemplateFactory
from ietf.person.factories import PersonFactory, EmailFactory
from ietf.doc.factories import DocumentFactory
from ietf.group.factories import RoleFactory, ReviewTeamFactory
from ietf.review.factories import ReviewRequestFactory, ReviewerSettingsFactory

class ReviewTests(TestCase):
    def test_review_requests(self):
        review_req = ReviewRequestFactory(reviewer=EmailFactory())
        group = review_req.team

        for url in [urlreverse(ietf.group.views.review_requests, kwargs={ 'acronym': group.acronym }),
                    urlreverse(ietf.group.views.review_requests, kwargs={ 'acronym': group.acronym , 'group_type': group.type_id})]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertIn(review_req.doc.name, unicontent(r))
            self.assertIn(unicode(review_req.reviewer.person), unicontent(r))

        url = urlreverse(ietf.group.views.review_requests, kwargs={ 'acronym': group.acronym })

        # close request, listed under closed
        review_req.state = ReviewRequestStateName.objects.get(slug="completed")
        review_req.result = ReviewResultName.objects.get(slug="ready")
        review_req.save()

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(review_req.doc.name in unicontent(r))

    def test_suggested_review_requests(self):
        review_req = ReviewRequestFactory()
        doc = review_req.doc
        team = review_req.team

        # put on telechat
        e = TelechatDocEvent.objects.create(
            type="scheduled_for_telechat",
            by=Person.objects.get(name="(System)"),
            doc=doc,
            rev=doc.rev,
            telechat_date=TelechatDate.objects.all().first().date,
        )
        doc.rev = "10"
        doc.save_with_history([e])

        prev_rev = "{:02}".format(int(doc.rev) - 1)

        # blocked by existing request
        review_req.requested_rev = ""
        review_req.save()

        self.assertEqual(len(suggested_review_requests_for_team(team)), 0)

        # ... but not to previous version
        review_req.requested_rev = prev_rev
        review_req.save()
        suggestions = suggested_review_requests_for_team(team)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].doc, doc)
        self.assertEqual(suggestions[0].team, team)

        # blocked by non-versioned refusal
        review_req.requested_rev = ""
        review_req.state = ReviewRequestStateName.objects.get(slug="no-review-document")
        review_req.save()

        self.assertEqual(list(suggested_review_requests_for_team(team)), [])

        # blocked by versioned refusal
        review_req.reviewed_rev = doc.rev
        review_req.state = ReviewRequestStateName.objects.get(slug="no-review-document")
        review_req.save()

        self.assertEqual(list(suggested_review_requests_for_team(team)), [])

        # blocked by completion
        review_req.state = ReviewRequestStateName.objects.get(slug="completed")
        review_req.save()

        self.assertEqual(list(suggested_review_requests_for_team(team)), [])

        # ... but not to previous version
        review_req.reviewed_rev = prev_rev
        review_req.state = ReviewRequestStateName.objects.get(slug="completed")
        review_req.save()

        self.assertEqual(len(suggested_review_requests_for_team(team)), 1)

    def test_reviewer_overview(self):
        team = ReviewTeamFactory()
        reviewer = RoleFactory(name_id='reviewer',group=team,person__user__username='reviewer').person
        ReviewerSettingsFactory(person=reviewer,team=team)
        review_req1 = ReviewRequestFactory(state_id='completed',team=team,reviewer=reviewer.email())
        PersonFactory(user__username='plain')

        ReviewRequest.objects.create(
            doc=review_req1.doc,
            team=review_req1.team,
            type_id="early",
            deadline=datetime.date.today() + datetime.timedelta(days=30),
            state_id="accepted",
            reviewer=review_req1.reviewer,
            requested_by=Person.objects.get(user__username="reviewer"),
        )

        UnavailablePeriod.objects.create(
            team=review_req1.team,
            person=reviewer,
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            availability="unavailable",
        )

        settings = ReviewerSettings.objects.get(person=reviewer,team=review_req1.team)
        settings.skip_next = 1
        settings.save()

        group = review_req1.team

        # get
        for url in [urlreverse(ietf.group.views.reviewer_overview, kwargs={ 'acronym': group.acronym }),
                    urlreverse(ietf.group.views.reviewer_overview, kwargs={ 'acronym': group.acronym, 'group_type': group.type_id })]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertIn(unicode(reviewer), unicontent(r))
            self.assertIn(review_req1.doc.name, unicontent(r))
            # without a login, reason for being unavailable should not be seen
            self.assertNotIn("Availability", unicontent(r))

        url = urlreverse(ietf.group.views.reviewer_overview, kwargs={ 'acronym': group.acronym })
        self.client.login(username="plain", password="plain+password")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        # not on review team, should not see reason for being unavailable
        self.assertNotIn("Availability", unicontent(r))

        self.client.login(username="reviewer", password="reviewer+password")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        # review team members can see reason for being unavailable
        self.assertIn("Availability", unicontent(r))

        self.client.login(username="secretary", password="secretary+password")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        # secretariat can see reason for being unavailable
        self.assertIn("Availability", unicontent(r))

    def test_manage_review_requests(self):
        group = ReviewTeamFactory()
        reviewer = RoleFactory(name_id='reviewer',group=group,person__user__username='reviewer').person
        marsperson = RoleFactory(name_id='reviewer',group=group,person=PersonFactory(name=u"Mars Anders Chairman",user__username='marschairman')).person
        review_req1 = ReviewRequestFactory(doc__pages=2,doc__shepherd=marsperson.email(),reviewer=reviewer.email(),team=group)
        RoleFactory(name_id='chair',group=review_req1.doc.group,person=marsperson)
        doc = review_req1.doc

        url = urlreverse(ietf.group.views.manage_review_requests, kwargs={ 'acronym': group.acronym, "assignment_status": "assigned" })

        login_testing_unauthorized(self, "secretary", url)

        assigned_url = urlreverse(ietf.group.views.manage_review_requests, kwargs={ 'acronym': group.acronym, 'group_type': group.type_id, "assignment_status": "assigned" })
        unassigned_url = urlreverse(ietf.group.views.manage_review_requests, kwargs={ 'acronym': group.acronym, 'group_type': group.type_id, "assignment_status": "unassigned" })

        review_req2 = ReviewRequest.objects.create(
            doc=review_req1.doc,
            team=review_req1.team,
            type_id="early",
            deadline=datetime.date.today() + datetime.timedelta(days=30),
            state_id="accepted",
            reviewer=review_req1.reviewer,
            requested_by=Person.objects.get(user__username="reviewer"),
        )

        review_req3 = ReviewRequest.objects.create(
            doc=review_req1.doc,
            team=review_req1.team,
            type_id="early",
            deadline=datetime.date.today() + datetime.timedelta(days=30),
            state_id="requested",
            requested_by=Person.objects.get(user__username="reviewer"),
        )

        # previous reviews
        ReviewRequest.objects.create(
            time=datetime.datetime.now() - datetime.timedelta(days=100),
            requested_by=Person.objects.get(name="(System)"),
            doc=doc,
            type=ReviewTypeName.objects.get(slug="early"),
            team=review_req1.team,
            state=ReviewRequestStateName.objects.get(slug="completed"),
            result=ReviewResultName.objects.get(slug="ready-nits"),
            reviewed_rev="01",
            deadline=datetime.date.today() - datetime.timedelta(days=80),
            reviewer=review_req1.reviewer,
        )

        ReviewRequest.objects.create(
            time=datetime.datetime.now() - datetime.timedelta(days=100),
            requested_by=Person.objects.get(name="(System)"),
            doc=doc,
            type=ReviewTypeName.objects.get(slug="early"),
            team=review_req1.team,
            state=ReviewRequestStateName.objects.get(slug="completed"),
            result=ReviewResultName.objects.get(slug="ready"),
            reviewed_rev="01",
            deadline=datetime.date.today() - datetime.timedelta(days=80),
            reviewer=review_req1.reviewer,
        )

        # Need one more person in review team one so we can test incrementing skip_count without immediately decrementing it
        another_reviewer = PersonFactory.create(name = u"Extra TestReviewer") # needs to be lexically greater than the exsting one
        another_reviewer.role_set.create(name_id='reviewer', email=another_reviewer.email(), group=review_req1.team)
        
        # get
        r = self.client.get(assigned_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(review_req1.doc.name in unicontent(r))

        # can't save assigned: conflict
        new_reviewer = Email.objects.get(role__name="reviewer", role__group=group, person__user__username="marschairman")
        # provoke conflict by posting bogus data
        r = self.client.post(assigned_url, {
            "reviewrequest": [str(review_req1.pk), str(review_req2.pk), str(123456)],

            # close
            "r{}-existing_reviewer".format(review_req1.pk): "123456",
            "r{}-action".format(review_req1.pk): "close",
            "r{}-close".format(review_req1.pk): "no-response",

            # assign
            "r{}-existing_reviewer".format(review_req2.pk): "123456",
            "r{}-action".format(review_req2.pk): "assign",
            "r{}-reviewer".format(review_req2.pk): new_reviewer.pk,

            "action": "save-continue",
        })
        self.assertEqual(r.status_code, 200)
        content = unicontent(r).lower()
        self.assertTrue("1 request closed" in content)
        self.assertTrue("2 requests changed assignment" in content)

        # can't save unassigned: conflict
        r = self.client.post(unassigned_url, {
            "reviewrequest": [str(123456)],
            "action": "save-continue",
        })
        self.assertEqual(r.status_code, 200)
        content = unicontent(r).lower()
        self.assertTrue("1 request opened" in content)

        # close and reassign assigned
        new_reviewer = Email.objects.get(role__name="reviewer", role__group=group, person__user__username="marschairman")
        r = self.client.post(assigned_url, {
            "reviewrequest": [str(review_req1.pk), str(review_req2.pk)],

            # close
            "r{}-existing_reviewer".format(review_req1.pk): review_req1.reviewer_id or "",
            "r{}-action".format(review_req1.pk): "close",
            "r{}-close".format(review_req1.pk): "no-response",

            # assign
            "r{}-existing_reviewer".format(review_req2.pk): review_req2.reviewer_id or "",
            "r{}-action".format(review_req2.pk): "assign",
            "r{}-reviewer".format(review_req2.pk): new_reviewer.pk,
            "r{}-add_skip".format(review_req2.pk) : 1,

            "action": "save",
        })
        self.assertEqual(r.status_code, 302)

        # no change on unassigned
        r = self.client.post(unassigned_url, {
            "reviewrequest": [str(review_req3.pk)],

            # no change
            "r{}-existing_reviewer".format(review_req3.pk): review_req3.reviewer_id or "",
            "r{}-action".format(review_req3.pk): "",
            "r{}-close".format(review_req3.pk): "no-response",
            "r{}-reviewer".format(review_req3.pk): "",
            
            "action": "save",
        })
        self.assertEqual(r.status_code, 302)

        review_req1, review_req2, review_req3 = reload_db_objects(review_req1, review_req2, review_req3)
        self.assertEqual(review_req1.state_id, "no-response")
        self.assertEqual(review_req2.state_id, "requested")
        self.assertEqual(review_req2.reviewer, new_reviewer)
        settings = ReviewerSettings.objects.filter(team=review_req2.team, person=new_reviewer.person).first()
        self.assertEqual(settings.skip_next,1)
        self.assertEqual(review_req3.state_id, "requested")

        r = self.client.post(assigned_url, {
            "reviewrequest": [str(review_req2.pk)],
            "r{}-existing_reviewer".format(review_req2.pk): review_req2.reviewer_id or "",
            "r{}-action".format(review_req2.pk): "assign",
            "r{}-reviewer".format(review_req2.pk): "",
            "r{}-add_skip".format(review_req2.pk) : 0,
            "action": "save",
        })
        self.assertEqual(r.status_code, 302)
        review_req2 = reload_db_objects(review_req2)
        self.assertEqual(review_req2.state_id, "requested")
        self.assertEqual(review_req2.reviewer, None)

    def test_email_open_review_assignments(self):
        review_req1 = ReviewRequestFactory(reviewer=EmailFactory(person__user__username='marschairman'))
        DBTemplateFactory.create(path='/group/defaults/email/open_assignments.txt',
                                 type_id='django',
                                 content = """
                                     {% autoescape off %}
                                     Reviewer               Deadline   Draft
                                     {% for r in review_requests %}{{ r.reviewer.person.plain_name|ljust:"22" }} {{ r.deadline|date:"Y-m-d" }} {{ r.doc_id }}-{% if r.requested_rev %}{{ r.requested_rev }}{% else %}{{ r.doc.rev }}{% endif %}
                                     {% endfor %}
                                     {% if rotation_list %}Next in the reviewer rotation:

                                     {% for p in rotation_list %}  {{ p }}
                                     {% endfor %}{% endif %}
                                     {% endautoescape %}
                                 """)

        group = review_req1.team

        url = urlreverse(ietf.group.views.email_open_review_assignments, kwargs={ 'acronym': group.acronym })

        login_testing_unauthorized(self, "secretary", url)

        url = urlreverse(ietf.group.views.email_open_review_assignments, kwargs={ 'acronym': group.acronym, 'group_type': group.type_id })

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        generated_text = q("[name=body]").text()
        self.assertTrue(review_req1.doc.name in generated_text)
        self.assertTrue(unicode(Person.objects.get(user__username="marschairman")) in generated_text)

        empty_outbox()
        r = self.client.post(url, {
            "to": 'toaddr@bogus.test',
            "cc": 'ccaddr@bogus.test',
            "reply_to": 'replytoaddr@bogus.test',
            "frm" : 'fromaddr@bogus.test',
            "subject": "Test subject",
            "body": "Test body",
            "action": "email",
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(outbox), 1)
        self.assertTrue('toaddr' in outbox[0]["To"])
        self.assertTrue('ccaddr' in outbox[0]["Cc"])
        self.assertTrue('replytoaddr' in outbox[0]["Reply-To"])
        self.assertTrue('fromaddr' in outbox[0]["From"])
        self.assertEqual(outbox[0]["subject"], "Test subject")
        self.assertTrue("Test body" in outbox[0].get_payload(decode=True).decode("utf-8"))

    def test_change_reviewer_settings(self):
        reviewer = ReviewerSettingsFactory(person__user__username='reviewer',expertise='Some expertise').person
        review_req = ReviewRequestFactory(reviewer=reviewer.email())
        RoleFactory(name_id='reviewer',group=review_req.team,person=review_req.reviewer.person)
        RoleFactory(name_id='secr',group=review_req.team)

        reviewer = review_req.reviewer.person

        url = urlreverse(ietf.group.views.change_reviewer_settings, kwargs={
            "acronym": review_req.team.acronym,
            "reviewer_email": review_req.reviewer_id,
        })

        login_testing_unauthorized(self, reviewer.user.username, url)

        url = urlreverse(ietf.group.views.change_reviewer_settings, kwargs={
            "group_type": review_req.team.type_id,
            "acronym": review_req.team.acronym,
            "reviewer_email": review_req.reviewer_id,
        })

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        # set settings
        empty_outbox()
        r = self.client.post(url, {
            "action": "change_settings",
            "min_interval": "7",
            "filter_re": "test-[regexp]",
            "remind_days_before_deadline": "6",
            "expertise": "Some expertise",
        })
        self.assertEqual(r.status_code, 302)
        settings = ReviewerSettings.objects.get(person=reviewer, team=review_req.team)
        self.assertEqual(settings.min_interval, 7)
        self.assertEqual(settings.filter_re, "test-[regexp]")
        self.assertEqual(settings.skip_next, 0)
        self.assertEqual(settings.remind_days_before_deadline, 6)
        self.assertEqual(settings.expertise, "Some expertise")
        self.assertEqual(len(outbox), 1)
        self.assertTrue("reviewer availability" in outbox[0]["subject"].lower())
        msg_content = outbox[0].get_payload(decode=True).decode("utf-8").lower()
        self.assertTrue("frequency changed", msg_content)
        self.assertTrue("skip next", msg_content)

        # Normal reviewer should not be able to change skip_next
        r = self.client.post(url, {
            "action": "change_settings",
            "min_interval": "7",
            "filter_re": "test-[regexp]",
            "remind_days_before_deadline": "6",
            "skip_next" : "2",
        })
        self.assertEqual(r.status_code, 302)
        settings = ReviewerSettings.objects.get(person=reviewer, team=review_req.team)
        self.assertEqual(settings.skip_next, 0)

        # add unavailable period
        start_date = datetime.date.today() + datetime.timedelta(days=10)
        empty_outbox()
        r = self.client.post(url, {
            "action": "add_period",
            'start_date': start_date.isoformat(),
            'end_date': "",
            'availability': "unavailable",
	    'reason': "Whimsy",
        })
        self.assertEqual(r.status_code, 302)
        period = UnavailablePeriod.objects.get(person=reviewer, team=review_req.team, start_date=start_date)
        self.assertEqual(period.end_date, None)
        self.assertEqual(period.availability, "unavailable")
        self.assertEqual(len(outbox), 1)
        msg_content = outbox[0].get_payload(decode=True).decode("utf-8").lower()
        self.assertTrue(start_date.isoformat(), msg_content)
        self.assertTrue("indefinite", msg_content)
	self.assertEqual(period.reason, "Whimsy")

        # end unavailable period
        empty_outbox()
        end_date = start_date + datetime.timedelta(days=10)
        r = self.client.post(url, {
            "action": "end_period",
            'period_id': period.pk,
            'end_date': end_date.isoformat(),
        })
        self.assertEqual(r.status_code, 302)
        period = reload_db_objects(period)
        self.assertEqual(period.end_date, end_date)
        self.assertEqual(len(outbox), 1)
        msg_content = outbox[0].get_payload(decode=True).decode("utf-8").lower()
        self.assertTrue(start_date.isoformat(), msg_content)
        self.assertTrue("indefinite", msg_content)

        # delete unavailable period
        empty_outbox()
        r = self.client.post(url, {
            "action": "delete_period",
            'period_id': period.pk,
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(UnavailablePeriod.objects.filter(person=reviewer, team=review_req.team, start_date=start_date).count(), 0)
        self.assertEqual(len(outbox), 1)
        msg_content = outbox[0].get_payload(decode=True).decode("utf-8").lower()
        self.assertTrue(start_date.isoformat(), msg_content)
        self.assertTrue(end_date.isoformat(), msg_content)

        # secretaries and the secretariat should be able to change skip_next
        for username in ["secretary","reviewsecretary"]:
            skip_next_val = {'secretary':'3','reviewsecretary':'4'}[username]
            self.client.login(username=username,password=username+"+password")
            r = self.client.post(url, {
                "action": "change_settings",
                "min_interval": "7",
                "filter_re": "test-[regexp]",
                "remind_days_before_deadline": "6",
                "skip_next" : skip_next_val,
            })
            self.assertEqual(r.status_code, 302)
            settings = ReviewerSettings.objects.get(person=reviewer, team=review_req.team)
            self.assertEqual(settings.skip_next, int(skip_next_val))
            

    def test_change_review_secretary_settings(self):
        review_req = ReviewRequestFactory()
        secretary = RoleFactory(name_id='secr',group=review_req.team,person__user__username='reviewsecretary').person

        url = urlreverse(ietf.group.views.change_review_secretary_settings, kwargs={
            "acronym": review_req.team.acronym,
        })

        login_testing_unauthorized(self, secretary.user.username, url)

        url = urlreverse(ietf.group.views.change_review_secretary_settings, kwargs={
            "group_type": review_req.team.type_id,
            "acronym": review_req.team.acronym,
        })

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        # set settings
        r = self.client.post(url, {
            "remind_days_before_deadline": "6"
        })
        self.assertEqual(r.status_code, 302)
        settings = ReviewSecretarySettings.objects.get(person=secretary, team=review_req.team)
        self.assertEqual(settings.remind_days_before_deadline, 6)

    def test_review_reminders(self):
        review_req = ReviewRequestFactory()
        reviewer =  RoleFactory(name_id='reviewer',group=review_req.team,person__user__username='reviewer').person
        RoleFactory(name_id='secr',group=review_req.team,person__user__username='reviewsecretary')
        ReviewerSettingsFactory(team = review_req.team, person = reviewer)

        remind_days = 6

        reviewer_settings = ReviewerSettings.objects.get(team=review_req.team, person=reviewer)
        reviewer_settings.remind_days_before_deadline = remind_days
        reviewer_settings.save()

        secretary = Person.objects.get(user__username="reviewsecretary")
        secretary_role = Role.objects.get(group=review_req.team, name="secr", person=secretary)

        secretary_settings = ReviewSecretarySettings(team=review_req.team, person=secretary)
        secretary_settings.remind_days_before_deadline = remind_days
        secretary_settings.save()

        today = datetime.date.today()

        review_req.reviewer = reviewer.email_set.first()
        review_req.deadline = today + datetime.timedelta(days=remind_days)
        review_req.save()

        # reviewer
        needing_reminders = review_requests_needing_reviewer_reminder(today - datetime.timedelta(days=1))
        self.assertEqual(list(needing_reminders), [])

        needing_reminders = review_requests_needing_reviewer_reminder(today)
        self.assertEqual(list(needing_reminders), [review_req])

        needing_reminders = review_requests_needing_reviewer_reminder(today + datetime.timedelta(days=1))
        self.assertEqual(list(needing_reminders), [])

        # secretary
        needing_reminders = review_requests_needing_secretary_reminder(today - datetime.timedelta(days=1))
        self.assertEqual(list(needing_reminders), [])

        needing_reminders = review_requests_needing_secretary_reminder(today)
        self.assertEqual(list(needing_reminders), [(review_req, secretary_role)])

        needing_reminders = review_requests_needing_secretary_reminder(today + datetime.timedelta(days=1))
        self.assertEqual(list(needing_reminders), [])

        # email reviewer
        empty_outbox()
        email_reviewer_reminder(review_req)
        self.assertEqual(len(outbox), 1)
        self.assertTrue(review_req.doc_id in outbox[0].get_payload(decode=True).decode("utf-8"))

        # email secretary
        empty_outbox()
        email_secretary_reminder(review_req, secretary_role)
        self.assertEqual(len(outbox), 1)
        self.assertTrue(review_req.doc_id in outbox[0].get_payload(decode=True).decode("utf-8"))



class BulkAssignmentTests(TestCase):

    def test_rotation_queue_update(self):
        group = ReviewTeamFactory.create()
        empty_outbox()
        reviewers = [RoleFactory.create(group=group,name_id='reviewer') for i in range(6)] # pyflakes:ignore
        secretary = RoleFactory.create(group=group,name_id='secr')
        docs = [DocumentFactory.create(type_id='draft',group=None) for i in range(4)]
        requests = [ReviewRequestFactory(team=group,doc=docs[i]) for i in range(4)]
        rot_list = reviewer_rotation_list(group)

        expected_ending_head_of_rotation = rot_list[3]
    
        unassigned_url = urlreverse(ietf.group.views.manage_review_requests, kwargs={ 'acronym': group.acronym, 'group_type': group.type_id, "assignment_status": "unassigned" })

        postdict = {}
        postdict['reviewrequest'] = [r.id for r in requests]
        # assignments that affect the first 3 reviewers in queue
        for i in range(3):
            postdict['r{}-existing_reviewer'.format(requests[i].pk)] = ''
            postdict['r{}-action'.format(requests[i].pk)] = 'assign'
            postdict['r{}-reviewer'.format(requests[i].pk)] = rot_list[i].email_address()
        # and one out of order assignment
        postdict['r{}-existing_reviewer'.format(requests[3].pk)] = ''
        postdict['r{}-action'.format(requests[3].pk)] = 'assign'
        postdict['r{}-reviewer'.format(requests[3].pk)] = rot_list[5].email_address()
        postdict['action'] = 'save'
        self.client.login(username=secretary.person.user.username,password=secretary.person.user.username+'+password')
        r = self.client.post(unassigned_url, postdict)
        self.assertEqual(r.status_code,302)
        self.assertEqual(expected_ending_head_of_rotation,reviewer_rotation_list(group)[0])
        self.assertMailboxContains(outbox, subject='Last Call assignment', text='Requested by', count=4)
        
