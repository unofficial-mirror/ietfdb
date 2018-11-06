# -*- coding: utf-8 -*-
import datetime
from pyquery import PyQuery

import debug                            # pyflakes:ignore

from django.urls import reverse as urlreverse

from ietf.doc.models import ( Document, State, DocEvent,
    BallotPositionDocEvent, LastCallDocEvent, WriteupDocEvent, TelechatDocEvent )
from ietf.doc.factories import DocumentFactory, IndividualDraftFactory, IndividualRfcFactory, WgDraftFactory
from ietf.doc.utils import create_ballot_if_not_open
from ietf.group.models import Group, Role
from ietf.group.factories import GroupFactory, RoleFactory
from ietf.ipr.factories import HolderIprDisclosureFactory
from ietf.name.models import BallotPositionName
from ietf.iesg.models import TelechatDate
from ietf.person.models import Person, PersonalApiKey
from ietf.person.factories import PersonFactory
from ietf.utils.test_utils import TestCase, unicontent, login_testing_unauthorized
from ietf.utils.mail import outbox, empty_outbox
from ietf.utils.text import unwrap


class EditPositionTests(TestCase):
    def test_edit_position(self):
        ad = Person.objects.get(user__username="ad")
        draft = IndividualDraftFactory(ad=ad)
        ballot = create_ballot_if_not_open(None, draft, ad, 'approve')
        url = urlreverse('ietf.doc.views_ballot.edit_position', kwargs=dict(name=draft.name,
                                                          ballot_id=ballot.pk))
        login_testing_unauthorized(self, "ad", url)

        ad = Person.objects.get(name="Areað Irector")
        
        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q('form input[name=position]')) > 0)
        self.assertEqual(len(q('form textarea[name=comment]')), 1)

        # vote
        events_before = draft.docevent_set.count()
        
        r = self.client.post(url, dict(position="discuss",
                                       discuss=" This is a discussion test. \n ",
                                       comment=" This is a test. \n "))
        self.assertEqual(r.status_code, 302)

        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "discuss")
        self.assertTrue(" This is a discussion test." in pos.discuss)
        self.assertTrue(pos.discuss_time != None)
        self.assertTrue(" This is a test." in pos.comment)
        self.assertTrue(pos.comment_time != None)
        self.assertTrue("New position" in pos.desc)
        self.assertEqual(draft.docevent_set.count(), events_before + 3)

        # recast vote
        events_before = draft.docevent_set.count()
        r = self.client.post(url, dict(position="noobj"))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(name=draft.name)
        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "noobj")
        self.assertEqual(draft.docevent_set.count(), events_before + 1)
        self.assertTrue("Position for" in pos.desc)
        
        # clear vote
        events_before = draft.docevent_set.count()
        r = self.client.post(url, dict(position="norecord"))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(name=draft.name)
        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "norecord")
        self.assertEqual(draft.docevent_set.count(), events_before + 1)
        self.assertTrue("Position for" in pos.desc)

        # change comment
        events_before = draft.docevent_set.count()
        r = self.client.post(url, dict(position="norecord", comment="New comment."))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(name=draft.name)
        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "norecord")
        self.assertEqual(draft.docevent_set.count(), events_before + 2)
        self.assertTrue("Ballot comment text updated" in pos.desc)
        
    def test_api_set_position(self):
        ad = Person.objects.get(name="Areað Irector")
        draft = WgDraftFactory(ad=ad)
        url = urlreverse('ietf.doc.views_ballot.api_set_position')
        create_ballot_if_not_open(None, draft, ad, 'approve')
        ad.user.last_login = datetime.datetime.now()
        ad.user.save()
        apikey = PersonalApiKey.objects.create(endpoint=url, person=ad)

        # vote
        events_before = draft.docevent_set.count()
        mailbox_before = len(outbox)

        r = self.client.post(url, dict(
                                        apikey=apikey.hash(),
                                        doc=draft.name,
                                        position="discuss",
                                        discuss=" This is a discussion test. \n ",
                                        comment=" This is a test. \n ")
            )
        self.assertEqual(r.content, "Done")
        self.assertEqual(r.status_code, 200)

        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "discuss")
        self.assertTrue(" This is a discussion test." in pos.discuss)
        self.assertTrue(pos.discuss_time != None)
        self.assertTrue(" This is a test." in pos.comment)
        self.assertTrue(pos.comment_time != None)
        self.assertTrue("New position" in pos.desc)
        self.assertEqual(draft.docevent_set.count(), events_before + 3)
        self.assertEqual(len(outbox), mailbox_before + 1)

        # recast vote
        events_before = draft.docevent_set.count()
        mailbox_before = len(outbox)
        r = self.client.post(url, dict(apikey=apikey.hash(), doc=draft.name, position="noobj"))
        self.assertEqual(r.status_code, 200)

        draft = Document.objects.get(name=draft.name)
        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "noobj")
        self.assertEqual(draft.docevent_set.count(), events_before + 1)
        self.assertTrue("Position for" in pos.desc)
        self.assertEqual(len(outbox), mailbox_before + 1)
        m = outbox[-1]
        self.assertIn('No Objection', m['Subject'])
        self.assertIn('iesg@', m['To'])
        self.assertIn(draft.name, m['Cc'])
        self.assertIn(draft.group.acronym+'-chairs@', m['Cc'])

        # clear vote
        events_before = draft.docevent_set.count()
        mailbox_before = len(outbox)
        r = self.client.post(url, dict(apikey=apikey.hash(), doc=draft.name, position="norecord"))
        self.assertEqual(r.status_code, 200)

        draft = Document.objects.get(name=draft.name)
        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "norecord")
        self.assertEqual(draft.docevent_set.count(), events_before + 1)
        self.assertTrue("Position for" in pos.desc)
        self.assertEqual(len(outbox), mailbox_before + 1)
        m = outbox[-1]
        self.assertIn('No Record', m['Subject'])

        # change comment
        events_before = draft.docevent_set.count()
        mailbox_before = len(outbox)
        r = self.client.post(url, dict(apikey=apikey.hash(), doc=draft.name, position="norecord", comment="New comment."))
        self.assertEqual(r.status_code, 200)

        draft = Document.objects.get(name=draft.name)
        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "norecord")
        self.assertEqual(draft.docevent_set.count(), events_before + 2)
        self.assertTrue("Ballot comment text updated" in pos.desc)
        self.assertEqual(len(outbox), mailbox_before + 1)
        m = outbox[-1]
        self.assertIn('COMMENT', m['Subject'])
        self.assertIn('New comment', m.get_payload())


    def test_edit_position_as_secretary(self):
        draft = IndividualDraftFactory()
        ad = Person.objects.get(user__username="ad")
        ballot = create_ballot_if_not_open(None, draft, ad, 'approve')
        url = urlreverse('ietf.doc.views_ballot.edit_position', kwargs=dict(name=draft.name, ballot_id=ballot.pk))
        ad = Person.objects.get(name="Areað Irector")
        url += "?ad=%s" % ad.pk
        login_testing_unauthorized(self, "secretary", url)

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q('form input[name=position]')) > 0)

        # vote on behalf of AD
        # events_before = draft.docevent_set.count()
        r = self.client.post(url, dict(position="discuss", discuss="Test discuss text"))
        self.assertEqual(r.status_code, 302)

        pos = draft.latest_event(BallotPositionDocEvent, ad=ad)
        self.assertEqual(pos.pos.slug, "discuss")
        self.assertEqual(pos.discuss, "Test discuss text")
        self.assertTrue("New position" in pos.desc)
        self.assertTrue("by Sec" in pos.desc)

    def test_cannot_edit_position_as_pre_ad(self):
        draft = IndividualDraftFactory()
        ad = Person.objects.get(user__username="ad")
        ballot = create_ballot_if_not_open(None, draft, ad, 'approve')
        url = urlreverse('ietf.doc.views_ballot.edit_position', kwargs=dict(name=draft.name, ballot_id=ballot.pk))
        
        # transform to pre-ad
        ad_role = Role.objects.filter(name="ad")[0]
        ad_role.name_id = "pre-ad"
        ad_role.save()

        # we can see
        login_testing_unauthorized(self, ad_role.person.user.username, url)

        # but not touch
        r = self.client.post(url, dict(position="discuss", discuss="Test discuss text"))
        self.assertEqual(r.status_code, 403)
        
    def test_send_ballot_comment(self):
        ad = Person.objects.get(user__username="ad")
        draft = WgDraftFactory(ad=ad,group__acronym='mars')
        draft.notify = "somebody@example.com"
        draft.save_with_history([DocEvent.objects.create(doc=draft, rev=draft.rev, type="changed_document", by=Person.objects.get(user__username="secretary"), desc="Test")])

        ballot = create_ballot_if_not_open(None, draft, ad, 'approve')

        BallotPositionDocEvent.objects.create(
            doc=draft, rev=draft.rev, type="changed_ballot_position",
            by=ad, ad=ad, ballot=ballot, pos=BallotPositionName.objects.get(slug="discuss"),
            discuss="This draft seems to be lacking a clearer title?",
            discuss_time=datetime.datetime.now(),
            comment="Test!",
            comment_time=datetime.datetime.now())
        
        url = urlreverse('ietf.doc.views_ballot.send_ballot_comment', kwargs=dict(name=draft.name,
                                                                ballot_id=ballot.pk))
        login_testing_unauthorized(self, "ad", url)

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q('form input[name="extra_cc"]')) > 0)

        # send
        mailbox_before = len(outbox)

        r = self.client.post(url, dict(extra_cc="test298347@example.com", cc_choices=['doc_notify','doc_group_chairs']))
        self.assertEqual(r.status_code, 302)

        self.assertEqual(len(outbox), mailbox_before + 1)
        m = outbox[-1]
        self.assertTrue("COMMENT" in m['Subject'])
        self.assertTrue("DISCUSS" in m['Subject'])
        self.assertTrue(draft.name in m['Subject'])
        self.assertTrue("clearer title" in str(m))
        self.assertTrue("Test!" in str(m))
        self.assertTrue("iesg@" in m['To'])
        # cc_choice doc_group_chairs
        self.assertTrue("mars-chairs@" in m['Cc'])
        # cc_choice doc_notify
        self.assertTrue("somebody@example.com" in m['Cc'])
        # cc_choice doc_group_email_list was not selected
        self.assertFalse(draft.group.list_email in m['Cc'])
        # extra-cc    
        self.assertTrue("test298347@example.com" in m['Cc'])

        r = self.client.post(url, dict(cc=""))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(outbox), mailbox_before + 2)
        m = outbox[-1]
        self.assertTrue("iesg@" in m['To'])
        self.assertFalse(m['Cc'] and draft.group.list_email in m['Cc'])


