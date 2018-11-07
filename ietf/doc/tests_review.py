# -*- coding: utf-8 -*-

import datetime, os, shutil, json
import tarfile, tempfile, mailbox
import email.mime.multipart, email.mime.text, email.utils
from StringIO import StringIO
from mock import patch
from requests import Response


from django.urls import reverse as urlreverse
from django.conf import settings

from pyquery import PyQuery

import debug                            # pyflakes:ignore

import ietf.review.mailarch
from ietf.doc.factories import NewRevisionDocEventFactory, WgDraftFactory, WgRfcFactory
from ietf.doc.models import DocumentAuthor, RelatedDocument, DocEvent, ReviewRequestDocEvent
from ietf.group.factories import RoleFactory, ReviewTeamFactory
from ietf.group.models import Group
from ietf.message.models import Message
from ietf.name.models import ReviewResultName, ReviewRequestStateName, ReviewTypeName
from ietf.person.models import Email, Person
from ietf.review.factories import ReviewRequestFactory
from ietf.review.models import (ReviewRequest, ReviewerSettings,
                                ReviewWish, UnavailablePeriod, NextReviewerInTeam)
from ietf.review.utils import reviewer_rotation_list, possibly_advance_next_reviewer_for_team

from ietf.utils.test_utils import TestCase
from ietf.utils.test_data import create_person
from ietf.utils.test_utils import login_testing_unauthorized, unicontent, reload_db_objects
from ietf.utils.mail import outbox, empty_outbox
from ietf.person.factories import PersonFactory

