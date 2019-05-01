# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import datetime
import json
from pyquery import PyQuery
from StringIO import StringIO
from django.urls import reverse as urlreverse

import debug                            # pyflakes:ignore

from ietf.community.models import CommunityList
from ietf.group.factories import RoleFactory
from ietf.nomcom.models import NomCom
from ietf.nomcom.test_data import nomcom_test_data
from ietf.nomcom.factories import NomComFactory, NomineeFactory, NominationFactory, FeedbackFactory, PositionFactory
from ietf.person.factories import EmailFactory, PersonFactory, UserFactory
from ietf.person.models import Person, Alias
from ietf.person.utils import (merge_persons, determine_merge_order, send_merge_notification,
    handle_users, get_extra_primary, dedupe_aliases, move_related_objects, merge_nominees, merge_users)
from ietf.utils.test_utils import TestCase, login_testing_unauthorized
from ietf.utils.mail import outbox, empty_outbox


def get_person_no_user():
    person = PersonFactory()
    person.user = None
    person.save()
    return person


class PersonTests(TestCase):
    def test_ajax_search_emails(self):
        person = PersonFactory()

        r = self.client.get(urlreverse("ietf.person.views.ajax_select2_search", kwargs={ "model_name": "email"}), dict(q=person.name))
        self.assertEqual(r.status_code, 200)
        data = json.loads(r.content)
        self.assertEqual(data[0]["id"], person.email_address())

    def test_default_email(self):
        person = PersonFactory()
        primary = EmailFactory(person=person, primary=True, active=True)
        EmailFactory(person=person, primary=False, active=True)
        EmailFactory(person=person, primary=False, active=False)
        self.assertTrue(primary.address in person.formatted_email())

    def test_profile(self):
        person = PersonFactory(with_bio=True)
        
        self.assertTrue(person.photo is not None)
        self.assertTrue(person.photo.name is not None)

        url = urlreverse("ietf.person.views.profile", kwargs={ "email_or_name": person.plain_name()})
        r = self.client.get(url)
        #debug.show('person.name')
        #debug.show('person.plain_name()')
        #debug.show('person.photo_name()')
        self.assertContains(r, person.photo_name(), status_code=200)
        q = PyQuery(r.content)
        self.assertIn("Photo of %s"%person, q("div.bio-text img.bio-photo").attr("alt"))

        bio_text  = q("div.bio-text").text()
        self.assertIsNotNone(bio_text)

        photo_url = q("div.bio-text img.bio-photo").attr("src")
        r = self.client.get(photo_url)
        self.assertEqual(r.status_code, 200)

    def test_name_methods(self):
        person = PersonFactory(name=u"Dr. Jens F. Möller", )

        self.assertEqual(person.name, u"Dr. Jens F. Möller" )
        self.assertEqual(person.ascii_name(), u"Dr. Jens F. Moller" )
        self.assertEqual(person.plain_name(), u"Jens Möller" )
        self.assertEqual(person.plain_ascii(), u"Jens Moller" )
        self.assertEqual(person.initials(), u"J. F.")
        self.assertEqual(person.first_name(), u"Jens" )
        self.assertEqual(person.last_name(), u"Möller" )

        person = PersonFactory(name=u"吴建平")
        # The following are probably incorrect because the given name should
        # be Jianping and the surname should be Wu ...
        # TODO: Figure out better handling for names with CJK characters.
        # Maybe use ietf.person.cjk.*
        self.assertEqual(person.ascii_name(), u"Wu Jian Ping")

    def test_duplicate_person_name(self):
        empty_outbox()
        p = PersonFactory(name="Föö Bär")
        PersonFactory(name=p.name)
        self.assertTrue("possible duplicate" in unicode(outbox[0]["Subject"]).lower())

    def test_merge(self):
        url = urlreverse("ietf.person.views.merge")
        login_testing_unauthorized(self, "secretary", url)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

    def test_merge_with_params(self):
        p1 = get_person_no_user()
        p2 = PersonFactory()
        url = urlreverse("ietf.person.views.merge") + "?source={}&target={}".format(p1.pk, p2.pk)
        login_testing_unauthorized(self, "secretary", url)
        r = self.client.get(url)
        self.assertContains(r, 'retaining login', status_code=200)

    def test_merge_with_params_bad_id(self):
        url = urlreverse("ietf.person.views.merge") + "?source=1000&target=2000"
        login_testing_unauthorized(self, "secretary", url)
        r = self.client.get(url)
        self.assertContains(r, 'ID does not exist', status_code=200)

    def test_merge_post(self):
        p1 = get_person_no_user()
        p2 = PersonFactory()
        url = urlreverse("ietf.person.views.merge")
        expected_url = urlreverse("ietf.secr.rolodex.views.view", kwargs={'id': p2.pk})
        login_testing_unauthorized(self, "secretary", url)
        data = {'source': p1.pk, 'target': p2.pk}
        r = self.client.post(url, data, follow=True)
        self.assertRedirects(r, expected_url)
        self.assertContains(r, 'Merged', status_code=200)
        self.assertFalse(Person.objects.filter(pk=p1.pk))