class BallotWriteupsTests(TestCase):
    def test_edit_last_call_text(self):
        draft = IndividualDraftFactory(ad=Person.objects.get(user__username='ad'),states=[('draft','active'),('draft-iesg','ad-eval')])
        url = urlreverse('ietf.doc.views_ballot.lastcalltext', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('textarea[name=last_call_text]')), 1)
        self.assertTrue(q('[type=submit]:contains("Save")'))
        # we're Secretariat, so we got The Link
        self.assertEqual(len(q('a:contains("Issue last call")')), 1)
        
        # subject error
        r = self.client.post(url, dict(
                last_call_text="Subject: test\r\nhello\r\n\r\n",
                save_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q('form .has-error')) > 0)

        # save
        r = self.client.post(url, dict(
                last_call_text="This is a simple test.",
                save_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        self.assertTrue("This is a simple test" in draft.latest_event(WriteupDocEvent, type="changed_last_call_text").text)

        # test regenerate
        r = self.client.post(url, dict(
                last_call_text="This is a simple test.",
                regenerate_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        text = q("[name=last_call_text]").text()
        self.assertTrue("Subject: Last Call" in text)


    def test_request_last_call(self):
        ad = Person.objects.get(user__username="ad")
        draft = IndividualDraftFactory(ad=ad,states=[('draft-iesg','iesg-eva')])
        url = urlreverse('ietf.doc.views_ballot.lastcalltext', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # give us an announcement to send
        r = self.client.post(url, dict(regenerate_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        text = q("[name=last_call_text]").text()

        mailbox_before = len(outbox)

        # send
        r = self.client.post(url, dict(
                last_call_text=text,
                send_last_call_request="1"))
        draft = Document.objects.get(name=draft.name)
        self.assertEqual(draft.get_state_slug("draft-iesg"), "lc-req")
        self.assertEqual(len(outbox), mailbox_before + 1)
        self.assertTrue("Last Call" in outbox[-1]['Subject'])
        self.assertTrue(draft.name in outbox[-1]['Subject'])
        self.assertTrue('iesg-secretary@' in outbox[-1]['To'])
        self.assertTrue('aread@' in outbox[-1]['Cc'])

    def test_edit_ballot_writeup(self):
        draft = IndividualDraftFactory()
        url = urlreverse('ietf.doc.views_ballot.ballot_writeupnotes', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # add a IANA review note
        draft.set_state(State.objects.get(used=True, type="draft-iana-review", slug="not-ok"))
        DocEvent.objects.create(type="iana_review",
                                doc=draft,
                                rev=draft.rev,
                                by=Person.objects.get(user__username="iana"),
                                desc="IANA does not approve of this document, it does not make sense.",
                                )

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('textarea[name=ballot_writeup]')), 1)
        self.assertTrue(q('[type=submit]:contains("Save")'))
        self.assertTrue("IANA does not" in unicontent(r))

        # save
        r = self.client.post(url, dict(
                ballot_writeup="This is a simple test.",
                save_ballot_writeup="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        self.assertTrue("This is a simple test" in draft.latest_event(WriteupDocEvent, type="changed_ballot_writeup_text").text)

    def test_edit_ballot_rfceditornote(self):
        draft = IndividualDraftFactory()
        url = urlreverse('ietf.doc.views_ballot.ballot_rfceditornote', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # add a note to the RFC Editor
        WriteupDocEvent.objects.create(
            doc=draft,
            rev=draft.rev,
            desc="Changed text",
            type="changed_rfc_editor_note_text",
            text="This is a note for the RFC Editor.",
            by=Person.objects.get(name="(System)"))

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('textarea[name=rfc_editor_note]')), 1)
        self.assertTrue(q('[type=submit]:contains("Save")'))
        self.assertTrue("<label class=\"control-label\">RFC Editor Note</label>" in r.content)
        self.assertTrue("This is a note for the RFC Editor" in r.content)

        # save with a note
        empty_outbox()
        r = self.client.post(url, dict(
                rfc_editor_note="This is a simple test.",
                save_ballot_rfceditornote="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        self.assertTrue(draft.has_rfc_editor_note())
        self.assertTrue("This is a simple test" in draft.latest_event(WriteupDocEvent, type="changed_rfc_editor_note_text").text)
        self.assertEqual(len(outbox), 0)

        # clear the existing note
        r = self.client.post(url, dict(
                rfc_editor_note=" ",
                clear_ballot_rfceditornote="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        self.assertFalse(draft.has_rfc_editor_note())

        # Add a note after the doc is approved
        empty_outbox()
        draft.set_state(State.objects.get(type='draft-iesg',slug='approved'))
        r = self.client.post(url, dict(
                rfc_editor_note='This is a new note.',
                save_ballot_rfceditornote="1"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(outbox),1)
        self.assertIn('RFC Editor note changed',outbox[-1]['Subject'])

    def test_issue_ballot(self):
        ad = Person.objects.get(user__username="ad")
        for case in ('none','past','future'):
            draft = IndividualDraftFactory(ad=ad)
            if case in ('past','future'):
                LastCallDocEvent.objects.create(
                    by=Person.objects.get(name='(System)'),
                    type='sent_last_call',
                    doc=draft,
                    rev=draft.rev,
                    desc='issued last call',
                    expires = datetime.datetime.now()+datetime.timedelta(days = 1 if case=='future' else -1)
                )
            url = urlreverse('ietf.doc.views_ballot.ballot_writeupnotes', kwargs=dict(name=draft.name))
            login_testing_unauthorized(self, "ad", url)


            empty_outbox()
            
            r = self.client.post(url, dict(
                    ballot_writeup="This is a test.",
                    issue_ballot="1"))
            self.assertEqual(r.status_code, 200)
            draft = Document.objects.get(name=draft.name)

            self.assertTrue(draft.latest_event(type="sent_ballot_announcement"))
            self.assertEqual(len(outbox), 2)
            self.assertTrue('Ballot issued:' in outbox[-2]['Subject'])
            self.assertTrue('iesg@' in outbox[-2]['To'])
            self.assertTrue('Ballot issued:' in outbox[-1]['Subject'])
            self.assertTrue('drafts-eval@' in outbox[-1]['To'])
            self.assertTrue('X-IETF-Draft-string' in outbox[-1])
            if case=='none':
                self.assertNotIn('call expire', outbox[-1].get_payload(decode=True).decode("utf-8"))
            elif case=='past':
                self.assertIn('call expired', outbox[-1].get_payload(decode=True).decode("utf-8"))
            else:
                self.assertIn('call expires', outbox[-1].get_payload(decode=True).decode("utf-8"))
            self.client.logout()


    def test_edit_approval_text(self):
        ad = Person.objects.get(user__username="ad")
        draft = WgDraftFactory(ad=ad,states=[('draft','active'),('draft-iesg','iesg-eva')],intended_std_level_id='ps',group__parent=Group.objects.get(acronym='farfut'))
        url = urlreverse('ietf.doc.views_ballot.ballot_approvaltext', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('textarea[name=approval_text]')), 1)
        self.assertTrue(q('[type=submit]:contains("Save")'))

        # save
        r = self.client.post(url, dict(
                approval_text="This is a simple test.",
                save_approval_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        self.assertTrue("This is a simple test" in draft.latest_event(WriteupDocEvent, type="changed_ballot_approval_text").text)

        # test regenerate
        r = self.client.post(url, dict(regenerate_approval_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)        
        self.assertTrue("Subject: Protocol Action" in draft.latest_event(WriteupDocEvent, type="changed_ballot_approval_text").text)

        # test regenerate when it's a disapprove
        draft.set_state(State.objects.get(used=True, type="draft-iesg", slug="nopubadw"))

        r = self.client.post(url, dict(regenerate_approval_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        self.assertIn("NOT be published", unwrap(draft.latest_event(WriteupDocEvent, type="changed_ballot_approval_text").text))

        # test regenerate when it's a conflict review
        draft.group = Group.objects.get(type="individ")
        draft.stream_id = "irtf"
        draft.set_state(State.objects.get(used=True, type="draft-iesg", slug="iesg-eva"))
        draft.save_with_history([DocEvent.objects.create(doc=draft, rev=draft.rev, type="changed_document", by=Person.objects.get(user__username="secretary"), desc="Test")])

        r = self.client.post(url, dict(regenerate_approval_text="1"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue("Subject: Results of IETF-conflict review" in draft.latest_event(WriteupDocEvent, type="changed_ballot_approval_text").text)
        
    def test_edit_verify_permissions(self):

        def verify_fail(username, url):
            if username:
                self.client.login(username=username, password=username+"+password")
            r = self.client.get(url)
            self.assertEqual(r.status_code,403)

        def verify_can_see(username, url):
            self.client.login(username=username, password=username+"+password")
            r = self.client.get(url)
            self.assertEqual(r.status_code,200)
            q = PyQuery(r.content)
            self.assertEqual(len(q("<textarea class=\"form-control\"")),1) 

        for username in ['plain','marschairman']:
            PersonFactory(user__username=username)
        mars = GroupFactory(acronym='mars',type_id='wg')
        RoleFactory(group=mars,person=Person.objects.get(user__username='marschairman'),name_id='chair')
        ad = Person.objects.get(user__username="ad")
        draft = WgDraftFactory(group=mars,ad=ad,states=[('draft','active'),('draft-iesg','ad-eval')])

        events = []
        
        e = WriteupDocEvent()
        e.type = "changed_ballot_approval_text"
        e.by = Person.objects.get(name="(System)")
        e.doc = draft
        e.rev = draft.rev
        e.desc = u"Ballot approval text was generated"
        e.text = u"Test approval text."
        e.save()
        events.append(e)

        e = WriteupDocEvent()
        e.type = "changed_ballot_writeup_text"
        e.by = Person.objects.get(name="(System)")
        e.doc = draft
        e.rev = draft.rev
        e.desc = u"Ballot writeup was generated"
        e.text = u"Test ballot writeup text."
        e.save()
        events.append(e)

        e = WriteupDocEvent()
        e.type = "changed_ballot_rfceditornote_text"
        e.by = Person.objects.get(name="(System)")
        e.doc = draft
        e.rev = draft.rev
        e.desc = u"RFC Editor Note for ballot was generated"
        e.text = u"Test note to the RFC Editor text."
        e.save()
        events.append(e)

        # IETF Stream Documents
        for p in ['ietf.doc.views_ballot.ballot_approvaltext','ietf.doc.views_ballot.ballot_writeupnotes','ietf.doc.views_ballot.ballot_rfceditornote']:
            url = urlreverse(p, kwargs=dict(name=draft.name))

            for username in ['plain','marschairman','iab-chair','irtf-chair','ise','iana']:
                verify_fail(username, url)

            for username in ['secretary','ad']:
                verify_can_see(username, url)

        # RFC Editor Notes for documents in the IAB Stream
        draft.stream_id = 'iab'
        draft.save_with_history(events)
        url = urlreverse('ietf.doc.views_ballot.ballot_rfceditornote', kwargs=dict(name=draft.name))

        for username in ['plain','marschairman','ad','irtf-chair','ise','iana']:
            verify_fail(username, url)

        for username in ['secretary','iab-chair']:
            verify_can_see(username, url)

        # RFC Editor Notes for documents in the IRTF Stream
        e = DocEvent(doc=draft, rev=draft.rev, by=Person.objects.get(name="(System)"), type='changed_stream')
        e.desc = u"Changed stream to <b>%s</b>" % 'irtf'
        e.save()

        draft.stream_id = 'irtf'
        draft.save_with_history([e])
        url = urlreverse('ietf.doc.views_ballot.ballot_rfceditornote', kwargs=dict(name=draft.name))

        for username in ['plain','marschairman','ad','iab-chair','ise','iana']:
            verify_fail(username, url)

        for username in ['secretary','irtf chair']:
            verify_can_see(username, url)

        # RFC Editor Notes for documents in the IAB Stream
        e = DocEvent(doc=draft, rev=draft.rev, by=Person.objects.get(name="(System)"), type='changed_stream')
        e.desc = u"Changed stream to <b>%s</b>" % 'ise'
        e.save()

        draft.stream_id = 'ise'
        draft.save_with_history([e])
        url = urlreverse('ietf.doc.views_ballot.ballot_rfceditornote', kwargs=dict(name=draft.name))

        for username in ['plain','marschairman','ad','iab-chair','irtf-chair','iana']:
            verify_fail(username, url)

        for username in ['secretary','ise']:
            verify_can_see(username, url)

class ApproveBallotTests(TestCase):
    def test_approve_ballot(self):
        ad = Person.objects.get(name="Areað Irector")
        draft = IndividualDraftFactory(ad=ad, intended_std_level_id='ps')
        draft.set_state(State.objects.get(used=True, type="draft-iesg", slug="iesg-eva")) # make sure it's approvable

        url = urlreverse('ietf.doc.views_ballot.approve_ballot', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(q('[type=submit]:contains("send announcement")'))
        self.assertEqual(len(q('form pre:contains("Subject: Protocol Action")')), 1)
        self.assertEqual(len(q('form pre:contains("This is a note for the RFC Editor")')), 0)

        # add a note to the RFC Editor
        WriteupDocEvent.objects.create(
            doc=draft,
            rev=draft.rev,
            desc="Changed text",
            type="changed_rfc_editor_note_text",
            text="This is a note for the RFC Editor.",
            by=Person.objects.get(name="(System)"))
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(q('[type=submit]:contains("send announcement")'))
        self.assertEqual(len(q('form pre:contains("Subject: Protocol Action")')), 1)
        self.assertEqual(len(q('form pre:contains("This is a note for the RFC Editor")')), 1)

        # approve
        mailbox_before = len(outbox)

        r = self.client.post(url, dict(skiprfceditorpost="1"))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(name=draft.name)
        self.assertEqual(draft.get_state_slug("draft-iesg"), "ann")
        self.assertEqual(len(outbox), mailbox_before + 2)
        self.assertTrue("Protocol Action" in outbox[-2]['Subject'])
        self.assertTrue("ietf-announce" in outbox[-2]['To'])
        self.assertTrue("rfc-editor" in outbox[-2]['Cc'])
        # the IANA copy
        self.assertTrue("Protocol Action" in outbox[-1]['Subject'])
        self.assertTrue(not outbox[-1]['CC'])
        self.assertTrue('drafts-approval@icann.org' in outbox[-1]['To'])
        self.assertTrue("Protocol Action" in draft.message_set.order_by("-time")[0].subject)

    def test_disapprove_ballot(self):
        # This tests a codepath that is not used in production
        # and that has already had some drift from usefulness (it results in a
        # older-style conflict review response). 
        draft = IndividualDraftFactory()
        draft.set_state(State.objects.get(used=True, type="draft-iesg", slug="nopubadw"))

        url = urlreverse('ietf.doc.views_ballot.approve_ballot', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # disapprove (the Martians aren't going to be happy)
        mailbox_before = len(outbox)

        r = self.client.post(url, dict())
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(name=draft.name)
        self.assertEqual(draft.get_state_slug("draft-iesg"), "dead")
        self.assertEqual(len(outbox), mailbox_before + 1)
        self.assertTrue("NOT be published" in str(outbox[-1]))

    def test_clear_ballot(self):
        draft = IndividualDraftFactory()
        ad = Person.objects.get(user__username="ad")
        ballot = create_ballot_if_not_open(None, draft, ad, 'approve')
        old_ballot_id = ballot.id
        draft.set_state(State.objects.get(used=True, type="draft-iesg", slug="iesg-eva")) 
        url = urlreverse('ietf.doc.views_ballot.clear_ballot', kwargs=dict(name=draft.name,ballot_type_slug=draft.ballot_open('approve').ballot_type.slug))
        login_testing_unauthorized(self, "secretary", url)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        r = self.client.post(url,{})
        self.assertEqual(r.status_code, 302)
        ballot = draft.ballot_open('approve')
        self.assertIsNotNone(ballot)
        self.assertEqual(ballot.ballotpositiondocevent_set.count(),0)
        self.assertNotEqual(old_ballot_id, ballot.id)

class MakeLastCallTests(TestCase):
    def test_make_last_call(self):
        ad = Person.objects.get(user__username="ad")
        draft = WgDraftFactory(name='draft-ietf-mars-test',group__acronym='mars',ad=ad,states=[('draft-iesg','lc-req')],intended_std_level_id='ps')
        HolderIprDisclosureFactory(docs=[draft])

        url = urlreverse('ietf.doc.views_ballot.make_last_call', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)

        # normal get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('input[name=last_call_sent_date]')), 1)

        # make last call
        mailbox_before = len(outbox)

        expire_date = q('input[name=last_call_expiration_date]')[0].get("value")
        
        r = self.client.post(url,
                             dict(last_call_sent_date=q('input[name=last_call_sent_date]')[0].get("value"),
                                  last_call_expiration_date=expire_date
                                  ))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(name=draft.name)
        self.assertEqual(draft.get_state_slug("draft-iesg"), "lc")
        self.assertEqual(draft.latest_event(LastCallDocEvent, "sent_last_call").expires.strftime("%Y-%m-%d"), expire_date)

        self.assertEqual(len(outbox), mailbox_before + 2)

        self.assertTrue("Last Call" in outbox[-2]['Subject'])
        self.assertTrue("ietf-announce@" in outbox[-2]['To'])
        for prefix in ['draft-ietf-mars-test','mars-chairs','aread']:
            self.assertTrue(prefix+"@" in outbox[-2]['Cc'])
        self.assertIn("The following IPR Declarations",outbox[-2].get_payload())

        self.assertTrue("Last Call" in outbox[-1]['Subject'])
        self.assertTrue("drafts-lastcall@icann.org" in outbox[-1]['To'])

        self.assertTrue("Last Call" in draft.message_set.order_by("-time")[0].subject)

class DeferUndeferTestCase(TestCase):
    def helper_test_defer(self,name):

        doc = Document.objects.get(name=name)
        url = urlreverse('ietf.doc.views_ballot.defer_ballot',kwargs=dict(name=doc.name))

        login_testing_unauthorized(self, "ad", url)

        # Verify that you can't defer a document that's not on a telechat
        r = self.client.post(url,dict())
        self.assertEqual(r.status_code, 404)

        # Put the document on a telechat
        dates = TelechatDate.objects.active().order_by("date")
        first_date = dates[0].date
        second_date = dates[1].date

        e = TelechatDocEvent(type="scheduled_for_telechat",
                             doc = doc,
                             rev = doc.rev,
                             by = Person.objects.get(name="Areað Irector"),
                             telechat_date = first_date,
                             returning_item = False, 
                            )
        e.save()

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[type=submit]:contains("Defer ballot")')),1)

        # defer
        mailbox_before = len(outbox)
        self.assertEqual(doc.telechat_date(), first_date)
        r = self.client.post(url,dict())
        self.assertEqual(r.status_code, 302)
        doc = Document.objects.get(name=name)
        self.assertEqual(doc.telechat_date(), second_date)
        self.assertFalse(doc.returning_item())
        defer_states = dict(draft=['draft-iesg','defer'],conflrev=['conflrev','defer'],statchg=['statchg','defer'])
        if doc.type_id in defer_states:
           self.assertEqual(doc.get_state(defer_states[doc.type_id][0]).slug,defer_states[doc.type_id][1])
        self.assertTrue(doc.active_defer_event())

        self.assertEqual(len(outbox), mailbox_before + 2)

        self.assertTrue('Telechat update' in outbox[-2]['Subject'])
        self.assertTrue('iesg-secretary@' in outbox[-2]['To'])
        self.assertTrue('iesg@' in outbox[-2]['To'])

        self.assertTrue("Deferred" in outbox[-1]['Subject'])
        self.assertTrue(doc.file_tag() in outbox[-1]['Subject'])
        self.assertTrue('iesg@' in outbox[-1]['To'])

        # Ensure it's not possible to defer again
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)
        r = self.client.post(url,dict())
        self.assertEqual(r.status_code, 404) 


    def helper_test_undefer(self,name):

        doc = Document.objects.get(name=name)
        url = urlreverse('ietf.doc.views_ballot.undefer_ballot',kwargs=dict(name=doc.name))

        login_testing_unauthorized(self, "ad", url)

        # some additional setup
        dates = TelechatDate.objects.active().order_by("date")
        first_date = dates[0].date
        second_date = dates[1].date

        e = TelechatDocEvent(type="scheduled_for_telechat",
                             doc = doc,
                             rev = doc.rev,
                             by = Person.objects.get(name="Areað Irector"),
                             telechat_date = second_date,
                             returning_item = True, 
                            )
        e.save()
        defer_states = dict(draft=['draft-iesg','defer'],conflrev=['conflrev','defer'],statchg=['statchg','defer'])
        if doc.type_id in defer_states:
            doc.set_state(State.objects.get(used=True, type=defer_states[doc.type_id][0],slug=defer_states[doc.type_id][1]))

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[type=submit]:contains("Undefer ballot")')),1)

        # undefer
        mailbox_before = len(outbox)
        self.assertEqual(doc.telechat_date(), second_date)
        r = self.client.post(url,dict())
        self.assertEqual(r.status_code, 302)
        doc = Document.objects.get(name=name)
        self.assertEqual(doc.telechat_date(), first_date)
        self.assertTrue(doc.returning_item()) 
        undefer_states = dict(draft=['draft-iesg','iesg-eva'],conflrev=['conflrev','iesgeval'],statchg=['statchg','iesgeval'])
        if doc.type_id in undefer_states:
           self.assertEqual(doc.get_state(undefer_states[doc.type_id][0]).slug,undefer_states[doc.type_id][1])
        self.assertFalse(doc.active_defer_event())
        self.assertEqual(len(outbox), mailbox_before + 2)
        self.assertTrue("Telechat update" in outbox[-2]['Subject'])
        self.assertTrue('iesg-secretary@' in outbox[-2]['To'])
        self.assertTrue('iesg@' in outbox[-2]['To'])
        self.assertTrue("Undeferred" in outbox[-1]['Subject'])
        self.assertTrue(doc.file_tag() in outbox[-1]['Subject'])
        self.assertTrue('iesg@' in outbox[-1]['To'])

        # Ensure it's not possible to undefer again
        r = self.client.get(url)
        self.assertEqual(r.status_code, 404)
        r = self.client.post(url,dict())
        self.assertEqual(r.status_code, 404) 

    def test_defer_draft(self):
        self.helper_test_defer('draft-ietf-mars-test')

    def test_defer_conflict_review(self):
        self.helper_test_defer('conflict-review-imaginary-irtf-submission')

    def test_defer_status_change(self):
        self.helper_test_defer('status-change-imaginary-mid-review')

    def test_undefer_draft(self):
        self.helper_test_undefer('draft-ietf-mars-test')

    def test_undefer_conflict_review(self):
        self.helper_test_undefer('conflict-review-imaginary-irtf-submission')

    def test_undefer_status_change(self):
        self.helper_test_undefer('status-change-imaginary-mid-review')

    # when charters support being deferred, be sure to test them here

    def setUp(self):
        IndividualDraftFactory(name='draft-ietf-mars-test',states=[('draft','active'),('draft-iesg','iesg-eva')])
        DocumentFactory(type_id='statchg',name='status-change-imaginary-mid-review',states=[('statchg','iesgeval')])
        DocumentFactory(type_id='conflrev',name='conflict-review-imaginary-irtf-submission',states=[('conflrev','iesgeval')])

class RegenerateLastCallTestCase(TestCase):

    def test_regenerate_last_call(self):
        draft = WgDraftFactory.create(
                    stream_id='ietf',
                    states=[('draft','active'),('draft-iesg','pub-req')],
                    intended_std_level_id='ps',
                )
    
        url = urlreverse('ietf.doc.views_ballot.lastcalltext', kwargs=dict(name=draft.name))
        login_testing_unauthorized(self, "secretary", url)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        r = self.client.post(url, dict(regenerate_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        lc_text = draft.latest_event(WriteupDocEvent, type="changed_last_call_text").text
        self.assertTrue("Subject: Last Call" in lc_text)
        self.assertFalse("contains these normative down" in lc_text)

        rfc = IndividualRfcFactory.create(
                  stream_id='ise',
                  other_aliases=['rfc6666',],
                  states=[('draft','rfc'),('draft-iesg','pub')],
                  std_level_id='inf',
              )

        draft.relateddocument_set.create(target=rfc.docalias_set.get(name='rfc6666'),relationship_id='refnorm')

        r = self.client.post(url, dict(regenerate_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        lc_text = draft.latest_event(WriteupDocEvent, type="changed_last_call_text").text
        self.assertTrue("contains these normative down" in lc_text)
        self.assertTrue("rfc6666" in lc_text)
        self.assertTrue("Independent Submission Editor stream" in lc_text)

        draft.relateddocument_set.create(target=rfc.docalias_set.get(name='rfc6666'),relationship_id='downref-approval')

        r = self.client.post(url, dict(regenerate_last_call_text="1"))
        self.assertEqual(r.status_code, 200)
        draft = Document.objects.get(name=draft.name)
        lc_text = draft.latest_event(WriteupDocEvent, type="changed_last_call_text").text
        self.assertFalse("contains these normative down" in lc_text)
        self.assertFalse("rfc6666" in lc_text)
