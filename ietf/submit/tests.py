# Copyright The IETF Trust 2011-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import datetime
import email
import io
import os
import re
import shutil
import six
import sys


from io import StringIO
from pyquery import PyQuery

from django.conf import settings
from django.urls import reverse as urlreverse
from django.utils.encoding import force_str, force_text

import debug                            # pyflakes:ignore

from ietf.submit.utils import expirable_submissions, expire_submission
from ietf.doc.factories import DocumentFactory, WgDraftFactory, IndividualDraftFactory
from ietf.doc.models import Document, DocAlias, DocEvent, State, BallotPositionDocEvent, DocumentAuthor
from ietf.doc.utils import create_ballot_if_not_open
from ietf.group.factories import GroupFactory, RoleFactory
from ietf.group.models import Group
from ietf.group.utils import setup_default_community_list_for_group
from ietf.meeting.models import Meeting
from ietf.meeting.factories import MeetingFactory
from ietf.message.models import Message
from ietf.name.models import FormalLanguageName
from ietf.person.models import Person
from ietf.person.factories import UserFactory, PersonFactory
from ietf.submit.models import Submission, Preapproval
from ietf.submit.mail import add_submission_email, process_response_email
from ietf.utils.mail import outbox, empty_outbox, get_payload
from ietf.utils.models import VersionInfo
from ietf.utils.test_utils import login_testing_unauthorized, TestCase
from ietf.utils.draft import Draft


def submission_file(name, rev, group, format, templatename, author=None, email=None, title=None, year=None, ascii=True):
    # construct appropriate text draft
    f = io.open(os.path.join(settings.BASE_DIR, "submit", templatename))
    template = f.read()
    f.close()

    if author is None:
        author = PersonFactory()
    if email is None:
        email = author.email().address.lower() if author.email() else None
    if title is None:
        title = "Test Document"
    if year is None:
        year = datetime.date.today().strftime("%Y")

    submission_text = template % dict(
            date=datetime.date.today().strftime("%d %B %Y"),
            expiration=(datetime.date.today() + datetime.timedelta(days=100)).strftime("%d %B, %Y"),
            year=year,
            month=datetime.date.today().strftime("%B"),
            day=datetime.date.today().strftime("%d"),
            name="%s-%s" % (name, rev),
            group=group or "",
            author=author.ascii if ascii else author.name,
            asciiAuthor=author.ascii,
            initials=author.initials(),
            surname=author.ascii_parts()[3] if ascii else author.name_parts()[3],
            asciiSurname=author.ascii_parts()[3],
            email=email,
            title=title,
    )
    file = StringIO(submission_text)
    file.name = "%s-%s.%s" % (name, rev, format)
    return file, author