class ReviewTests(TestCase):
    def setUp(self):
        self.review_dir = self.tempdir('review')
        self.old_document_path_pattern = settings.DOCUMENT_PATH_PATTERN
        settings.DOCUMENT_PATH_PATTERN = self.review_dir + "/{doc.type_id}/"

        self.review_subdir = os.path.join(self.review_dir, "review")
        if not os.path.exists(self.review_subdir):
            os.mkdir(self.review_subdir)
        
    def tearDown(self):
        shutil.rmtree(self.review_dir)
        settings.DOCUMENT_PATH_PATTERN = self.old_document_path_pattern

    def test_request_review(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        NewRevisionDocEventFactory(doc=doc,rev='01')
        RoleFactory(name_id='chair',person__user__username='marschairman',group=doc.group)
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        review_team3 = ReviewTeamFactory(acronym="reviewteam3", name="Review Team3", type_id="review", list_email="reviewteam3@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team3,person=rev_role.person,name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        RoleFactory(group=review_team3,person__user__username='reviewsecretary3',person__user__email='reviewsecretary3@example.com',name_id='secr')

        ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        url = urlreverse('ietf.doc.views_review.request_review', kwargs={ "name": doc.name })
        login_testing_unauthorized(self, "ad", url)

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        deadline = datetime.date.today() + datetime.timedelta(days=10)

        empty_outbox()

        # post request
        r = self.client.post(url, {
            "type": "early",
            "team": [review_team.pk,review_team3.pk],
            "deadline": deadline.isoformat(),
            "requested_rev": "01",
            "requested_by": Person.objects.get(user__username="ad").pk,
            "comment": "gZT2iiYqYLKiQHvsgWCcVLdH"
        })
        self.assertEqual(r.status_code, 302)

        qs = ReviewRequest.objects.filter(doc=doc, state="requested")
        self.assertEqual(qs.count(),2)
        self.assertEqual(set(qs.values_list('team__acronym',flat=True)),set(['reviewteam','reviewteam3']))
        for req in qs:
            self.assertEqual(req.deadline, deadline)
            self.assertEqual(req.requested_rev, "01")
            self.assertEqual(doc.latest_event().type, "requested_review")
            self.assertEqual(req.comment, "gZT2iiYqYLKiQHvsgWCcVLdH")

        self.assertEqual(len(outbox),2)
        self.assertTrue('reviewteam Early' in outbox[0]['Subject'])
        self.assertTrue('reviewsecretary@' in outbox[0]['To'])
        self.assertTrue('reviewteam3 Early' in outbox[1]['Subject'])
        self.assertTrue('reviewsecretary3@' in outbox[1]['To'])

    def test_request_review_of_rfc(self):
        doc = WgRfcFactory()

        url = urlreverse('ietf.doc.views_review.request_review', kwargs={ "name": doc.name })
        login_testing_unauthorized(self, "ad", url)

        # get should fail
        r = self.client.get(url)
        self.assertEqual(r.status_code, 403)

    def test_doc_page(self):

        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        # move the review request to a doubly-replaced document to
        # check we can fish it out
        old_doc = WgDraftFactory(name="draft-foo-mars-test")
        older_doc = WgDraftFactory(name="draft-older")
        RelatedDocument.objects.create(source=old_doc, target=older_doc.docalias_set.first(), relationship_id='replaces')
        RelatedDocument.objects.create(source=doc, target=old_doc.docalias_set.first(), relationship_id='replaces')
        review_req.doc = older_doc
        review_req.save()

        url = urlreverse('ietf.doc.views_doc.document_main', kwargs={ "name": doc.name })
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        content = unicontent(r)
        self.assertTrue("{} Review".format(review_req.type.name) in content)

    def test_review_request(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        url = urlreverse('ietf.doc.views_review.review_request', kwargs={ "name": doc.name, "request_id": review_req.pk })

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn(review_req.team.acronym, unicontent(r))
        self.assertIn(review_req.team.name, unicontent(r))

        url = urlreverse('ietf.doc.views_review.review_request_forced_login', kwargs={ "name": doc.name, "request_id": review_req.pk })
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.client.login(username='reviewer', password="reviewer+password")
        r = self.client.get(url,follow=True)
        self.assertEqual(r.status_code, 200)


    def test_close_request(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        close_url = urlreverse('ietf.doc.views_review.close_request', kwargs={ "name": doc.name, "request_id": review_req.pk })


        # follow link
        req_url = urlreverse('ietf.doc.views_review.review_request', kwargs={ "name": doc.name, "request_id": review_req.pk })
        self.client.login(username="reviewsecretary", password="reviewsecretary+password")
        r = self.client.get(req_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(close_url in unicontent(r))
        self.client.logout()

        # get close page
        login_testing_unauthorized(self, "reviewsecretary", close_url)
        r = self.client.get(close_url)
        self.assertEqual(r.status_code, 200)

        # close
        empty_outbox()
        r = self.client.post(close_url, { "close_reason": "withdrawn" })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "withdrawn")
        e = doc.latest_event()
        self.assertEqual(e.type, "closed_review_request")
        self.assertTrue("closed" in e.desc.lower())
        self.assertEqual(len(outbox), 1)
        self.assertTrue("closed" in outbox[0].get_payload(decode=True).decode("utf-8").lower())

    def test_possibly_advance_next_reviewer_for_team(self):

        team = ReviewTeamFactory(acronym="rotationteam", name="Review Team", list_email="rotationteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        doc = WgDraftFactory()

        # make a bunch of reviewers
        reviewers = [
            create_person(team, "reviewer", name="Test Reviewer{}".format(i), username="testreviewer{}".format(i))
            for i in range(5)
        ]

        self.assertEqual(reviewers, reviewer_rotation_list(team))

        def get_skip_next(person):
            settings = (ReviewerSettings.objects.filter(team=team, person=person).first()
                        or ReviewerSettings(team=team))
            return settings.skip_next

        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[0].pk, add_skip=False)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[1])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 0)

        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[1].pk, add_skip=True)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[2])
        self.assertEqual(get_skip_next(reviewers[1]), 1)
        self.assertEqual(get_skip_next(reviewers[2]), 0)

        # skip reviewer 2
        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[3].pk, add_skip=True)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[2])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 1)
        self.assertEqual(get_skip_next(reviewers[2]), 0)
        self.assertEqual(get_skip_next(reviewers[3]), 1)

        # pick reviewer 2, use up reviewer 3's skip_next
        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[2].pk, add_skip=False)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[4])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 1)
        self.assertEqual(get_skip_next(reviewers[2]), 0)
        self.assertEqual(get_skip_next(reviewers[3]), 0)
        self.assertEqual(get_skip_next(reviewers[4]), 0)

        # check wrap-around
        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[4].pk)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[0])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 1)
        self.assertEqual(get_skip_next(reviewers[2]), 0)
        self.assertEqual(get_skip_next(reviewers[3]), 0)
        self.assertEqual(get_skip_next(reviewers[4]), 0)

        # unavailable
        today = datetime.date.today()
        UnavailablePeriod.objects.create(team=team, person=reviewers[1], start_date=today, end_date=today, availability="unavailable")
        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[0].pk)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[2])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 1) # don't consume that skip while the reviewer is unavailable
        self.assertEqual(get_skip_next(reviewers[2]), 0)
        self.assertEqual(get_skip_next(reviewers[3]), 0)
        self.assertEqual(get_skip_next(reviewers[4]), 0)

        # pick unavailable anyway
        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[1].pk, add_skip=False)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[2])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 1)
        self.assertEqual(get_skip_next(reviewers[2]), 0)
        self.assertEqual(get_skip_next(reviewers[3]), 0)
        self.assertEqual(get_skip_next(reviewers[4]), 0)

        # not through min_interval so advance past reviewer[2]
        settings, _ = ReviewerSettings.objects.get_or_create(team=team, person=reviewers[2])
        settings.min_interval = 30
        settings.save()
        ReviewRequest.objects.create(team=team, doc=doc, type_id="early", state_id="accepted", deadline=today, requested_by=reviewers[0], reviewer=reviewers[2].email_set.first())
        possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id=reviewers[3].pk)
        self.assertEqual(NextReviewerInTeam.objects.get(team=team).next_reviewer, reviewers[4])
        self.assertEqual(get_skip_next(reviewers[0]), 0)
        self.assertEqual(get_skip_next(reviewers[1]), 1)
        self.assertEqual(get_skip_next(reviewers[2]), 0)
        self.assertEqual(get_skip_next(reviewers[3]), 0)
        self.assertEqual(get_skip_next(reviewers[4]), 0)

    def test_assign_reviewer(self):
        doc = WgDraftFactory(pages=2)
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',person__name=u'Some Reviewer',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='marschairman',person__name=u'WG Cháir Man',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        ReviewerSettings.objects.create(team=review_team, person=rev_role.person, min_interval=14, skip_next=0)

        # review to assign to
        review_req = ReviewRequestFactory(team=review_team,doc=doc,state_id='requested')

        # set up some reviewer-suitability factors
        reviewer_email = Email.objects.get(person__user__username="reviewer")
        DocumentAuthor.objects.create(person=reviewer_email.person, email=reviewer_email, document=doc)
        doc.rev = "10"
        doc.save_with_history([DocEvent.objects.create(doc=doc, rev=doc.rev, type="changed_document", by=Person.objects.get(user__username="secretary"), desc="Test")])

        # previous review
        ReviewRequest.objects.create(
            time=datetime.datetime.now() - datetime.timedelta(days=100),
            requested_by=Person.objects.get(name="(System)"),
            doc=doc,
            type=ReviewTypeName.objects.get(slug="early"),
            team=review_req.team,
            state=ReviewRequestStateName.objects.get(slug="completed"),
            result_id='serious-issues',
            reviewed_rev="01",
            deadline=datetime.date.today() - datetime.timedelta(days=80),
            reviewer=reviewer_email,
        )

        reviewer_settings = ReviewerSettings.objects.get(person__email=reviewer_email, team=review_req.team)
        reviewer_settings.filter_re = doc.name
        reviewer_settings.skip_next = 1
        reviewer_settings.save()

        # Need one more person in review team one so we can test incrementing skip_count without immediately decrementing it
        another_reviewer = PersonFactory.create(name = u"Extra TestReviewer") # needs to be lexically greater than the existing one
        another_reviewer.role_set.create(name_id='reviewer', email=another_reviewer.email(), group=review_req.team)

        UnavailablePeriod.objects.create(
            team=review_req.team,
            person=reviewer_email.person,
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            availability="unavailable",
        )

        ReviewWish.objects.create(person=reviewer_email.person, team=review_req.team, doc=doc)

        # pick a non-existing reviewer as next to see that we can
        # handle reviewers who have left
        NextReviewerInTeam.objects.filter(team=review_req.team).delete()
        NextReviewerInTeam.objects.create(
            team=review_req.team,
            next_reviewer=Person.objects.exclude(pk=reviewer_email.person_id).first(),
        )

        assign_url = urlreverse('ietf.doc.views_review.assign_reviewer', kwargs={ "name": doc.name, "request_id": review_req.pk })


        # follow link
        req_url = urlreverse('ietf.doc.views_review.review_request', kwargs={ "name": doc.name, "request_id": review_req.pk })
        self.client.login(username="reviewsecretary", password="reviewsecretary+password")
        r = self.client.get(req_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(assign_url in unicontent(r))
        self.client.logout()

        # get assign page
        login_testing_unauthorized(self, "reviewsecretary", assign_url)
        r = self.client.get(assign_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        reviewer_label = q("option[value=\"{}\"]".format(reviewer_email.address)).text().lower()
        self.assertIn("reviewed document before", reviewer_label)
        self.assertIn("wishes to review", reviewer_label)
        self.assertIn("is author", reviewer_label)
        self.assertIn("regexp matches", reviewer_label)
        self.assertIn("unavailable indefinitely", reviewer_label)
        self.assertIn("skip next 1", reviewer_label)
        self.assertIn("#1", reviewer_label)
        self.assertIn("1 fully completed", reviewer_label)

        # assign
        empty_outbox()
        rotation_list = reviewer_rotation_list(review_req.team)
        reviewer = Email.objects.filter(role__name="reviewer", role__group=review_req.team, person=rotation_list[0]).first()
        r = self.client.post(assign_url, { "action": "assign", "reviewer": reviewer.pk })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "requested")
        self.assertEqual(review_req.reviewer, reviewer)
        self.assertEqual(len(outbox), 1)
        self.assertTrue("assigned" in outbox[0].get_payload(decode=True).decode("utf-8"))
        self.assertEqual(NextReviewerInTeam.objects.get(team=review_req.team).next_reviewer, rotation_list[1])

        # re-assign
        empty_outbox()
        review_req.state = ReviewRequestStateName.objects.get(slug="accepted")
        review_req.save()
        reviewer = Email.objects.filter(role__name="reviewer", role__group=review_req.team, person=rotation_list[1]).first()
        r = self.client.post(assign_url, { "action": "assign", "reviewer": reviewer.pk, "add_skip": 1 })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "requested") # check that state is reset
        self.assertEqual(review_req.reviewer, reviewer)
        self.assertEqual(len(outbox), 2)
        self.assertTrue("cancelled your assignment" in outbox[0].get_payload(decode=True).decode("utf-8"))
        self.assertTrue("assigned" in outbox[1].get_payload(decode=True).decode("utf-8"))
        self.assertEqual(ReviewerSettings.objects.get(person=reviewer.person).skip_next, 1)

    def test_accept_reviewer_assignment(self):

        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='requested',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        url = urlreverse('ietf.doc.views_review.review_request', kwargs={ "name": doc.name, "request_id": review_req.pk })
        username = review_req.reviewer.person.user.username
        self.client.login(username=username, password=username + "+password")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(q("[name=action][value=accept]"))

        # accept
        r = self.client.post(url, { "action": "accept" })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "accepted")

    def test_reject_reviewer_assignment(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        reject_url = urlreverse('ietf.doc.views_review.reject_reviewer_assignment', kwargs={ "name": doc.name, "request_id": review_req.pk })


        # follow link
        req_url = urlreverse('ietf.doc.views_review.review_request', kwargs={ "name": doc.name, "request_id": review_req.pk })
        self.client.login(username="reviewsecretary", password="reviewsecretary+password")
        r = self.client.get(req_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(reject_url in unicontent(r))
        self.client.logout()

        # get reject page
        login_testing_unauthorized(self, "reviewsecretary", reject_url)
        r = self.client.get(reject_url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(unicode(review_req.reviewer.person) in unicontent(r))

        # reject
        empty_outbox()
        r = self.client.post(reject_url, { "action": "reject", "message_to_secretary": "Test message" })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "rejected")
        e = doc.latest_event()
        self.assertEqual(e.type, "closed_review_request")
        self.assertTrue("rejected" in e.desc)
        self.assertEqual(ReviewRequest.objects.filter(doc=review_req.doc, team=review_req.team, state="requested").count(), 1)
        self.assertEqual(len(outbox), 1)
        self.assertTrue("Test message" in outbox[0].get_payload(decode=True).decode("utf-8"))

    def make_test_mbox_tarball(self, review_req):
        mbox_path = os.path.join(self.review_dir, "testmbox.tar.gz")
        with tarfile.open(mbox_path, "w:gz") as tar:
            with tempfile.NamedTemporaryFile(dir=self.review_dir, suffix=".mbox") as tmp:
                mbox = mailbox.mbox(tmp.name)

                # plain text
                msg = email.mime.text.MIMEText("Hello,\n\nI have reviewed the document and did not find any problems.\n\nJohn Doe")
                msg["From"] = "John Doe <johndoe@example.com>"
                msg["To"] = review_req.team.list_email
                msg["Subject"] = "Review of {}-01".format(review_req.doc.name)
                msg["Message-ID"] = email.utils.make_msgid()
                msg["Archived-At"] = "<https://www.example.com/testmessage>"
                msg["Date"] = email.utils.formatdate()

                mbox.add(msg)

                # plain text + HTML
                msg = email.mime.multipart.MIMEMultipart('alternative')
                msg["From"] = "John Doe II <johndoe2@example.com>"
                msg["To"] = review_req.team.list_email
                msg["Subject"] = "Review of {}".format(review_req.doc.name)
                msg["Message-ID"] = email.utils.make_msgid()
                msg["Archived-At"] = "<https://www.example.com/testmessage2>"

                msg.attach(email.mime.text.MIMEText("Hi!,\r\nLooks OK!\r\n-John", "plain"))
                msg.attach(email.mime.text.MIMEText("<html><body><p>Hi!,</p><p>Looks OK!</p><p>-John</p></body></html>", "html"))
                mbox.add(msg)

                tmp.flush()

                tar.add(os.path.relpath(tmp.name))

        return mbox_path

    def test_search_mail_archive(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        # test URL construction
        query_urls = ietf.review.mailarch.construct_query_urls(review_req)
        self.assertTrue(review_req.doc.name in query_urls["query_data_url"])

        # test parsing
        mbox_path = self.make_test_mbox_tarball(review_req)

        try:
            # mock URL generator and point it to local file - for this
            # to work, the module (and not the function) must be
            # imported in the view
            real_fn = ietf.review.mailarch.construct_query_urls
            ietf.review.mailarch.construct_query_urls = lambda review_req, query=None: { "query_data_url": "file://" + os.path.abspath(mbox_path) }

            url = urlreverse('ietf.doc.views_review.search_mail_archive', kwargs={ "name": doc.name, "request_id": review_req.pk })
            login_testing_unauthorized(self, "reviewsecretary", url)

            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            messages = json.loads(r.content)["messages"]
            self.assertEqual(len(messages), 2)

            today = datetime.date.today()

            self.assertEqual(messages[0]["url"], "https://www.example.com/testmessage")
            self.assertTrue("John Doe" in messages[0]["content"])
            self.assertEqual(messages[0]["subject"], "Review of {}-01".format(review_req.doc.name))
            self.assertEqual(messages[0]["splitfrom"], ["John Doe", "johndoe@example.com"])
            self.assertEqual(messages[0]["utcdate"][0], today.isoformat())

            self.assertEqual(messages[1]["url"], "https://www.example.com/testmessage2")
            self.assertTrue("Looks OK" in messages[1]["content"])
            self.assertTrue("<html>" not in messages[1]["content"])
            self.assertEqual(messages[1]["subject"], "Review of {}".format(review_req.doc.name))
            self.assertEqual(messages[1]["splitfrom"], ["John Doe II", "johndoe2@example.com"])
            self.assertEqual(messages[1]["utcdate"][0], "")
        finally:
            ietf.review.mailarch.construct_query_urls = real_fn

    def setup_complete_review_test(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        NewRevisionDocEventFactory(doc=doc,rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))
        for r in ReviewResultName.objects.filter(slug__in=("issues", "ready")):
            review_req.team.reviewteamsettings.review_results.add(r)

        url = urlreverse('ietf.doc.views_review.complete_review', kwargs={ "name": doc.name, "request_id": review_req.pk })

        return review_req, url

    def test_complete_review_upload_content(self):
        review_req, url = self.setup_complete_review_test()

        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        # faulty post
        r = self.client.post(url, data={
            "result": "ready",
            "state": "completed",
            "reviewed_rev": "abc",
            "review_submission": "upload",
            "review_content": "",
            "review_url": "",
            "review_file": "",
        })
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(q("[name=reviewed_rev]").closest(".form-group").filter(".has-error"))
        self.assertTrue(q("[name=review_file]").closest(".form-group").filter(".has-error"))

        # complete by uploading file
        empty_outbox()

        test_file = StringIO("This is a review\nwith two lines")
        test_file.name = "unnamed"

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "upload",
            "review_content": "",
            "review_url": "",
            "review_file": test_file,
        })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "completed")
        self.assertEqual(review_req.result_id, "ready")
        self.assertEqual(review_req.reviewed_rev, review_req.doc.rev)
        self.assertTrue(review_req.team.acronym.lower() in review_req.review.name)
        self.assertTrue(review_req.doc.rev in review_req.review.name)

        with open(os.path.join(self.review_subdir, review_req.review.name + ".txt")) as f:
            self.assertEqual(f.read(), "This is a review\nwith two lines")

        self.assertEqual(len(outbox), 1)
        self.assertTrue(review_req.team.list_email in outbox[0]["To"])
        self.assertTrue(review_req.reviewer.role_set.filter(group=review_req.team,name='reviewer').first().email.address in outbox[0]["From"])
        self.assertTrue("This is a review" in outbox[0].get_payload(decode=True).decode("utf-8"))

        self.assertTrue(settings.MAILING_LIST_ARCHIVE_URL in review_req.review.external_url)

        # Check that the review has the reviewer as author
        self.assertEqual(review_req.reviewer, review_req.review.documentauthor_set.first().email)

        # Check that we have a copy of the outgoing message
        msgid = outbox[0]["Message-ID"]
        message = Message.objects.get(msgid=msgid)
        self.assertEqual(email.utils.parseaddr(outbox[0]["To"]), email.utils.parseaddr(message.to))
        self.assertEqual(email.utils.parseaddr(outbox[0]["From"]), email.utils.parseaddr(message.frm))
        self.assertEqual(outbox[0].get_payload(decode=True).decode(str(outbox[0].get_charset())), message.body)

        # check the review document page
        url = urlreverse('ietf.doc.views_doc.document_main', kwargs={ "name": review_req.review.name })
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        content = unicontent(r)
        self.assertTrue("{} Review".format(review_req.type.name) in content)
        self.assertTrue("This is a review" in content)


    def test_complete_review_enter_content(self):
        review_req, url = self.setup_complete_review_test()

        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        empty_outbox()

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is a review\nwith two lines",
            "review_url": "",
            "review_file": "",
        })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "completed")

        with open(os.path.join(self.review_subdir, review_req.review.name + ".txt")) as f:
            self.assertEqual(f.read(), "This is a review\nwith two lines")

        self.assertEqual(len(outbox), 1)
        self.assertTrue(review_req.team.list_email in outbox[0]["To"])
        self.assertTrue("This is a review" in outbox[0].get_payload(decode=True).decode("utf-8"))

        self.assertTrue(settings.MAILING_LIST_ARCHIVE_URL in review_req.review.external_url)

    def test_complete_notify_ad_because_team_settings(self):
        review_req, url = self.setup_complete_review_test()
        review_req.team.reviewteamsettings.notify_ad_when.add(ReviewResultName.objects.get(slug='issues'))
        # TODO - it's a little surprising that the factories so far didn't give this doc an ad
        review_req.doc.ad = PersonFactory()
        review_req.doc.save_with_history([DocEvent.objects.create(doc=review_req.doc, rev=review_req.doc.rev, by=review_req.reviewer.person, type='changed_document',desc='added an AD')])
        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        empty_outbox()

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="issues").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is a review\nwith two lines",
            "review_url": "",
            "review_file": "",
        })
        self.assertEqual(r.status_code, 302)

        self.assertEqual(len(outbox), 2)
        self.assertIn('Has Issues', outbox[-1]['Subject'])
        self.assertIn('settings indicated', outbox[-1].get_payload(decode=True).decode("utf-8"))

    def test_complete_notify_ad_because_checkbox(self):
        review_req, url = self.setup_complete_review_test()
        review_req.doc.ad = PersonFactory()
        review_req.doc.save_with_history([DocEvent.objects.create(doc=review_req.doc, rev=review_req.doc.rev, by=review_req.reviewer.person, type='changed_document',desc='added an AD')])
        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        empty_outbox()

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="issues").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is a review\nwith two lines",
            "review_url": "",
            "review_file": "",
            "email_ad": "1",
        })
        self.assertEqual(r.status_code, 302)

        self.assertEqual(len(outbox), 2)
        self.assertIn('Has Issues', outbox[-1]['Subject']) 
        self.assertIn('reviewer indicated', outbox[-1].get_payload(decode=True).decode("utf-8"))

    @patch('requests.get')
    def test_complete_review_link_to_mailing_list(self, mock):
        # Mock up the url response for the request.get() call to retrieve the mailing list url
        response = Response()
        response.status_code = 200
        response._content = "This is a review\nwith two lines"
        mock.return_value = response

        # Run the test
        review_req, url = self.setup_complete_review_test()

        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        empty_outbox()

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "link",
            "review_content": response.content,
            "review_url": "http://example.com/testreview/",
            "review_file": "",
        })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "completed")

        with open(os.path.join(self.review_subdir, review_req.review.name + ".txt")) as f:
            self.assertEqual(f.read(), "This is a review\nwith two lines")

        self.assertEqual(len(outbox), 0)
        self.assertTrue("http://example.com" in review_req.review.external_url)

    def test_partially_complete_review(self):
        review_req, url = self.setup_complete_review_test()

        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        # partially complete
        empty_outbox()

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="part-completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is a review with a somewhat long line spanning over 80 characters to test word wrapping\nand another line",
        })
        self.assertEqual(r.status_code, 302)



        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "part-completed")
        self.assertTrue(review_req.doc.rev in review_req.review.name)

        self.assertEqual(len(outbox), 2)
        self.assertTrue("reviewsecretary@example.com" in outbox[0]["To"])
        self.assertTrue("partially" in outbox[0]["Subject"].lower())
        self.assertTrue("new review request" in outbox[0].get_payload(decode=True).decode("utf-8"))

        self.assertTrue(review_req.team.list_email in outbox[1]["To"])
        self.assertTrue("partial review" in outbox[1]["Subject"].lower())
        body = outbox[1].get_payload(decode=True).decode("utf-8")
        self.assertTrue("This is a review" in body)
        # This review has a line longer than 80, but less than 100; it should
        # not be wrapped.

        self.assertTrue(not any( len(line) > 100 for line in body.splitlines() ))
        self.assertTrue(any( len(line) > 80 for line in body.splitlines() ))

        first_review = review_req.review
        first_reviewer = review_req.reviewer

        # complete
        review_req = ReviewRequest.objects.get(state="requested", doc=review_req.doc, team=review_req.team)
        self.assertEqual(review_req.reviewer, None)
        review_req.reviewer = first_reviewer # same reviewer, so we can test uniquification
        review_req.save()

        url = urlreverse('ietf.doc.views_review.complete_review', kwargs={ "name": review_req.doc.name, "request_id": review_req.pk })

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is another review with a really, really, really, really, really, really, really, really, really, really long line.",
        })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "completed")
        self.assertTrue(review_req.doc.rev in review_req.review.name)
        second_review = review_req.review
        self.assertTrue(first_review.name != second_review.name)
        self.assertTrue(second_review.name.endswith("-2")) # uniquified

        # This review has a line longer than 100; it should be wrapped to less
        # than 80.
        body = outbox[2].get_payload(decode=True).decode("utf-8")
        self.assertIn('really, really, really', body)
        self.assertTrue(all( len(line) <= 80 for line in body.splitlines() ))


    def test_revise_review_enter_content(self):
        review_req, url = self.setup_complete_review_test()
        review_req.state = ReviewRequestStateName.objects.get(slug="no-response")
        review_req.save()

        login_testing_unauthorized(self, review_req.reviewer.person.user.username, url)

        empty_outbox()

        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is a review\nwith two lines",
            "review_url": "",
            "review_file": "",
            "completion_date": "2012-12-24",
            "completion_time": "12:13:14",
        })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.state_id, "completed")
        event = ReviewRequestDocEvent.objects.get(type="closed_review_request", review_request=review_req)
        self.assertEqual(event.time, datetime.datetime(2012, 12, 24, 12, 13, 14))

        with open(os.path.join(self.review_subdir, review_req.review.name + ".txt")) as f:
            self.assertEqual(f.read(), "This is a review\nwith two lines")

        self.assertEqual(len(outbox), 0)

        # revise again
        empty_outbox()
        r = self.client.post(url, data={
            "result": ReviewResultName.objects.get(reviewteamsettings_review_results_set__group=review_req.team, slug="ready").pk,
            "state": ReviewRequestStateName.objects.get(slug="part-completed").pk,
            "reviewed_rev": review_req.doc.rev,
            "review_submission": "enter",
            "review_content": "This is a revised review",
            "review_url": "",
            "review_file": "",
            "completion_date": "2013-12-24",
            "completion_time": "11:11:11",
        })
        self.assertEqual(r.status_code, 302)

        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.review.rev, "01")
        event = ReviewRequestDocEvent.objects.get(type="closed_review_request", review_request=review_req)
        self.assertEqual(event.time, datetime.datetime(2013, 12, 24, 11, 11, 11))

        self.assertEqual(len(outbox), 0)
        
    def test_edit_comment(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        url = urlreverse('ietf.doc.views_review.edit_comment', kwargs={ "name": doc.name, "request_id": review_req.pk })

        login_testing_unauthorized(self, "ad", url)

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        r = self.client.post(url, data={
            "comment": "iHsnReEHXEmNPXcixsvAF9Aa",
        })
        self.assertEqual(r.status_code, 302)
        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.comment,'iHsnReEHXEmNPXcixsvAF9Aa')

    def test_edit_deadline(self):
        doc = WgDraftFactory(group__acronym='mars',rev='01')
        review_team = ReviewTeamFactory(acronym="reviewteam", name="Review Team", type_id="review", list_email="reviewteam@ietf.org", parent=Group.objects.get(acronym="farfut"))
        rev_role = RoleFactory(group=review_team,person__user__username='reviewer',person__user__email='reviewer@example.com',name_id='reviewer')
        RoleFactory(group=review_team,person__user__username='reviewsecretary',person__user__email='reviewsecretary@example.com',name_id='secr')
        review_req = ReviewRequestFactory(doc=doc,team=review_team,type_id='early',state_id='accepted',requested_by=rev_role.person,reviewer=rev_role.person.email_set.first(),deadline=datetime.datetime.now()+datetime.timedelta(days=20))

        url = urlreverse('ietf.doc.views_review.edit_deadline', kwargs={ "name": doc.name, "request_id": review_req.pk })

        login_testing_unauthorized(self, "ad", url)

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        old_deadline = review_req.deadline.date()
        new_deadline = old_deadline + datetime.timedelta(days=1)
        r = self.client.post(url, data={
            "deadline": new_deadline.isoformat(),
        })
        self.assertEqual(r.status_code, 302)
        review_req = reload_db_objects(review_req)
        self.assertEqual(review_req.deadline,new_deadline)
        self.assertTrue('Deadline changed' in outbox[-1]['Subject'])