class PersonUtilsTests(TestCase):
    def test_determine_merge_order(self):
        p1 = get_person_no_user()
        p2 = PersonFactory()
        p3 = get_person_no_user()
        p4 = PersonFactory()

        # target has User
        results = determine_merge_order(p1, p2)
        self.assertEqual(results,(p1,p2))

        # source has User
        results = determine_merge_order(p2, p1)
        self.assertEqual(results,(p1,p2))
        
        # neither have User
        results = determine_merge_order(p1, p3)
        self.assertEqual(results,(p1,p3))

        # both have User
        today = datetime.datetime.today()
        p2.user.last_login = today
        p2.user.save()
        p4.user.last_login = today - datetime.timedelta(days=30)
        p4.user.save()
        results = determine_merge_order(p2, p4)
        self.assertEqual(results,(p4,p2))

    def test_send_merge_notification(self):
        person = PersonFactory()
        len_before = len(outbox)
        send_merge_notification(person,['Record Merged'])
        self.assertEqual(len(outbox),len_before+1)
        self.assertTrue('IETF Datatracker records merged' in outbox[-1]['Subject'])

    def test_handle_users(self):
        source1 = get_person_no_user()
        target1 = get_person_no_user()
        source2 = get_person_no_user()
        target2 = PersonFactory()
        source3 = PersonFactory()
        target3 = get_person_no_user()
        source4 = PersonFactory()
        target4 = PersonFactory()

        # no Users
        result = handle_users(source1, target1)
        self.assertTrue('DATATRACKER LOGIN ACTION: none' in result)

        # target user
        result = handle_users(source2, target2)
        self.assertTrue("DATATRACKER LOGIN ACTION: retaining login {}".format(target2.user) in result)

        # source user
        user = source3.user
        result = handle_users(source3, target3)
        self.assertTrue("DATATRACKER LOGIN ACTION: retaining login {}".format(user) in result)
        self.assertTrue(target3.user == user)

        # both have user
        source_user = source4.user
        target_user = target4.user
        result = handle_users(source4, target4)
        self.assertTrue("DATATRACKER LOGIN ACTION: retaining login: {}, removing login: {}".format(target_user,source_user) in result)
        self.assertTrue(target4.user == target_user)
        self.assertTrue(source4.user == None)

    def test_get_extra_primary(self):
        source = PersonFactory()
        target = PersonFactory()
        extra = get_extra_primary(source, target)
        self.assertTrue(extra == list(source.email_set.filter(primary=True)))

    def test_dedupe_aliases(self):
        person = PersonFactory()
        Alias.objects.create(person=person, name='Joe')
        Alias.objects.create(person=person, name='Joe')
        self.assertEqual(person.alias_set.filter(name='Joe').count(),2)
        dedupe_aliases(person)
        self.assertEqual(person.alias_set.filter(name='Joe').count(),1)
      
    def test_merge_nominees(self):
        nomcom_test_data()
        nomcom = NomCom.objects.first()
        source = PersonFactory()
        source.nominee_set.create(nomcom=nomcom,email=source.email())
        target = PersonFactory()
        merge_nominees(source, target)
        self.assertTrue(target.nominee_set.all())

    def test_move_related_objects(self):
        source = PersonFactory()
        target = PersonFactory()
        source_email = source.email_set.first()
        source_alias = source.alias_set.first()
        move_related_objects(source, target, file=StringIO())
        self.assertTrue(source_email in target.email_set.all())
        self.assertTrue(source_alias in target.alias_set.all())

    def test_merge_persons(self):
        source = PersonFactory()
        target = PersonFactory()
        source_id = source.pk
        source_email = source.email_set.first()
        source_alias = source.alias_set.first()
        source_user = source.user
        merge_persons(source, target, file=StringIO())
        self.assertTrue(source_email in target.email_set.all())
        self.assertTrue(source_alias in target.alias_set.all())
        self.assertFalse(Person.objects.filter(id=source_id))
        self.assertFalse(source_user.is_active)

    def test_merge_users(self):
        person = PersonFactory()
        source = person.user
        target = UserFactory()
        mars = RoleFactory(name_id='chair',group__acronym='mars').group
        communitylist = CommunityList.objects.create(user=source, group=mars)
        nomcom = NomComFactory()
        position = PositionFactory(nomcom=nomcom)
        nominee = NomineeFactory(nomcom=nomcom, person=mars.get_chair().person)
        feedback = FeedbackFactory(user=source, author=person, nomcom=nomcom)
        feedback.nominees.add(nominee)
        nomination = NominationFactory(nominee=nominee, user=source, position=position, comments=feedback)

        merge_users(source, target)
        self.assertIn(communitylist, target.communitylist_set.all())
        self.assertIn(feedback, target.feedback_set.all())
        self.assertIn(nomination, target.nomination_set.all())