class SubmitTests(TestCase):
    def setUp(self):
        self.saved_idsubmit_staging_path = settings.IDSUBMIT_STAGING_PATH
        self.staging_dir = self.tempdir('submit-staging')
        settings.IDSUBMIT_STAGING_PATH = self.staging_dir

        self.saved_internet_draft_path = settings.INTERNET_DRAFT_PATH
        self.saved_idsubmit_repository_path = settings.IDSUBMIT_REPOSITORY_PATH
        self.repository_dir = self.tempdir('submit-repository')
        settings.INTERNET_DRAFT_PATH = settings.IDSUBMIT_REPOSITORY_PATH = self.repository_dir

        self.saved_archive_dir = settings.INTERNET_DRAFT_ARCHIVE_DIR
        self.archive_dir = self.tempdir('submit-archive')
        settings.INTERNET_DRAFT_ARCHIVE_DIR = self.archive_dir
        
        self.saved_yang_rfc_model_dir = settings.SUBMIT_YANG_RFC_MODEL_DIR
        self.yang_rfc_model_dir = self.tempdir('yang-rfc-model')
        settings.SUBMIT_YANG_RFC_MODEL_DIR = self.yang_rfc_model_dir

        self.saved_yang_draft_model_dir = settings.SUBMIT_YANG_DRAFT_MODEL_DIR
        self.yang_draft_model_dir = self.tempdir('yang-draft-model')
        settings.SUBMIT_YANG_DRAFT_MODEL_DIR = self.yang_draft_model_dir

        self.saved_yang_iana_model_dir = settings.SUBMIT_YANG_IANA_MODEL_DIR
        self.yang_iana_model_dir = self.tempdir('yang-iana-model')
        settings.SUBMIT_YANG_IANA_MODEL_DIR = self.yang_iana_model_dir

        # Submit views assume there is a "next" IETF to look for cutoff dates against
        MeetingFactory(type_id='ietf', date=datetime.date.today()+datetime.timedelta(days=180))

    def tearDown(self):
        shutil.rmtree(self.staging_dir)
        shutil.rmtree(self.repository_dir)
        shutil.rmtree(self.archive_dir)
        shutil.rmtree(self.yang_rfc_model_dir)
        shutil.rmtree(self.yang_draft_model_dir)
        shutil.rmtree(self.yang_iana_model_dir)
        settings.IDSUBMIT_STAGING_PATH = self.saved_idsubmit_staging_path
        settings.INTERNET_DRAFT_PATH = self.saved_internet_draft_path
        settings.IDSUBMIT_REPOSITORY_PATH = self.saved_idsubmit_repository_path
        settings.INTERNET_DRAFT_ARCHIVE_DIR = self.saved_archive_dir
        settings.SUBMIT_YANG_RFC_MODEL_DIR = self.saved_yang_rfc_model_dir
        settings.SUBMIT_YANG_DRAFT_MODEL_DIR = self.saved_yang_draft_model_dir
        settings.SUBMIT_YANG_IANA_MODEL_DIR = self.saved_yang_iana_model_dir


    def do_submission(self, name, rev, group=None, formats=["txt",], author=None):
        # break early in case of missing configuration
        self.assertTrue(os.path.exists(settings.IDSUBMIT_IDNITS_BINARY))

        # get
        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('input[type=file][name=txt]')), 1)
        self.assertEqual(len(q('input[type=file][name=xml]')), 1)

        # submit
        files = {}
        if author is None:
            author = PersonFactory()
        for format in formats:
            files[format], __ = submission_file(name, rev, group, format, "test_submission.%s" % format, author=author)

        r = self.client.post(url, files)
        if r.status_code != 302:
            q = PyQuery(r.content)
            print(q('div.has-error div.alert').text())

        self.assertNoFormPostErrors(r, ".has-error,.alert-danger")

        status_url = r["Location"]
        for format in formats:
            self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.%s" % (name, rev, format))))
            if format == 'xml':
                self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.%s" % (name, rev, 'html'))))
        self.assertEqual(Submission.objects.filter(name=name).count(), 1)
        submission = Submission.objects.get(name=name)
        if len(submission.authors) != 1:
            sys.stderr.write("\nAuthor extraction failure.\n")
            sys.stderr.write(six.ensure_text("Author name used in test: %s\n"%author))
            sys.stderr.write("Author ascii name: %s\n" % author.ascii)
            sys.stderr.write("Author initials: %s\n" % author.initials())
        self.assertEqual(len(submission.authors), 1)
        a = submission.authors[0]
        self.assertEqual(a["name"], author.ascii)
        self.assertEqual(a["email"], author.email().address.lower())
        self.assertEqual(a["affiliation"], "Test Centre Inc.")
        self.assertEqual(a["country"], "UK")

        return status_url, author

    def supply_extra_metadata(self, name, status_url, submitter_name, submitter_email, replaces):
        # check the page
        r = self.client.get(status_url)
        q = PyQuery(r.content)
        post_button = q('[type=submit]:contains("Post")')
        self.assertEqual(len(post_button), 1)
        action = post_button.parents("form").find('input[type=hidden][name="action"]').val()

        # post submitter info
        r = self.client.post(status_url, {
            "action": action,
            "submitter-name": submitter_name,
            "submitter-email": submitter_email,
            "replaces": replaces,
        })

        if r.status_code == 302:
            submission = Submission.objects.get(name=name)
            self.assertEqual(submission.submitter, email.utils.formataddr((submitter_name, submitter_email)))
            self.assertEqual(submission.replaces, ",".join(d.name for d in DocAlias.objects.filter(pk__in=replaces.split(",") if replaces else [])))

        return r

    def extract_confirmation_url(self, confirmation_email):
        # dig out confirmation_email link
        charset = confirmation_email.get_content_charset()
        msg = confirmation_email.get_payload(decode=True).decode(charset)
        line_start = "http"
        confirmation_url = None
        for line in msg.split("\n"):
            if line.strip().startswith(line_start):
                confirmation_url = line.strip()
        self.assertTrue(confirmation_url)

        return confirmation_url

    def submit_new_wg(self, formats):
        # submit new -> supply submitter info -> approve
        GroupFactory(type_id='wg',acronym='ames')
        mars = GroupFactory(type_id='wg', acronym='mars')
        RoleFactory(name_id='chair', group=mars, person__user__username='marschairman')
        draft = WgDraftFactory(group=mars)
        setup_default_community_list_for_group(draft.group)

        # prepare draft to suggest replace
        sug_replaced_draft = Document.objects.create(
            name="draft-ietf-ames-sug-replaced",
            time=datetime.datetime.now(),
            type_id="draft",
            title="Draft to be suggested to be replaced",
            stream_id="ietf",
            group=Group.objects.get(acronym="ames"),
            abstract="Blahblahblah.",
            rev="01",
            pages=2,
            words=100,
            intended_std_level_id="ps",
            ad=draft.ad,
            expires=datetime.datetime.now() + datetime.timedelta(days=settings.INTERNET_DRAFT_DAYS_TO_EXPIRE),
            notify="aliens@example.mars",
            note="",
        )
        sug_replaced_draft.set_state(State.objects.get(used=True, type="draft", slug="active"))
        sug_replaced_alias = DocAlias.objects.create(name=sug_replaced_draft.name)
        sug_replaced_alias.docs.add(sug_replaced_draft)

        name = "draft-ietf-mars-testing-tests"
        rev = "00"
        group = "mars"

        status_url, author = self.do_submission(name, rev, group, formats)

        # supply submitter info, then draft should be in and ready for approval
        mailbox_before = len(outbox)
        replaced_alias = draft.docalias.first()
        r = self.supply_extra_metadata(name, status_url, author.ascii, author.email().address.lower(),
                                       replaces=str(replaced_alias.pk) + "," + str(sug_replaced_alias.pk))

        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        self.assertEqual(len(outbox), mailbox_before + 1)
        self.assertTrue("New draft waiting for approval" in outbox[-1]["Subject"])
        self.assertTrue(name in outbox[-1]["Subject"])

        # as chair of WG, we should see approval button
        self.client.login(username="marschairman", password="marschairman+password")

        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)

        self.assertContains(r, 'xym')
        self.assertContains(r, 'pyang')
        if settings.SUBMIT_YANGLINT_COMMAND:
            self.assertContains(r, 'yanglint')

        q = PyQuery(r.content)
        approve_button = q('[type=submit]:contains("Approve")')
        self.assertEqual(len(approve_button), 1)

        action = approve_button.parents("form").find('input[type=hidden][name="action"]').val()

        # approve submission
        mailbox_before = len(outbox)
        r = self.client.post(status_url, dict(action=action))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, rev)
        new_revision = draft.latest_event(type="new_revision")
        self.assertEqual(draft.group.acronym, "mars")
        self.assertEqual(new_revision.type, "new_revision")
        self.assertEqual(new_revision.by.name, author.name)
        self.assertTrue(draft.latest_event(type="added_suggested_replaces"))
        self.assertTrue(not os.path.exists(os.path.join(self.staging_dir, "%s-%s.txt" % (name, rev))))
        self.assertTrue(os.path.exists(os.path.join(self.repository_dir, "%s-%s.txt" % (name, rev))))
        self.assertEqual(draft.type_id, "draft")
        self.assertEqual(draft.stream_id, "ietf")
        self.assertTrue(draft.expires >= datetime.datetime.now() + datetime.timedelta(days=settings.INTERNET_DRAFT_DAYS_TO_EXPIRE - 1))
        self.assertEqual(draft.get_state("draft-stream-%s" % draft.stream_id).slug, "wg-doc")
        authors = draft.documentauthor_set.all()
        self.assertEqual(len(authors), 1)
        self.assertEqual(authors[0].person, author)
        self.assertEqual(set(draft.formal_languages.all()), set(FormalLanguageName.objects.filter(slug="json")))
        self.assertEqual(draft.relations_that_doc("replaces").count(), 1)
        self.assertTrue(draft.relations_that_doc("replaces").first().target, replaced_alias)
        self.assertEqual(draft.relations_that_doc("possibly-replaces").count(), 1)
        self.assertTrue(draft.relations_that_doc("possibly-replaces").first().target, sug_replaced_alias)
        self.assertEqual(len(outbox), mailbox_before + 5)
        self.assertIn(("I-D Action: %s" % name), outbox[-4]["Subject"])
        self.assertIn(author.ascii, get_payload(outbox[-4]))
        self.assertIn(("I-D Action: %s" % name), outbox[-3]["Subject"])
        self.assertIn(author.ascii, get_payload(outbox[-3]))
        self.assertIn("New Version Notification",outbox[-2]["Subject"])
        self.assertIn(name, get_payload(outbox[-2]))
        self.assertIn("mars", get_payload(outbox[-2]))
        # Check "Review of suggested possible replacements for..." mail
        self.assertIn("review", outbox[-1]["Subject"].lower())
        self.assertIn(name, get_payload(outbox[-1]))
        self.assertIn(sug_replaced_alias.name, get_payload(outbox[-1]))
        self.assertIn("ames-chairs@", outbox[-1]["To"].lower())
        self.assertIn("mars-chairs@", outbox[-1]["To"].lower())

        # fetch the document page
        url = urlreverse('ietf.doc.views_doc.document_main', kwargs={'name':name})
        r = self.client.get(url)
        self.assertContains(r, name)
        self.assertContains(r, 'Active Internet-Draft')
        self.assertContains(r, 'mars WG')
        self.assertContains(r, 'Yang Validation')
        self.assertContains(r, 'WG Document')

    def test_submit_new_wg_txt(self):
        self.submit_new_wg(["txt"])

    def text_submit_new_wg_xml(self):
        self.submit_new_wg(["xml"])

    def text_submit_new_wg_txt_xml(self):
        self.submit_new_wg(["txt", "xml"])

    def submit_existing(self, formats, change_authors=True, group_type='wg', stream_type='ietf'):
        # submit new revision of existing -> supply submitter info -> prev authors confirm
        if stream_type == 'ietf':
            ad = Person.objects.get(user__username='ad')
            if group_type == 'area':
                group = GroupFactory(type_id='area', acronym='mars')
                RoleFactory(name_id='ad', group=group, person=ad)
            else:
                area = GroupFactory(type_id='area')
                RoleFactory(name_id='ad',group=area,person=ad)
                group = GroupFactory(type_id=group_type, parent=area, acronym='mars')
            draft = DocumentFactory(type_id='draft', group=group, stream_id=stream_type, ad=ad, authors=PersonFactory.create_batch(1))
            draft.set_state(State.objects.get(type_id='draft-stream-ietf',slug='wg-doc'))

            # pretend IANA reviewed it
            draft.set_state(State.objects.get(used=True, type="draft-iana-review", slug="not-ok"))

            # pretend it was approved to check that we notify the RFC Editor
            e = DocEvent(type="iesg_approved", doc=draft, rev=draft.rev)
            e.time = draft.time
            e.by = Person.objects.get(name="(System)")
            e.desc = "The IESG approved the document"
            e.save()

            # make a discuss to see if the AD gets an email
            ad = Person.objects.get(user__username="ad")
            ballot = create_ballot_if_not_open(None, draft, ad, 'approve')
            ballot_position = BallotPositionDocEvent()
            ballot_position.ballot = ballot
            ballot_position.pos_id = "discuss"
            ballot_position.type = "changed_ballot_position"
            ballot_position.doc = draft
            ballot_position.rev = draft.rev
            ballot_position.ad = ballot_position.by = Person.objects.get(user__username="ad2")
            ballot_position.save()

        elif stream_type == 'irtf':
            group = GroupFactory(type_id='rg', parent=Group.objects.get(acronym='irtf'), acronym='mars')
            draft = DocumentFactory(type_id='draft', group=group, stream_id='irtf', authors=PersonFactory.create_batch(1))

        else:
            draft = IndividualDraftFactory(stream_id=stream_type, authors=PersonFactory.create_batch(1))
            
        prev_author = draft.documentauthor_set.all()[0]
        if change_authors:
            # Make it such that one of the previous authors has an invalid email address
            nomail_author = PersonFactory()
            email = nomail_author.email()
            email.address='unknown-email-%s' % nomail_author.plain_ascii().replace(' ', '-')
            email.save()
            DocumentAuthor.objects.create(document=draft, person=nomail_author, email=email, order=draft.documentauthor_set.latest('order').order+1)

        # Set the revision needed tag
        draft.tags.add("need-rev")

        name = draft.name
        rev = "%02d" % (int(draft.rev) + 1)
        group = draft.group

        # write the old draft in a file so we can check it's moved away
        old_rev = draft.rev
        with io.open(os.path.join(self.repository_dir, "%s-%s.txt" % (name, old_rev)), 'w') as f:
            f.write("a" * 2000)

        old_docevents = list(draft.docevent_set.all())

        status_url, author = self.do_submission(name, rev, group, formats, author=prev_author.person)

        # supply submitter info, then previous authors get a confirmation email
        mailbox_before = len(outbox)
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "submitter@example.com", replaces="")
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "The submission is pending approval by the authors")

        self.assertEqual(len(outbox), mailbox_before + 1)
        confirm_email = outbox[-1]
        self.assertTrue("Confirm submission" in confirm_email["Subject"])
        self.assertTrue(name in confirm_email["Subject"])
        self.assertTrue(prev_author.email.address in confirm_email["To"])
        if change_authors:
            self.assertTrue("author@example.com" not in confirm_email["To"])
        self.assertTrue("submitter@example.com" not in confirm_email["To"])
        # Verify that mail wasn't sent to know invalid addresses
        self.assertTrue("unknown-email-" not in confirm_email["To"])
        if change_authors:
            # Since authors changed, ensure chairs are copied (and that the message says why)
            self.assertTrue("chairs have been copied" in six.text_type(confirm_email))
            if group_type in ['wg','rg','ag']:
                self.assertTrue("mars-chairs@" in confirm_email["To"].lower())
            elif group_type == 'area':
                self.assertTrue("aread@" in confirm_email["To"].lower())
            else:
                pass
            if stream_type=='ise':
               self.assertTrue("rfc-ise@" in confirm_email["To"].lower())
        else:
            self.assertNotIn("chairs have been copied", six.text_type(confirm_email))
            self.assertNotIn("mars-chairs@", confirm_email["To"].lower())

        confirmation_url = self.extract_confirmation_url(confirm_email)

        # go to confirm page
        r = self.client.get(confirmation_url)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[type=submit]:contains("Confirm")')), 1)

        # confirm
        mailbox_before = len(outbox)
        r = self.client.post(confirmation_url, {'action':'confirm'})
        self.assertEqual(r.status_code, 302)

        new_docevents = draft.docevent_set.exclude(pk__in=[event.pk for event in old_docevents])

        # check we have document events 
        doc_events = new_docevents.filter(type__in=["new_submission", "added_comment"])
        edescs = '::'.join([x.desc for x in doc_events])
        self.assertTrue('New version approved' in edescs)
        self.assertTrue('Uploaded new revision' in edescs)

        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, rev)
        self.assertEqual(draft.group.acronym, name.split("-")[2])
        #
        docevents = list(new_docevents.order_by("-time", "-id"))
        # Latest events are first (this is the default, but we make it explicit)
        # Assert event content in chronological order:

        def inspect_docevents(docevents, event_delta, type, be_in_desc, by_name):
            self.assertEqual(docevents[event_delta].type, type)
            self.assertIn(be_in_desc, docevents[event_delta].desc)
            self.assertEqual(docevents[event_delta].by.name, by_name)
            if len(docevents) > event_delta + 1:
                self.assertGreater(docevents[event_delta].id, docevents[event_delta+1].id)

        if draft.stream_id == 'ietf':
            inspect_docevents(docevents, 5, "new_submission", "Uploaded new revision", "Submitter Name")
            inspect_docevents(docevents, 4, "new_submission", "Request for posting confirmation", "(System)")
            inspect_docevents(docevents, 3, "new_submission", "New version approved", "(System)")
            inspect_docevents(docevents, 2, "new_revision", "New version available", "Submitter Name")
            inspect_docevents(docevents, 1, "changed_state", "IANA Review", "(System)")
            inspect_docevents(docevents, 0, "changed_document", "AD Followup", "(System)")
        elif draft.stream_id in ('ise', 'irtf', 'iab'):
            inspect_docevents(docevents, 4, "new_submission", "Uploaded new revision", "Submitter Name")
            inspect_docevents(docevents, 3, "new_submission", "Request for posting confirmation", "(System)")
            inspect_docevents(docevents, 2, "new_submission", "New version approved", "(System)")
            inspect_docevents(docevents, 1, "new_revision", "New version available", "Submitter Name")
            inspect_docevents(docevents, 0, "changed_document", "tag cleared", "(System)")
        else:
            pass

        self.assertTrue(not os.path.exists(os.path.join(self.repository_dir, "%s-%s.txt" % (name, old_rev))))
        self.assertTrue(os.path.exists(os.path.join(self.archive_dir, "%s-%s.txt" % (name, old_rev))))
        self.assertTrue(not os.path.exists(os.path.join(self.staging_dir, "%s-%s.txt" % (name, rev))))
        self.assertTrue(os.path.exists(os.path.join(self.repository_dir, "%s-%s.txt" % (name, rev))))
        self.assertEqual(draft.type_id, "draft")
        if stream_type == 'ietf':
            self.assertEqual(draft.stream_id, "ietf")
            self.assertEqual(draft.get_state_slug("draft-stream-%s" % draft.stream_id), "wg-doc")
            self.assertEqual(draft.get_state_slug("draft-iana-review"), "changed")
        authors = draft.documentauthor_set.all()
        self.assertEqual(len(authors), 1)
        self.assertIn(author, [ a.person for a in authors ])
        self.assertEqual(len(outbox), mailbox_before + 3)
        self.assertTrue(("I-D Action: %s" % name) in outbox[-3]["Subject"])
        self.assertTrue(("I-D Action: %s" % name) in draft.message_set.order_by("-time")[0].subject)
        self.assertTrue(author.ascii in get_payload(outbox[-3]))
        self.assertTrue("i-d-announce@" in outbox[-3]['To'])
        self.assertTrue("New Version Notification" in outbox[-2]["Subject"])
        self.assertTrue(name in get_payload(outbox[-2]))
        interesting_address = {'ietf':'mars', 'irtf':'irtf-chair', 'iab':'iab-chair', 'ise':'rfc-ise'}[draft.stream_id]
        self.assertTrue(interesting_address in force_text(outbox[-2].as_string()))
        if draft.stream_id == 'ietf':
            self.assertTrue(draft.ad.role_email("ad").address in force_text(outbox[-2].as_string()))
            self.assertTrue(ballot_position.ad.role_email("ad").address in force_text(outbox[-2].as_string()))
        self.assertTrue("New Version Notification" in outbox[-1]["Subject"])
        self.assertTrue(name in get_payload(outbox[-1]))
        r = self.client.get(urlreverse('ietf.doc.views_search.recent_drafts'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, draft.name)
        self.assertContains(r, draft.title)


    def test_submit_existing_txt(self):
        self.submit_existing(["txt"])

    def test_submit_existing_xml(self):
        self.submit_existing(["xml"])

    def test_submit_existing_txt_xml(self):
        self.submit_existing(["txt", "xml"])

    def test_submit_existing_txt_preserve_authors(self):
        self.submit_existing(["txt"], change_authors=False)

    def test_submit_existing_rg(self):
        self.submit_existing(["txt"],group_type='rg', stream_type='irtf')

    def test_submit_existing_ag(self):
        self.submit_existing(["txt"],group_type='ag')

    def test_submit_existing_area(self):
        self.submit_existing(["txt"],group_type='area')

    def test_submit_existing_ise(self):
        self.submit_existing(["txt"],stream_type='ise', group_type='individ')

    def test_submit_existing_iab(self):
        self.submit_existing(["txt"],stream_type='iab', group_type='individ')

    def submit_new_individual(self, formats):
        # submit new -> supply submitter info -> confirm

        name = "draft-authorname-testing-tests"
        rev = "00"
        group = None

        status_url, author = self.do_submission(name, rev, group, formats)

        # supply submitter info, then draft should be be ready for email auth
        mailbox_before = len(outbox)
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "submitter@example.com", replaces="")

        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "The submission is pending email authentication")

        self.assertEqual(len(outbox), mailbox_before + 1)
        confirm_email = outbox[-1]
        self.assertTrue("Confirm submission" in confirm_email["Subject"])
        self.assertTrue(name in confirm_email["Subject"])
        # both submitter and author get email
        self.assertTrue(author.email().address.lower() in confirm_email["To"])
        self.assertTrue("submitter@example.com" in confirm_email["To"])
        self.assertFalse("chairs have been copied" in six.text_type(confirm_email))

        confirmation_url = self.extract_confirmation_url(outbox[-1])

        # go to confirm page
        r = self.client.get(confirmation_url)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[type=submit]:contains("Confirm")')), 1)

        # confirm
        mailbox_before = len(outbox)
        r = self.client.post(confirmation_url, {'action':'confirm'})
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, rev)
        new_revision = draft.latest_event()
        self.assertEqual(new_revision.type, "new_revision")
        self.assertEqual(new_revision.by.name, "Submitter Name")

    def test_submit_new_individual_txt(self):
        self.submit_new_individual(["txt"])

    def test_submit_new_individual_xml(self):
        self.submit_new_individual(["xml"])

    def test_submit_new_individual_txt_xml(self):
        self.submit_new_individual(["txt", "xml"])

    def submit_new_individual_logged_in(self, formats):
        # submit new -> supply submitter info -> done

        name = "draft-authorname-testing-logged-in"
        rev = "00"
        group = None

        author = PersonFactory()
        username = author.user.email
        self.client.login(username=username, password=username+"+password")
        
        status_url, author = self.do_submission(name, rev, group, formats, author=author)

        # supply submitter info, then draft should be be ready for email auth
        mailbox_before = len(outbox)
        r = self.supply_extra_metadata(name, status_url, author.name, username, replaces="")

        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "New version accepted")

        self.assertEqual(len(outbox), mailbox_before+2)
        announcement_email = outbox[-2]
        self.assertIn(name, announcement_email["Subject"])
        self.assertIn('I-D Action:', announcement_email["Subject"])
        self.assertIn('i-d-announce', announcement_email["To"])
        notification_email = outbox[-1]
        self.assertIn(name, notification_email["Subject"])
        self.assertIn("New Version Notification", notification_email["Subject"])
        self.assertIn(author.email().address.lower(), notification_email["To"])

        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, rev)
        new_revision = draft.latest_event()
        self.assertEqual(new_revision.type, "new_revision")
        self.assertEqual(new_revision.by.name, author.name)

    def test_submit_new_logged_in_txt(self):
        self.submit_new_individual_logged_in(["txt"])

    def test_submit_new_logged_in_xml(self):
        self.submit_new_individual_logged_in(["xml"])

    def test_submit_update_individual(self):
        IndividualDraftFactory(name='draft-ietf-random-thing', states=[('draft','rfc')], other_aliases=['rfc9999',], pages=5)
        ad=Person.objects.get(user__username='ad')
        # Group of None here does not reflect real individual submissions
        draft = IndividualDraftFactory(group=None, ad = ad, authors=[ad,], notify='aliens@example.mars', pages=5)
        replaces_count = draft.relateddocument_set.filter(relationship_id='replaces').count()
        name = draft.name
        rev = '%02d'%(int(draft.rev)+1)
        status_url, author = self.do_submission(name,rev)
        mailbox_before = len(outbox)
        replaced_alias = draft.docalias.first()
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "author@example.com", replaces=str(replaced_alias.pk))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'cannot replace itself')
        replaced_alias = DocAlias.objects.get(name='draft-ietf-random-thing')
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "author@example.com", replaces=str(replaced_alias.pk))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'cannot replace an RFC')
        replaced_alias.document.set_state(State.objects.get(type='draft-iesg',slug='approved'))
        replaced_alias.document.set_state(State.objects.get(type='draft',slug='active'))
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "author@example.com", replaces=str(replaced_alias.pk))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'approved by the IESG and cannot')
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "author@example.com", replaces='')
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        self.assertEqual(len(outbox), mailbox_before + 1)
        confirmation_url = self.extract_confirmation_url(outbox[-1])
        self.assertFalse("chairs have been copied" in str(outbox[-1]))
        mailbox_before = len(outbox)
        r = self.client.post(confirmation_url, {'action':'confirm'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(outbox), mailbox_before+3)
        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, rev)
        self.assertEqual(draft.relateddocument_set.filter(relationship_id='replaces').count(), replaces_count)
        #
        r = self.client.get(urlreverse('ietf.doc.views_search.recent_drafts'))
        self.assertContains(r, draft.name)
        self.assertContains(r, draft.title)

    def test_submit_cancel_confirmation(self):
        ad=Person.objects.get(user__username='ad')
        # Group of None here does not reflect real individual submissions
        draft = IndividualDraftFactory(group=None, ad = ad, authors=[ad,], notify='aliens@example.mars', pages=5)
        name = draft.name
        old_rev = draft.rev
        rev = '%02d'%(int(draft.rev)+1)
        status_url, author = self.do_submission(name, rev)
        mailbox_before = len(outbox)
        r = self.supply_extra_metadata(name, status_url, "Submitter Name", "author@example.com", replaces='')
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        self.assertEqual(len(outbox), mailbox_before + 1)
        confirmation_url = self.extract_confirmation_url(outbox[-1])
        mailbox_before = len(outbox)
        r = self.client.post(confirmation_url, {'action':'cancel'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(outbox), mailbox_before)
        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, old_rev)

    def test_submit_new_wg_with_dash(self):
        group = Group.objects.create(acronym="mars-special", name="Mars Special", type_id="wg", state_id="active")

        name = "draft-ietf-%s-testing-tests" % group.acronym

        self.do_submission(name, "00")

        self.assertEqual(Submission.objects.get(name=name).group.acronym, group.acronym)

    def test_submit_new_irtf(self):
        group = Group.objects.create(acronym="saturnrg", name="Saturn", type_id="rg", state_id="active")

        name = "draft-irtf-%s-testing-tests" % group.acronym

        self.do_submission(name, "00")

        self.assertEqual(Submission.objects.get(name=name).group.acronym, group.acronym)
        self.assertEqual(Submission.objects.get(name=name).group.type_id, group.type_id)

    def test_submit_new_iab(self):
        name = "draft-iab-testing-tests"

        self.do_submission(name, "00")

        self.assertEqual(Submission.objects.get(name=name).group.acronym, "iab")

    def test_cancel_submission(self):
        # submit -> cancel
        GroupFactory(acronym='mars')

        name = "draft-ietf-mars-testing-tests"
        rev = "00"

        status_url, author = self.do_submission(name, rev)

        # check we got cancel button
        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        cancel_button = q('[type=submit]:contains("Cancel")')
        self.assertEqual(len(cancel_button), 1)

        action = cancel_button.parents("form").find('input[type=hidden][name="action"]').val()

        # cancel
        r = self.client.post(status_url, dict(action=action))
        self.assertTrue(not os.path.exists(os.path.join(self.staging_dir, "%s-%s.txt" % (name, rev))))

    def test_edit_submission_and_force_post(self):
        # submit -> edit
        draft = WgDraftFactory(group__acronym='mars')

        name = "draft-ietf-mars-testing-tests"
        rev = "00"

        status_url, author = self.do_submission(name, rev)

        # check we have edit button
        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        adjust_button = q('[type=submit]:contains("Adjust")')
        self.assertEqual(len(adjust_button), 1)

        action = adjust_button.parents("form").find('input[type=hidden][name="action"]').val()

        # go to edit, we do this by posting, slightly weird
        r = self.client.post(status_url, dict(action=action))
        self.assertEqual(r.status_code, 302)
        edit_url = r['Location']

        # check page
        r = self.client.get(edit_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('input[name=edit-title]')), 1)

        # edit
        mailbox_before = len(outbox)
        # FIXME If this test is started before midnight, and ends after, it will fail
        document_date = datetime.date.today() - datetime.timedelta(days=-3)
        r = self.client.post(edit_url, {
            "edit-title": "some title",
            "edit-rev": "00",
            "edit-document_date": document_date.strftime("%Y-%m-%d"),
            "edit-abstract": "some abstract",
            "edit-pages": "123",
            "submitter-name": "Some Random Test Person",
            "submitter-email": "random@example.com",
            "replaces": str(draft.docalias.first().pk),
            "edit-note": "no comments",
            "authors-0-name": "Person 1",
            "authors-0-email": "person1@example.com",
            "authors-1-name": "Person 2",
            "authors-1-email": "person2@example.com",
            "authors-2-name": "Person 3",
            "authors-2-email": "person3@example.com",
            "authors-prefix": ["authors-", "authors-0", "authors-1", "authors-2"],
        })
        self.assertNoFormPostErrors(r, ".has-error,.alert-danger")

        submission = Submission.objects.get(name=name)
        self.assertEqual(submission.title, "some title")
        self.assertEqual(submission.document_date, document_date)
        self.assertEqual(submission.abstract, "some abstract")
        self.assertEqual(submission.pages, 123)
        self.assertEqual(submission.note, "no comments")
        self.assertEqual(submission.submitter, "Some Random Test Person <random@example.com>")
        self.assertEqual(submission.replaces, draft.docalias.first().name)
        self.assertEqual(submission.state_id, "manual")

        authors = submission.authors
        self.assertEqual(len(authors), 3)
        self.assertEqual(authors[0]["name"], "Person 1")
        self.assertEqual(authors[0]["email"], "person1@example.com")
        self.assertEqual(authors[1]["name"], "Person 2")
        self.assertEqual(authors[1]["email"], "person2@example.com")
        self.assertEqual(authors[2]["name"], "Person 3")
        self.assertEqual(authors[2]["email"], "person3@example.com")

        self.assertEqual(len(outbox), mailbox_before + 1)
        self.assertTrue("Manual Post Requested" in outbox[-1]["Subject"])
        self.assertTrue(name in outbox[-1]["Subject"])

        # as Secretariat, we should see the force post button
        self.client.login(username="secretary", password="secretary+password")

        r = self.client.get(status_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        post_button = q('[type=submit]:contains("Force")')
        self.assertEqual(len(post_button), 1)

        action = post_button.parents("form").find('input[type=hidden][name="action"]').val()

        # force post
        mailbox_before = len(outbox)
        r = self.client.post(status_url, dict(action=action))
        self.assertEqual(r.status_code, 302)

        draft = Document.objects.get(docalias__name=name)
        self.assertEqual(draft.rev, rev)

    def test_search_for_submission_and_edit_as_secretariat(self):
        # submit -> edit
        GroupFactory(acronym='mars')

        name = "draft-ietf-mars-testing-tests"
        rev = "00"

        self.do_submission(name, rev)

        # search status page
        r = self.client.get(urlreverse("ietf.submit.views.search_submission"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "submission status")

        # search
        r = self.client.post(urlreverse("ietf.submit.views.search_submission"), dict(name=name))
        self.assertEqual(r.status_code, 302)
        unprivileged_status_url = r['Location']

        # search with rev
        r = self.client.post(urlreverse("ietf.submit.views.search_submission"), dict(name=name+'-'+rev))
        self.assertEqual(r.status_code, 302)
        unprivileged_status_url = r['Location']

        # status page as unpriviliged => no edit button
        r = self.client.get(unprivileged_status_url)
        self.assertContains(r, "Submission status of %s" % name)
        q = PyQuery(r.content)
        adjust_button = q('[type=submit]:contains("Adjust")')
        self.assertEqual(len(adjust_button), 0)

        # as Secretariat, we should get edit button
        self.client.login(username="secretary", password="secretary+password")
        r = self.client.get(unprivileged_status_url)
        q = PyQuery(r.content)
        adjust_button = q('[type=submit]:contains("Adjust")')
        self.assertEqual(len(adjust_button), 1)

        action = adjust_button.parents("form").find('input[type=hidden][name="action"]').val()

        # go to edit, we do this by posting, slightly weird
        r = self.client.post(unprivileged_status_url, dict(action=action))
        self.assertEqual(r.status_code, 302)
        edit_url = r['Location']

        # check page
        r = self.client.get(edit_url)
        self.assertEqual(r.status_code, 200)

    def test_request_full_url(self):
        # submit -> request full URL to be sent
        GroupFactory(acronym='mars')

        name = "draft-ietf-mars-testing-tests"
        rev = "00"

        self.do_submission(name, rev)

        submission = Submission.objects.get(name=name)
        url = urlreverse('ietf.submit.views.submission_status', kwargs=dict(submission_id=submission.pk))

        # check we got request full URL button
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        request_button = q('[type=submit]:contains("Request full access")')
        self.assertEqual(len(request_button), 1)

        # request URL to be sent
        mailbox_before = len(outbox)

        action = request_button.parents("form").find('input[type=hidden][name="action"]').val()
        r = self.client.post(url, dict(action=action))
        self.assertEqual(r.status_code, 200)

        self.assertEqual(len(outbox), mailbox_before + 1)
        self.assertTrue("Full URL for managing submission" in outbox[-1]["Subject"])
        self.assertTrue(name in outbox[-1]["Subject"])

        # This could use a test on an 01 from a new author to make sure the logic on
        # who gets the management url behaves as expected

    def test_submit_all_file_types(self):
        GroupFactory(acronym='mars')
        name = "draft-ietf-mars-testing-tests"
        rev = "00"
        group = "mars"

        self.do_submission(name, rev, group, ["txt", "xml", "ps", "pdf"])

        self.assertEqual(Submission.objects.filter(name=name).count(), 1)

        self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.txt" % (name, rev))))
        self.assertTrue(name in io.open(os.path.join(self.staging_dir, "%s-%s.txt" % (name, rev))).read())
        self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.xml" % (name, rev))))
        self.assertTrue(name in io.open(os.path.join(self.staging_dir, "%s-%s.xml" % (name, rev))).read())
        self.assertTrue('<?xml version="1.0" encoding="UTF-8"?>' in io.open(os.path.join(self.staging_dir, "%s-%s.xml" % (name, rev))).read())
        self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.pdf" % (name, rev))))
        self.assertTrue('This is PDF' in io.open(os.path.join(self.staging_dir, "%s-%s.pdf" % (name, rev))).read())
        self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.ps" % (name, rev))))
        self.assertTrue('This is PostScript' in io.open(os.path.join(self.staging_dir, "%s-%s.ps" % (name, rev))).read())

    def test_expire_submissions(self):
        s = Submission.objects.create(name="draft-ietf-mars-foo",
                                      group=None,
                                      submission_date=datetime.date.today() - datetime.timedelta(days=10),
                                      rev="00",
                                      state_id="uploaded")

        self.assertEqual(len(expirable_submissions(older_than_days=10)), 0)
        self.assertEqual(len(expirable_submissions(older_than_days=9)), 1)

        s.state_id = "cancel"
        s.save()

        self.assertEqual(len(expirable_submissions(older_than_days=9)), 0)

        s.state_id = "posted"
        s.save()

        self.assertEqual(len(expirable_submissions(older_than_days=9)), 0)

        s.state_id = "uploaded"
        s.save()

        expire_submission(s, by=None)

        self.assertEqual(s.state_id, "cancel")

    def test_help_pages(self):
        r = self.client.get(urlreverse("ietf.submit.views.note_well"))
        self.assertEqual(r.status_code, 200)

        r = self.client.get(urlreverse("ietf.submit.views.tool_instructions"))
        self.assertEqual(r.status_code, 200)
        
    def test_blackout_access(self):
        # get
        url = urlreverse('ietf.submit.views.upload_submission')

        # Put today in the blackout period
        meeting = Meeting.get_current_meeting()
        meeting.importantdate_set.create(name_id='idcutoff',date=datetime.date.today()-datetime.timedelta(days=2))
        
        # regular user, no access
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('input[type=file][name=txt]')), 0)
        
        # Secretariat has access
        self.client.login(username="secretary", password="secretary+password")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('input[type=file][name=txt]')), 1)

    def submit_bad_file(self, name, formats):
        rev = ""
        group = None

        # break early in case of missing configuration
        self.assertTrue(os.path.exists(settings.IDSUBMIT_IDNITS_BINARY))

        # get
        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)

        # submit
        files = {}
        for format in formats:
            files[format], author = submission_file(name, rev, group, "bad", "test_submission.bad")

        r = self.client.post(url, files)

        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q("form .has-error")) > 0)
        m = q('div.has-error div.alert').text()

        return r, q, m
        
    def test_submit_bad_file_txt(self):
        r, q, m = self.submit_bad_file("some name", ["txt"])
        self.assertIn('Invalid characters were found in the name', m)
        self.assertIn('Expected the TXT file to have extension ".txt"', m)
        self.assertIn('Expected an TXT file of type "text/plain"', m)
        self.assertIn('document does not contain a legitimate name', m)

    def test_submit_bad_file_xml(self):
        r, q, m = self.submit_bad_file("some name", ["xml"])
        self.assertIn('Invalid characters were found in the name', m)
        self.assertIn('Expected the XML file to have extension ".xml"', m)
        self.assertIn('Expected an XML file of type "application/xml"', m)

    def test_submit_bad_file_pdf(self):
        r, q, m = self.submit_bad_file("some name", ["pdf"])
        self.assertIn('Invalid characters were found in the name', m)
        self.assertIn('Expected the PDF file to have extension ".pdf"', m)
        self.assertIn('Expected an PDF file of type "application/pdf"', m)

    def test_submit_bad_file_ps(self):
        r, q, m = self.submit_bad_file("some name", ["ps"])
        self.assertIn('Invalid characters were found in the name', m)
        self.assertIn('Expected the PS file to have extension ".ps"', m)
        self.assertIn('Expected an PS file of type "application/postscript"', m)

    def test_submit_nonascii_name(self):
        name = "draft-authorname-testing-nonascii"
        rev = "00"
        group = None

        # get
        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)

        # submit
        #author = PersonFactory(name=u"Jörgen Nilsson".encode('latin1'))
        user = UserFactory(first_name="Jörgen", last_name="Nilsson")
        author = PersonFactory(user=user)

        file, __ = submission_file(name, rev, group, "txt", "test_submission.nonascii", author=author, ascii=False)
        files = {"txt": file }

        r = self.client.post(url, files)
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        q = PyQuery(r.content)
        m = q('p.alert-warning').text()

        self.assertIn('The idnits check returned 1 warning', m)

    def test_submit_missing_author_email(self):
        name = "draft-authorname-testing-noemail"
        rev = "00"
        group = None

        author = PersonFactory()
        for e in author.email_set.all():
            e.delete()

        files = {"txt": submission_file(name, rev, group, "txt", "test_submission.txt", author=author, ascii=True)[0] }

        # submit
        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.post(url, files)
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        q = PyQuery(r.content)
        m = q('p.text-danger').text()

        self.assertIn('Author email error', m)
        self.assertIn('Found no email address.', m)

    def test_submit_bad_author_email(self):
        name = "draft-authorname-testing-bademail"
        rev = "00"
        group = None

        author = PersonFactory()
        email = author.email_set.first()
        email.address = '@bad.email'
        email.save()

        files = {"xml": submission_file(name, rev, group, "xml", "test_submission.xml", author=author, ascii=False)[0] }

        # submit
        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.post(url, files)
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        q = PyQuery(r.content)
        m = q('p.text-danger').text()

        self.assertIn('Author email error', m)
        self.assertIn('Invalid email address.', m)

    def test_submit_invalid_yang(self):
        name = "draft-yang-testing-invalid"
        rev = "00"
        group = None

        # submit
        files = {"txt": submission_file(name, rev, group, "txt", "test_submission_invalid_yang.txt")[0] }

        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.post(url, files)
        self.assertEqual(r.status_code, 302)
        status_url = r["Location"]
        r = self.client.get(status_url)
        q = PyQuery(r.content)
        #
        self.assertContains(r, 'The yang validation returned 1 error')
        #
        m = q('#yang-validation-message').text()
        for command in ['xym', 'pyang', 'yanglint']:
            version = VersionInfo.objects.get(command=command).version
            if command != 'yanglint' or settings.SUBMIT_YANGLINT_COMMAND:
                self.assertIn(version, m)
        self.assertIn("draft-yang-testing-invalid-00.txt", m)
        self.assertIn("error: syntax error: illegal keyword: ;", m)
        if settings.SUBMIT_YANGLINT_COMMAND:
            self.assertIn("No validation errors", m)


