# Copyright The IETF Trust 2017-2019, All Rights Reserved
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import os, shutil, time, datetime
from urlparse import urlsplit
from pyquery import PyQuery
from unittest import skipIf

import django.contrib.auth.views
from django.urls import reverse as urlreverse
from django.contrib.auth.models import User
from django.conf import settings

import debug                            # pyflakes:ignore

from ietf.utils.test_utils import TestCase, login_testing_unauthorized, unicontent
from ietf.utils.mail import outbox, empty_outbox
from ietf.group.models import Group, Role, RoleName
from ietf.group.factories import GroupFactory, RoleFactory
from ietf.ietfauth.htpasswd import update_htpasswd_file
from ietf.mailinglists.models import Subscribed
from ietf.person.models import Person, Email, PersonalApiKey, PERSON_API_KEY_ENDPOINTS
from ietf.person.factories import PersonFactory, EmailFactory
from ietf.review.factories import ReviewRequestFactory, ReviewAssignmentFactory
from ietf.review.models import ReviewWish, UnavailablePeriod
from ietf.utils.decorators import skip_coverage

import ietf.ietfauth.views

if os.path.exists(settings.HTPASSWD_COMMAND):
    skip_htpasswd_command = False
    skip_message = ""
else:
    import sys
    skip_htpasswd_command = True
    skip_message = ("Skipping htpasswd test: The binary for htpasswd wasn't found in the\n       "
                    "location indicated in settings.py.")
    sys.stderr.write("     "+skip_message+'\n')