class ApprovalsTestCase(TestCase):
    def test_approvals(self):
        RoleFactory(name_id='chair', group__acronym='mars', person__user__username='marschairman')
        url = urlreverse('ietf.submit.views.approvals')
        self.client.login(username="marschairman", password="marschairman+password")

        Preapproval.objects.create(name="draft-ietf-mars-foo", by=Person.objects.get(user__username="marschairman"))
        Preapproval.objects.create(name="draft-ietf-mars-baz", by=Person.objects.get(user__username="marschairman"))

        Submission.objects.create(name="draft-ietf-mars-foo",
                                  group=Group.objects.get(acronym="mars"),
                                  submission_date=datetime.date.today(),
                                  rev="00",
                                  state_id="posted")
        Submission.objects.create(name="draft-ietf-mars-bar",
                                  group=Group.objects.get(acronym="mars"),
                                  submission_date=datetime.date.today(),
                                  rev="00",
                                  state_id="grp-appr")

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)

        self.assertEqual(len(q('.approvals a:contains("draft-ietf-mars-foo")')), 0)
        self.assertEqual(len(q('.approvals a:contains("draft-ietf-mars-bar")')), 1)
        self.assertEqual(len(q('.preapprovals td:contains("draft-ietf-mars-foo")')), 0)
        self.assertEqual(len(q('.preapprovals td:contains("draft-ietf-mars-baz")')), 1)
        self.assertEqual(len(q('.recently-approved a:contains("draft-ietf-mars-foo")')), 1)

    def test_add_preapproval(self):
        RoleFactory(name_id='chair', group__acronym='mars', person__user__username='marschairman')

        url = urlreverse('ietf.submit.views.add_preapproval')
        login_testing_unauthorized(self, "marschairman", url)

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[type=submit]:contains("Save")')), 1)

        # faulty post
        r = self.client.post(url, dict(name="draft-test-nonexistingwg-something"))
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q("form .has-error")) > 0)

        # add
        name = "draft-ietf-mars-foo"
        r = self.client.post(url, dict(name=name))
        self.assertEqual(r.status_code, 302)

        self.assertEqual(len(Preapproval.objects.filter(name=name)), 1)

    def test_cancel_preapproval(self):
        RoleFactory(name_id='chair', group__acronym='mars', person__user__username='marschairman')

        preapproval = Preapproval.objects.create(name="draft-ietf-mars-foo", by=Person.objects.get(user__username="marschairman"))

        url = urlreverse('ietf.submit.views.cancel_preapproval', kwargs=dict(preapproval_id=preapproval.pk))
        login_testing_unauthorized(self, "marschairman", url)

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[type=submit]:contains("Cancel")')), 1)

        # cancel
        r = self.client.post(url, dict(action="cancel"))
        self.assertEqual(r.status_code, 302)

        self.assertEqual(len(Preapproval.objects.filter(name=preapproval.name)), 0)

class ManualPostsTestCase(TestCase):
    def test_manual_posts(self):
        GroupFactory(acronym='mars')

        url = urlreverse('ietf.submit.views.manualpost')
        # Secretariat has access
        self.client.login(username="secretary", password="secretary+password")

        Submission.objects.create(name="draft-ietf-mars-foo",
                                  group=Group.objects.get(acronym="mars"),
                                  submission_date=datetime.date.today(),
                                  state_id="manual")
        Submission.objects.create(name="draft-ietf-mars-bar",
                                  group=Group.objects.get(acronym="mars"),
                                  submission_date=datetime.date.today(),
                                  rev="00",
                                  state_id="grp-appr")

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)

        self.assertEqual(len(q('.submissions a:contains("draft-ietf-mars-foo")')), 1)
        self.assertEqual(len(q('.submissions a:contains("draft-ietf-mars-bar")')), 0)

    def test_waiting_for_draft(self):
        message_string = """To: somebody@ietf.org
From: joe@test.com
Date: {}
Subject: test submission via email

Please submit my draft at http://test.com/mydraft.txt

Thank you
""".format(datetime.datetime.now().ctime())
        message = email.message_from_string(force_str(message_string))
        submission, submission_email_event = (
            add_submission_email(request=None,
                                 remote_ip ="192.168.0.1",
                                 name = "draft-my-new-draft",
                                 rev='00',
                                 submission_pk=None,
                                 message = message,
                                 by = Person.objects.get(name="(System)"),
                                 msgtype = "msgin") )

        url = urlreverse('ietf.submit.views.manualpost')
        # Secretariat has access
        self.client.login(username="secretary", password="secretary+password")

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)

        self.assertEqual(len(q('.waiting-for-draft a:contains("draft-my-new-draft")')), 1)

        # Same name should raise an error
        with self.assertRaises(Exception):
            add_submission_email(request=None,
                                 remote_ip ="192.168.0.1",
                                 name = "draft-my-new-draft",
                                 rev='00',
                                 submission_pk=None,
                                 message = message,
                                 by = Person.objects.get(name="(System)"),
                                 msgtype = "msgin")

        # Cancel this one
        r = self.client.post(urlreverse("ietf.submit.views.cancel_waiting_for_draft"), {
            "submission_id": submission.pk,
            "access_token": submission.access_token(),
        })
        self.assertEqual(r.status_code, 302)
        url = r["Location"]
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('.waiting-for-draft a:contains("draft-my-new-draft")')), 0)

        # Should now be able to add it again
        submission, submission_email_event = (
            add_submission_email(request=None,
                                 remote_ip ="192.168.0.1",
                                 name = "draft-my-new-draft",
                                 rev='00',
                                 submission_pk=None,
                                 message = message,
                                 by = Person.objects.get(name="(System)"),
                                 msgtype = "msgin") )


    def test_waiting_for_draft_with_attachment(self):
        frm = "joe@test.com"
        
        message_string = """To: somebody@ietf.org
From: {}
Date: {}
Subject: A very important message with a small attachment
Content-Type: multipart/mixed; boundary="------------090908050800030909090207"

This is a multi-part message in MIME format.
--------------090908050800030909090207
Content-Type: text/plain; charset=utf-8; format=flowed
Content-Transfer-Encoding: 7bit

The message body will probably say something about the attached document

--------------090908050800030909090207
Content-Type: text/plain; charset=UTF-8; name="attach.txt"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="attach.txt"

QW4gZXhhbXBsZSBhdHRhY2htZW50IHd0aG91dCB2ZXJ5IG11Y2ggaW4gaXQuCgpBIGNvdXBs
ZSBvZiBsaW5lcyAtIGJ1dCBpdCBjb3VsZCBiZSBhIGRyYWZ0Cg==
--------------090908050800030909090207--
""".format(frm, datetime.datetime.now().ctime())

        message = email.message_from_string(force_str(message_string))
        submission, submission_email_event = (
            add_submission_email(request=None,
                                 remote_ip ="192.168.0.1",
                                 name = "draft-my-new-draft",
                                 rev='00',
                                 submission_pk=None,
                                 message = message,
                                 by = Person.objects.get(name="(System)"),
                                 msgtype = "msgin") )

        manualpost_page_url = urlreverse('ietf.submit.views.manualpost')
        # Secretariat has access
        self.client.login(username="secretary", password="secretary+password")

        self.check_manualpost_page(submission=submission, 
                                   submission_email_event=submission_email_event,
                                   the_url=manualpost_page_url, 
                                   submission_name_fragment='draft-my-new-draft',
                                   frm=frm,
                                   is_secretariat=True)
 
        # Try the status page with no credentials
        self.client.logout()

        self.check_manualpost_page(submission=submission, 
                                   submission_email_event=submission_email_event,
                                   the_url=manualpost_page_url, 
                                   submission_name_fragment='draft-my-new-draft',
                                   frm=frm,
                                   is_secretariat=False)
        
        # Post another message to this submission using the link
        message_string = """To: somebody@ietf.org
From: joe@test.com
Date: {}
Subject: A new submission message with a small attachment
Content-Type: multipart/mixed; boundary="------------090908050800030909090207"

This is a multi-part message in MIME format.
--------------090908050800030909090207
Content-Type: text/plain; charset=utf-8; format=flowed
Content-Transfer-Encoding: 7bit

The message body will probably say something more about the attached document

--------------090908050800030909090207
Content-Type: text/plain; charset=UTF-8; name="attach.txt"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="attachment.txt"

QW4gZXhhbXBsZSBhdHRhY2htZW50IHd0aG91dCB2ZXJ5IG11Y2ggaW4gaXQuCgpBIGNvdXBs
ZSBvZiBsaW5lcyAtIGJ1dCBpdCBjb3VsZCBiZSBhIGRyYWZ0Cg==
--------------090908050800030909090207--
""".format(datetime.datetime.now().ctime())

        # Back to secretariat
        self.client.login(username="secretary", password="secretary+password")

        r, q = self.request_and_parse(manualpost_page_url)

        url = self.get_href(q, "a#new-submission-email:contains('New submission from email')")

        # Get the form
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        #self.assertEqual(len(q('input[name=edit-title]')), 1)

        # Post the new message
        r = self.client.post(url, {
            "name": "draft-my-next-new-draft-00",
            "direction": "incoming",
            "message": message_string,
        })

        if r.status_code != 302:
            q = PyQuery(r.content)
            print(q)

        self.assertEqual(r.status_code, 302)
        

        #self.check_manualpost_page(submission, submission_email_event,
        #                        url, 'draft-my-next-new-draft'
        #                        'Another very important message',
        #                        true)

    def check_manualpost_page(self, submission, submission_email_event,
                              the_url, submission_name_fragment,
                              frm,
                              is_secretariat):
        # get the page listing manual posts
        r, q = self.request_and_parse(the_url)
        selector = "#waiting-for-draft a#add-submission-email%s:contains('Add email')" % submission.pk

        if is_secretariat:
            # Can add an email to the submission
            add_email_url = self.get_href(q, selector)
        else:
            # No add email button button
            self.assertEqual(len(q(selector)), 0)

        # Find the link for our submission in those awaiting drafts
        submission_url = self.get_href(q, "#waiting-for-draft a#aw{}:contains('{}')".
                                       format(submission.pk, submission_name_fragment))

        # Follow the link to the status page for this submission
        r, q = self.request_and_parse(submission_url)
        
        selector = "#history a#reply%s:contains('Reply')" % submission.pk

        if is_secretariat:
            # check that reply button is visible and get the form
            reply_url = self.get_href(q, selector)

            # Get the form
            r = self.client.get(reply_url)
            self.assertEqual(r.status_code, 200)
            reply_q = PyQuery(r.content)
            self.assertEqual(len(reply_q('input[name=to]')), 1)
        else:
            # No reply button
            self.assertEqual(len(q(selector)), 0)

        if is_secretariat:
            # Now try to send an email using the send email link
    
            selector = "a#send%s:contains('Send Email')" % submission.pk
            send_url = self.get_href(q, selector)

            self.do_submission_email(the_url = send_url,
                                     to = frm,
                                     body = "A new message")

        # print q
        # print submission.pk
        # print submission_email_event.pk
        
        # Find the link for our message in the list
        url = self.get_href(q, "#aw{}-{}:contains('{}')".format(submission.pk, 
                                                                submission_email_event.message.pk,
                                                                "Received message - manual post"))
        
        # Page displaying message details
        r, q = self.request_and_parse(url)
        
        if is_secretariat:
            # check that reply button is visible

            reply_href = self.get_href(q, "#email-details a#reply%s:contains('Reply')" % submission.pk)

        else:
            # No reply button
            self.assertEqual(len(q(selector)), 0)
            reply_href = None

        # check that attachment link is visible

        url = self.get_href(q, "#email-details a#attach{}:contains('attach.txt')".format(submission.pk))

        # Fetch the attachment
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        
        # Attempt a reply if we can
        if reply_href == None:
            return

        self.do_submission_email(the_url = reply_href,
                                 to = frm,
                                 body = "A reply to the message")
        
        # try adding an email to the submission
        # Use the add email link from the manual post listing page

        if is_secretariat:
            # Can add an email to the submission
            # add_email_url set previously
            r = self.client.get(add_email_url)
            self.assertEqual(r.status_code, 200)
            add_email_q = PyQuery(r.content)
            self.assertEqual(len(add_email_q('input[name=submission_pk]')), 1)

            # Add a simple email
            new_message_string = """To: somebody@ietf.org
From: joe@test.com
Date: {}
Subject: Another message

About my submission

Thank you
""".format(datetime.datetime.now().ctime())

            r = self.client.post(add_email_url, {
                "name": "{}-{}".format(submission.name, submission.rev),
                "direction": "incoming",
                "submission_pk": submission.pk,
                "message": new_message_string,
            })

            if r.status_code != 302:
                q = PyQuery(r.content)
                print(q)

            self.assertEqual(r.status_code, 302)

    def request_and_parse(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return r, PyQuery(r.content)

        
    def get_href(self, q, query):
        link = q(query)
        self.assertEqual(len(link), 1)

        return PyQuery(link[0]).attr('href')


    def do_submission_email(self, the_url, to, body):
        # check the page
        r = self.client.get(the_url)
        q = PyQuery(r.content)
        post_button = q('[type=submit]:contains("Send Email")')
        self.assertEqual(len(post_button), 1)
        action = post_button.parents("form").find('input[type=hidden][name="action"]').val()
        subject = post_button.parents("form").find('input[name="subject"]').val()
        frm = post_button.parents("form").find('input[name="frm"]').val()
        cc = post_button.parents("form").find('input[name="cc"]').val()
        reply_to = post_button.parents("form").find('input[name="reply_to"]').val()

        empty_outbox()
        
        # post submitter info
        r = self.client.post(the_url, {
            "action": action,
            "subject": subject,
            "frm": frm,
            "to": to,
            "cc": cc,
            "reply_to": reply_to,
            "body": body,
        })

        self.assertEqual(r.status_code, 302)

        self.assertEqual(len(outbox), 1)

        outmsg = outbox[0]
        self.assertTrue(to in outmsg['To'])
        
        reply_to = outmsg['Reply-To']
        self.assertIsNotNone(reply_to, "Expected Reply-To")
        
        # Build a reply

        message_string = """To: {}
From: {}
Date: {}
Subject: test
""".format(reply_to, to, datetime.datetime.now().ctime())

        result = process_response_email(message_string)
        self.assertIsInstance(result, Message)

        return r

    def do_submission(self, name, rev, group=None, formats=["txt",]):
        # We're not testing the submission process - just the submission status 

        # get
        url = urlreverse('ietf.submit.views.upload_submission')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('input[type=file][name=txt]')), 1)
        self.assertEqual(len(q('input[type=file][name=xml]')), 1)

        # submit
        files = {}
        for format in formats:
            files[format], author = submission_file(name, rev, group, format, "test_submission.%s" % format)

        r = self.client.post(url, files)
        if r.status_code != 302:
            q = PyQuery(r.content)
            print(q('div.has-error span.help-block div').text())

        self.assertEqual(r.status_code, 302)

        status_url = r["Location"]
        for format in formats:
            self.assertTrue(os.path.exists(os.path.join(self.staging_dir, "%s-%s.%s" % (name, rev, format))))
        self.assertEqual(Submission.objects.filter(name=name).count(), 1)
        submission = Submission.objects.get(name=name)
        self.assertTrue(all([ c.passed!=False for c in submission.checks.all() ]))
        self.assertEqual(len(submission.authors), 1)
        author = submission.authors[0]
        self.assertEqual(author["name"], "Author Name")
        self.assertEqual(author["email"], "author@example.com")

        return status_url


    def supply_extra_metadata(self, name, status_url, submitter_name, submitter_email):
        # check the page
        r = self.client.get(status_url)
        q = PyQuery(r.content)
        post_button = q('[type=submit]:contains("Post")')
        self.assertEqual(len(post_button), 1)
        action = post_button.parents("form").find('input[type=hidden][name="action"]').val()

        # post submitter info
        r = self.client.post(status_url, {
            "action": action,
            "submitter-name": submitter_name,
            "submitter-email": submitter_email,
            "approvals_received": True,
        })

        if r.status_code == 302:
            submission = Submission.objects.get(name=name)
            self.assertEqual(submission.submitter, email.utils.formataddr((submitter_name, submitter_email)))

        return r