class IetfAuthTests(TestCase):
    def setUp(self):
        self.saved_use_python_htdigest = getattr(settings, "USE_PYTHON_HTDIGEST", None)
        settings.USE_PYTHON_HTDIGEST = True

        self.saved_htpasswd_file = settings.HTPASSWD_FILE
        self.htpasswd_dir = self.tempdir('htpasswd')
        settings.HTPASSWD_FILE = os.path.join(self.htpasswd_dir, "htpasswd")
        open(settings.HTPASSWD_FILE, 'a').close() # create empty file

        self.saved_htdigest_realm = getattr(settings, "HTDIGEST_REALM", None)
        settings.HTDIGEST_REALM = "test-realm"

    def tearDown(self):
        shutil.rmtree(self.htpasswd_dir)
        settings.USE_PYTHON_HTDIGEST = self.saved_use_python_htdigest
        settings.HTPASSWD_FILE = self.saved_htpasswd_file
        settings.HTDIGEST_REALM = self.saved_htdigest_realm

    def test_index(self):
        self.assertEqual(self.client.get(urlreverse(ietf.ietfauth.views.index)).status_code, 200)

    def test_login_and_logout(self):
        PersonFactory(user__username='plain')

        # try logging in without a next
        r = self.client.get(urlreverse(ietf.ietfauth.views.login))
        self.assertEqual(r.status_code, 200)

        r = self.client.post(urlreverse(ietf.ietfauth.views.login), {"username":"plain", "password":"plain+password"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(urlsplit(r["Location"])[2], urlreverse(ietf.ietfauth.views.profile))

        # try logging out
        r = self.client.get(urlreverse(django.contrib.auth.views.logout))
        self.assertEqual(r.status_code, 200)

        r = self.client.get(urlreverse(ietf.ietfauth.views.profile))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(urlsplit(r["Location"])[2], urlreverse(ietf.ietfauth.views.login))

        # try logging in with a next
        r = self.client.post(urlreverse(ietf.ietfauth.views.login) + "?next=/foobar", {"username":"plain", "password":"plain+password"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(urlsplit(r["Location"])[2], "/foobar")

    def test_login_with_different_email(self):
        person = PersonFactory(user__username='plain')
        email = EmailFactory(person=person)

        # try logging in without a next
        r = self.client.get(urlreverse(ietf.ietfauth.views.login))
        self.assertEqual(r.status_code, 200)

        r = self.client.post(urlreverse(ietf.ietfauth.views.login), {"username":email, "password":"plain+password"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(urlsplit(r["Location"])[2], urlreverse(ietf.ietfauth.views.profile))

    def extract_confirm_url(self, confirm_email):
        # dig out confirm_email link
        msg = confirm_email.get_payload(decode=True)
        line_start = "http"
        confirm_url = None
        for line in msg.split("\n"):
            if line.strip().startswith(line_start):
                confirm_url = line.strip()
        self.assertTrue(confirm_url)

        return confirm_url

    def username_in_htpasswd_file(self, username):
        with open(settings.HTPASSWD_FILE) as f:
            for l in f:
                if l.startswith(username + ":"):
                    return True
        with open(settings.HTPASSWD_FILE) as f:
            print(f.read())

        return False

    def test_create_account_failure(self):

        url = urlreverse(ietf.ietfauth.views.create_account)

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        # register email and verify failure
        email = 'new-account@example.com'
        empty_outbox()
        r = self.client.post(url, { 'email': email })
        self.assertEqual(r.status_code, 200)
        self.assertIn("Account creation failed", unicontent(r))

    def register_and_verify(self, email):
        url = urlreverse(ietf.ietfauth.views.create_account)

        # register email
        empty_outbox()
        r = self.client.post(url, { 'email': email })
        self.assertEqual(r.status_code, 200)
        self.assertIn("Account request received", unicontent(r))
        self.assertEqual(len(outbox), 1)

        # go to confirm page
        confirm_url = self.extract_confirm_url(outbox[-1])
        r = self.client.get(confirm_url)
        self.assertEqual(r.status_code, 200)

        # password mismatch
        r = self.client.post(confirm_url, { 'password': 'secret', 'password_confirmation': 'nosecret' })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.filter(username=email).count(), 0)

        # confirm
        r = self.client.post(confirm_url, { 'name': 'User Name', 'ascii': 'User Name', 'password': 'secret', 'password_confirmation': 'secret' })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.filter(username=email).count(), 1)
        self.assertEqual(Person.objects.filter(user__username=email).count(), 1)
        self.assertEqual(Email.objects.filter(person__user__username=email).count(), 1)

        self.assertTrue(self.username_in_htpasswd_file(email))

    def test_create_whitelisted_account(self):
        email = "new-account@example.com"

        # add whitelist entry
        r = self.client.post(urlreverse(ietf.ietfauth.views.login), {"username":"secretary", "password":"secretary+password"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(urlsplit(r["Location"])[2], urlreverse(ietf.ietfauth.views.profile))

        r = self.client.get(urlreverse(ietf.ietfauth.views.add_account_whitelist))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Add a whitelist entry", unicontent(r))

        r = self.client.post(urlreverse(ietf.ietfauth.views.add_account_whitelist), {"email": email})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Whitelist entry creation successful", unicontent(r))

        # log out
        r = self.client.get(urlreverse(django.contrib.auth.views.logout))
        self.assertEqual(r.status_code, 200)

        # register and verify whitelisted email
        self.register_and_verify(email)


    def test_create_subscribed_account(self):
        # verify creation with email in subscribed list
        saved_delay = settings.LIST_ACCOUNT_DELAY
        settings.LIST_ACCOUNT_DELAY = 1
        email = "subscribed@example.com"
        s = Subscribed(email=email)
        s.save()
        time.sleep(1.1)
        self.register_and_verify(email)
        settings.LIST_ACCOUNT_DELAY = saved_delay

    def test_profile(self):
        EmailFactory(person__user__username='plain')
        GroupFactory(acronym='mars')

        username = "plain"
        email_address = Email.objects.filter(person__user__username=username).first().address

        url = urlreverse(ietf.ietfauth.views.profile)
        login_testing_unauthorized(self, username, url)


        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('.form-control-static:contains("%s")' % username)), 1)
        self.assertEqual(len(q('[name="active_emails"][value="%s"][checked]' % email_address)), 1)

        base_data = {
            "name": u"Test Nãme",
            "ascii": u"Test Name",
            "ascii_short": u"T. Name",
            "affiliation": "Test Org",
            "active_emails": email_address,
            "consent": True,
        }

        # edit details - faulty ASCII
        faulty_ascii = base_data.copy()
        faulty_ascii["ascii"] = u"Test Nãme"
        r = self.client.post(url, faulty_ascii)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q("form .has-error")) > 0)

        # edit details - blank ASCII
        blank_ascii = base_data.copy()
        blank_ascii["ascii"] = u""
        r = self.client.post(url, blank_ascii)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q("form .has-error")) > 0) # we get a warning about reconstructed name
        self.assertEqual(q("input[name=ascii]").val(), base_data["ascii"])

        # edit details
        r = self.client.post(url, base_data)
        self.assertEqual(r.status_code, 200)
        person = Person.objects.get(user__username=username)
        self.assertEqual(person.name, u"Test Nãme")
        self.assertEqual(person.ascii, u"Test Name")
        self.assertEqual(Person.objects.filter(alias__name=u"Test Name", user__username=username).count(), 1)
        self.assertEqual(Person.objects.filter(alias__name=u"Test Nãme", user__username=username).count(), 1)
        self.assertEqual(Email.objects.filter(address=email_address, person__user__username=username, active=True).count(), 1)

        # deactivate address
        without_email_address = { k: v for k, v in base_data.iteritems() if k != "active_emails" }

        r = self.client.post(url, without_email_address)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Email.objects.filter(address=email_address, person__user__username="plain", active=True).count(), 0)

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[name="%s"][checked]' % email_address)), 0)

        # add email address
        empty_outbox()
        new_email_address = "plain2@example.com"
        with_new_email_address = base_data.copy()
        with_new_email_address["new_email"] = new_email_address
        r = self.client.post(url, with_new_email_address)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(outbox), 1)

        # confirm new email address
        confirm_url = self.extract_confirm_url(outbox[-1])
        r = self.client.get(confirm_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[name="action"][value=confirm]')), 1)

        r = self.client.post(confirm_url, { "action": "confirm" })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Email.objects.filter(address=new_email_address, person__user__username=username, active=1).count(), 1)

        # check that we can't re-add it - that would give a duplicate
        r = self.client.get(confirm_url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[name="action"][value="confirm"]')), 0)

        # change role email
        role = Role.objects.create(
            person=Person.objects.get(user__username=username),
            email=Email.objects.get(address=email_address),
            name=RoleName.objects.get(slug="chair"),
            group=Group.objects.get(acronym="mars"),
        )

        role_email_input_name = "role_%s-email" % role.pk

        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q('[name="%s"]' % role_email_input_name)), 1)
        
        with_changed_role_email = base_data.copy()
        with_changed_role_email["active_emails"] = new_email_address
        with_changed_role_email[role_email_input_name] = new_email_address
        r = self.client.post(url, with_changed_role_email)
        self.assertEqual(r.status_code, 200)
        updated_roles = Role.objects.filter(person=role.person, name=role.name, group=role.group)
        self.assertEqual(len(updated_roles), 1)
        self.assertEqual(updated_roles[0].email_id, new_email_address)


    def test_reset_password(self):
        url = urlreverse(ietf.ietfauth.views.password_reset)

        user = User.objects.create(username="someone@example.com", email="someone@example.com")
        user.set_password("forgotten")
        user.save()
        p = Person.objects.create(name="Some One", ascii="Some One", user=user)
        Email.objects.create(address=user.username, person=p, origin=user.username)
        
        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)

        # ask for reset, wrong username
        r = self.client.post(url, { 'username': "nobody@example.com" })
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q("form .has-error")) > 0)

        # ask for reset
        empty_outbox()
        r = self.client.post(url, { 'username': user.username })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(outbox), 1)

        # go to change password page
        confirm_url = self.extract_confirm_url(outbox[-1])
        r = self.client.get(confirm_url)
        self.assertEqual(r.status_code, 200)

        # password mismatch
        r = self.client.post(confirm_url, { 'password': 'secret', 'password_confirmation': 'nosecret' })
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertTrue(len(q("form .has-error")) > 0)

        # confirm
        r = self.client.post(confirm_url, { 'password': 'secret', 'password_confirmation': 'secret' })
        self.assertEqual(r.status_code, 200)
        q = PyQuery(r.content)
        self.assertEqual(len(q("form .has-error")), 0)
        self.assertTrue(self.username_in_htpasswd_file(user.username))

    def test_review_overview(self):
        review_req = ReviewRequestFactory()
        assignment = ReviewAssignmentFactory(review_request=review_req,reviewer=EmailFactory(person__user__username='reviewer'))
        RoleFactory(name_id='reviewer',group=review_req.team,person=assignment.reviewer.person)
        doc = review_req.doc

        reviewer = assignment.reviewer.person

        UnavailablePeriod.objects.create(
            team=review_req.team,
            person=reviewer,
            start_date=datetime.date.today() - datetime.timedelta(days=10),
            availability="unavailable",
        )

        url = urlreverse(ietf.ietfauth.views.review_overview)

        login_testing_unauthorized(self, reviewer.user.username, url)

        # get
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(review_req.doc.name in unicontent(r))

        # wish to review
        r = self.client.post(url, {
            "action": "add_wish",
            'doc': doc.pk,
            "team": review_req.team_id,
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ReviewWish.objects.filter(doc=doc, team=review_req.team).count(), 1)

        # delete wish
        r = self.client.post(url, {
            "action": "delete_wish",
            'wish_id': ReviewWish.objects.get(doc=doc, team=review_req.team).pk,
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ReviewWish.objects.filter(doc=doc, team=review_req.team).count(), 0)

    def test_htpasswd_file_with_python(self):
        # make sure we test both Python and call-out to binary
        settings.USE_PYTHON_HTDIGEST = True

        update_htpasswd_file("foo", "passwd")
        self.assertTrue(self.username_in_htpasswd_file("foo"))

    @skipIf(skip_htpasswd_command, skip_message)
    @skip_coverage
    def test_htpasswd_file_with_htpasswd_binary(self):
        # make sure we test both Python and call-out to binary
        settings.USE_PYTHON_HTDIGEST = False

        update_htpasswd_file("foo", "passwd")
        self.assertTrue(self.username_in_htpasswd_file("foo"))
        

    def test_change_password(self):

        chpw_url = urlreverse(ietf.ietfauth.views.change_password)
        prof_url = urlreverse(ietf.ietfauth.views.profile)
        login_url = urlreverse(ietf.ietfauth.views.login)
        redir_url = '%s?next=%s' % (login_url, chpw_url)

        # get without logging in
        r = self.client.get(chpw_url)
        self.assertRedirects(r, redir_url)

        user = User.objects.create(username="someone@example.com", email="someone@example.com")
        user.set_password("password")
        user.save()
        p = Person.objects.create(name="Some One", ascii="Some One", user=user)
        Email.objects.create(address=user.username, person=p, origin=user.username)

        # log in
        r = self.client.post(redir_url, {"username":user.username, "password":"password"})
        self.assertRedirects(r, chpw_url)

        # wrong current password
        r = self.client.post(chpw_url, {"current_password": "fiddlesticks",
                                        "new_password": "foobar",
                                        "new_password_confirmation": "foobar",
                                       })
        self.assertEqual(r.status_code, 200)
        self.assertFormError(r, 'form', 'current_password', 'Invalid password')

        # mismatching new passwords
        r = self.client.post(chpw_url, {"current_password": "password",
                                        "new_password": "foobar",
                                        "new_password_confirmation": "barfoo",
                                       })
        self.assertEqual(r.status_code, 200)
        self.assertFormError(r, 'form', None, "The password confirmation is different than the new password")

        # correct password change
        r = self.client.post(chpw_url, {"current_password": "password",
                                        "new_password": "foobar",
                                        "new_password_confirmation": "foobar",
                                       })
        self.assertRedirects(r, prof_url)
        # refresh user object
        user = User.objects.get(username="someone@example.com")
        self.assertTrue(user.check_password(u'foobar'))

    def test_change_username(self):

        chun_url = urlreverse(ietf.ietfauth.views.change_username)
        prof_url = urlreverse(ietf.ietfauth.views.profile)
        login_url = urlreverse(ietf.ietfauth.views.login)
        redir_url = '%s?next=%s' % (login_url, chun_url)

        # get without logging in
        r = self.client.get(chun_url)
        self.assertRedirects(r, redir_url)

        user = User.objects.create(username="someone@example.com", email="someone@example.com")
        user.set_password("password")
        user.save()
        p = Person.objects.create(name="Some One", ascii="Some One", user=user)
        Email.objects.create(address=user.username, person=p, origin=user.username)
        Email.objects.create(address="othername@example.org", person=p, origin=user.username)

        # log in
        r = self.client.post(redir_url, {"username":user.username, "password":"password"})
        self.assertRedirects(r, chun_url)

        # wrong username
        r = self.client.post(chun_url, {"username": "fiddlesticks",
                                        "password": "password",
                                       })
        self.assertEqual(r.status_code, 200)
        self.assertFormError(r, 'form', 'username',
            "Select a valid choice. fiddlesticks is not one of the available choices.")

        # wrong password
        r = self.client.post(chun_url, {"username": "othername@example.org",
                                        "password": "foobar",
                                       })
        self.assertEqual(r.status_code, 200)
        self.assertFormError(r, 'form', 'password', 'Invalid password')

        # correct username change
        r = self.client.post(chun_url, {"username": "othername@example.org",
                                        "password": "password",
                                       })
        self.assertRedirects(r, prof_url)
        # refresh user object
        prev = user
        user = User.objects.get(username="othername@example.org")
        self.assertEqual(prev, user)
        self.assertTrue(user.check_password(u'password'))

    def test_apikey_management(self):
        person = PersonFactory()
        
        url = urlreverse('ietf.ietfauth.views.apikey_index')

        # Check that the url is protected, then log in
        login_testing_unauthorized(self, person.user.username, url)

        # Check api key list content
        r = self.client.get(url)
        self.assertContains(r, 'Personal API keys')
        self.assertContains(r, 'Get a new personal API key')

        # Check the add key form content
        url = urlreverse('ietf.ietfauth.views.apikey_create')
        r = self.client.get(url)
        self.assertContains(r, 'Create a new personal API key')
        self.assertContains(r, 'Endpoint')

        # Add 2 keys
        for endpoint, display in PERSON_API_KEY_ENDPOINTS:
            r = self.client.post(url, {'endpoint': endpoint})
            self.assertRedirects(r, urlreverse('ietf.ietfauth.views.apikey_index'))
        
        # Check api key list content
        url = urlreverse('ietf.ietfauth.views.apikey_index')
        r = self.client.get(url)
        for endpoint, display in PERSON_API_KEY_ENDPOINTS:
            self.assertContains(r, endpoint)
        q = PyQuery(r.content)
        self.assertEqual(len(q('td code')), len(PERSON_API_KEY_ENDPOINTS)) # hash
        self.assertEqual(len(q('td a:contains("Disable")')), len(PERSON_API_KEY_ENDPOINTS))

        # Get one of the keys
        key = person.apikeys.first()

        # Check the disable key form content
        url = urlreverse('ietf.ietfauth.views.apikey_disable')
        r = self.client.get(url)

        self.assertContains(r, 'Disable a personal API key')
        self.assertContains(r, 'Key')
        
        # Delete a key
        r = self.client.post(url, {'hash': key.hash()})
        self.assertRedirects(r, urlreverse('ietf.ietfauth.views.apikey_index'))

        # Check the api key list content again
        url = urlreverse('ietf.ietfauth.views.apikey_index')
        r = self.client.get(url)
        q = PyQuery(r.content)
        self.assertEqual(len(q('td code')), len(PERSON_API_KEY_ENDPOINTS)) # key hash
        self.assertEqual(len(q('td a:contains("Disable")')), len(PERSON_API_KEY_ENDPOINTS)-1)

    def test_apikey_errors(self):
        BAD_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

        person = PersonFactory()
        area = GroupFactory(type_id='area')
        area.role_set.create(name_id='ad', person=person, email=person.email())

        url = urlreverse('ietf.ietfauth.views.apikey_create')
        # Check that the url is protected, then log in
        login_testing_unauthorized(self, person.user.username, url)

        # Add keys
        for endpoint, display in PERSON_API_KEY_ENDPOINTS:
            r = self.client.post(url, {'endpoint': endpoint})
            self.assertRedirects(r, urlreverse('ietf.ietfauth.views.apikey_index'))

        for key in person.apikeys.all()[:3]:
            url = key.endpoint

            # bad method
            r = self.client.put(url, {'apikey':key.hash()})
            self.assertEqual(r.status_code, 405)

            # missing apikey
            r = self.client.post(url, {'dummy':'dummy',})
            self.assertEqual(r.status_code, 400)
            self.assertIn('Missing apikey parameter', unicontent(r))

            # invalid apikey
            r = self.client.post(url, {'apikey':BAD_KEY, 'dummy':'dummy',})
            self.assertEqual(r.status_code, 400)
            self.assertIn('Invalid apikey', unicontent(r))

            # too long since regular login
            person.user.last_login = datetime.datetime.now() - datetime.timedelta(days=settings.UTILS_APIKEY_GUI_LOGIN_LIMIT_DAYS+1)
            person.user.save()
            r = self.client.post(url, {'apikey':key.hash(), 'dummy':'dummy',})
            self.assertEqual(r.status_code, 400)
            self.assertIn('Too long since last regular login', unicontent(r))
            person.user.last_login = datetime.datetime.now()
            person.user.save()

            # endpoint mismatch
            key2 = PersonalApiKey.objects.create(person=person, endpoint='/')
            r = self.client.post(url, {'apikey':key2.hash(), 'dummy':'dummy',})
            self.assertEqual(r.status_code, 400)
            self.assertIn('Apikey endpoint mismatch', unicontent(r))
            key2.delete()

    def test_send_apikey_report(self):
        from ietf.ietfauth.management.commands.send_apikey_usage_emails import Command
        from ietf.utils.mail import outbox, empty_outbox

        person = PersonFactory()

        url = urlreverse('ietf.ietfauth.views.apikey_create')
        # Check that the url is protected, then log in
        login_testing_unauthorized(self, person.user.username, url)

        # Add keys
        for endpoint, display in PERSON_API_KEY_ENDPOINTS:
            r = self.client.post(url, {'endpoint': endpoint})
            self.assertRedirects(r, urlreverse('ietf.ietfauth.views.apikey_index'))
        
        # Use the endpoints (the form content will not be acceptable, but the
        # apikey usage will be registered)
        count = 2
        # avoid usage across dates
        if datetime.datetime.now().time() > datetime.time(hour=23, minute=59, second=58):
            time.sleep(2)
        for i in range(count):
            for key in person.apikeys.all():
                url = key.endpoint
                self.client.post(url, {'apikey':key.hash(), 'dummy': 'dummy', })
        date = str(datetime.date.today())

        empty_outbox()
        cmd = Command()
        cmd.handle(verbosity=0, days=7)
        
        self.assertEqual(len(outbox), len(PERSON_API_KEY_ENDPOINTS))
        for mail in outbox:
            body = mail.get_payload(decode=True).decode('utf-8')
            self.assertIn("API key usage", mail['subject'])
            self.assertIn(" %s times" % count, body)
            self.assertIn(date, body)
        