class ApiSubmitTests(TestCase):
    def setUp(self):
        # break early in case of missing configuration
        self.assertTrue(os.path.exists(settings.IDSUBMIT_IDNITS_BINARY))

        self.saved_idsubmit_staging_path = settings.IDSUBMIT_STAGING_PATH
        self.staging_dir = self.tempdir('submit-staging')
        settings.IDSUBMIT_STAGING_PATH = self.staging_dir

        self.saved_internet_draft_path = settings.INTERNET_DRAFT_PATH
        self.saved_idsubmit_repository_path = settings.IDSUBMIT_REPOSITORY_PATH
        self.repository_dir = self.tempdir('submit-repository')
        settings.INTERNET_DRAFT_PATH = settings.IDSUBMIT_REPOSITORY_PATH = self.repository_dir

        self.saved_archive_dir = settings.INTERNET_DRAFT_ARCHIVE_DIR
        self.archive_dir = self.tempdir('submit-archive')
        settings.INTERNET_DRAFT_ARCHIVE_DIR = self.archive_dir

        MeetingFactory(type_id='ietf', date=datetime.date.today()+datetime.timedelta(days=60))

    def tearDown(self):
        shutil.rmtree(self.staging_dir)
        shutil.rmtree(self.repository_dir)
        shutil.rmtree(self.archive_dir)
        settings.IDSUBMIT_STAGING_PATH = self.saved_idsubmit_staging_path
        settings.INTERNET_DRAFT_PATH = self.saved_internet_draft_path
        settings.IDSUBMIT_REPOSITORY_PATH = self.saved_idsubmit_repository_path
        settings.INTERNET_DRAFT_ARCHIVE_DIR = self.saved_archive_dir

    def post_submission(self, rev, author=None, name=None, group=None, email=None, title=None, year=None):
        url = urlreverse('ietf.submit.views.api_submit')
        if author is None:
            author = PersonFactory()
        if name is None:
            slug = re.sub('[^a-z0-9-]+', '', author.ascii_parts()[3].lower())
            name = 'draft-%s-foo' % slug
        if email is None:
            email = author.user.username
        # submit
        data = {}
        data['xml'], author = submission_file(name, rev, group, 'xml', "test_submission.xml", author=author, email=email, title=title, year=year)
        data['user'] = email
        r = self.client.post(url, data)
        return r, author, name

    def test_api_submit_info(self):
        url = urlreverse('ietf.submit.views.api_submit')
        r = self.client.get(url)
        expected = "A simplified draft submission interface, intended for automation"
        self.assertContains(r, expected, status_code=200)

    def test_api_submit_bad_method(self):
        url = urlreverse('ietf.submit.views.api_submit')
        r = self.client.put(url)
        self.assertEqual(r.status_code, 405)

    def test_api_submit_ok(self):
        r, author, name = self.post_submission('00')
        expected = "Upload of %s OK, confirmation requests sent to:\n  %s" % (name, author.formatted_email().replace('\n',''))
        self.assertContains(r, expected, status_code=200)

    def test_api_submit_no_user(self):
        email='nonexistant.user@example.org'
        r, author, name = self.post_submission('00', email=email)
        expected = "No such user: %s" % email
        self.assertContains(r, expected, status_code=400)

    def test_api_submit_no_person(self):
        user = UserFactory()
        email = user.username
        r, author, name = self.post_submission('00', email=email)
        expected = "No person with username %s" % email
        self.assertContains(r, expected, status_code=400)

    def test_api_submit_wrong_revision(self):
        r, author, name = self.post_submission('01')
        expected = "Invalid revision (revision 00 is expected)"
        self.assertContains(r, expected, status_code=400)

    def test_api_submit_pending_submission(self):
        r, author, name = self.post_submission('00')
        expected = "Upload of"
        self.assertContains(r, expected, status_code=200)
        r, author, name = self.post_submission('00', author=author, name=name)
        expected = "A submission with same name and revision is currently being processed"
        self.assertContains(r, expected, status_code=400)

    def test_api_submit_no_title(self):
        r, author, name = self.post_submission('00', title="")
        expected = "Could not extract a valid title from the upload"
        self.assertContains(r, expected, status_code=400)

    def test_api_submit_failed_idnits(self):
        r, author, name = self.post_submission('00', year="2010")
        expected = "Document date must be within 3 days of submission date"
        self.assertContains(r, expected, status_code=400)

class RefsTests(TestCase):

    def test_draft_refs_identification(self):

        group = None
        file, __ = submission_file('draft-some-subject', '00', group, 'txt', "test_submission.txt", )
        draft = Draft(file.read(), file.name)
        refs = draft.get_refs()
        self.assertEqual(refs['rfc2119'], 'norm')
        self.assertEqual(refs['rfc8174'], 'norm')
        self.assertEqual(refs['rfc8126'], 'info')
        self.assertEqual(refs['rfc8175'], 'info')
        
