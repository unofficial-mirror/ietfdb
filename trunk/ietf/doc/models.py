# Copyright The IETF Trust 2010-2019, All Rights Reserved
# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, unicode_literals

import datetime
import logging
import io
import os
import rfc2html
import six

from django.db import models
from django.core import checks
from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, RegexValidator
from django.urls import reverse as urlreverse
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.utils.encoding import python_2_unicode_compatible, force_text
from django.utils.html import mark_safe

import debug                            # pyflakes:ignore

from ietf.group.models import Group
from ietf.name.models import ( DocTypeName, DocTagName, StreamName, IntendedStdLevelName, StdLevelName,
    DocRelationshipName, DocReminderTypeName, BallotPositionName, ReviewRequestStateName, ReviewAssignmentStateName, FormalLanguageName,
    DocUrlTagName)
from ietf.person.models import Email, Person
from ietf.person.utils import get_active_ads
from ietf.utils import log
from ietf.utils.admin import admin_link
from ietf.utils.decorators import memoize
from ietf.utils.validators import validate_no_control_chars
from ietf.utils.mail import formataddr
from ietf.utils.models import ForeignKey

logger = logging.getLogger('django')

@python_2_unicode_compatible
class StateType(models.Model):
    slug = models.CharField(primary_key=True, max_length=30) # draft, draft-iesg, charter, ...
    label = models.CharField(max_length=255, help_text="Label that should be used (e.g. in admin) for state drop-down for this type of state") # State, IESG state, WG state, ...

    def __str__(self):
        return self.slug

@checks.register('db-consistency')
def check_statetype_slugs(app_configs, **kwargs):
    errors = []
    state_type_slugs = [ t.slug for t in StateType.objects.all() ]
    for type in DocTypeName.objects.all():
        if not type.slug in state_type_slugs:
            errors.append(checks.Error(
                "The document type '%s (%s)' does not have a corresponding entry in the doc.StateType table" % (type.name, type.slug),
                hint="You should add a doc.StateType entry with a slug '%s' to match the DocTypeName slug."%(type.slug),
                obj=type,
                id='datatracker.doc.E0015',
            ))
    return errors

@python_2_unicode_compatible
class State(models.Model):
    type = ForeignKey(StateType)
    slug = models.SlugField()
    name = models.CharField(max_length=255)
    used = models.BooleanField(default=True)
    desc = models.TextField(blank=True)
    order = models.IntegerField(default=0)

    next_states = models.ManyToManyField('State', related_name="previous_states", blank=True)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ["type", "order"]

IESG_BALLOT_ACTIVE_STATES = ("lc", "writeupw", "goaheadw", "iesg-eva", "defer")
IESG_SUBSTATE_TAGS = ('point', 'ad-f-up', 'need-rev', 'extpty')

class DocumentInfo(models.Model):
    """Any kind of document.  Draft, RFC, Charter, IPR Statement, Liaison Statement"""
    time = models.DateTimeField(default=datetime.datetime.now) # should probably have auto_now=True

    type = ForeignKey(DocTypeName, blank=True, null=True) # Draft, Agenda, Minutes, Charter, Discuss, Guideline, Email, Review, Issue, Wiki, External ...
    title = models.CharField(max_length=255, validators=[validate_no_control_chars, ])

    states = models.ManyToManyField(State, blank=True) # plain state (Active/Expired/...), IESG state, stream state
    tags = models.ManyToManyField(DocTagName, blank=True) # Revised ID Needed, ExternalParty, AD Followup, ...
    stream = ForeignKey(StreamName, blank=True, null=True) # IETF, IAB, IRTF, Independent Submission
    group = ForeignKey(Group, blank=True, null=True) # WG, RG, IAB, IESG, Edu, Tools

    abstract = models.TextField(blank=True)
    rev = models.CharField(verbose_name="revision", max_length=16, blank=True)
    pages = models.IntegerField(blank=True, null=True)
    words = models.IntegerField(blank=True, null=True)
    formal_languages = models.ManyToManyField(FormalLanguageName, blank=True, help_text="Formal languages used in document")
    order = models.IntegerField(default=1, blank=True) # This is probably obviated by SessionPresentaion.order
    intended_std_level = ForeignKey(IntendedStdLevelName, verbose_name="Intended standardization level", blank=True, null=True)
    std_level = ForeignKey(StdLevelName, verbose_name="Standardization level", blank=True, null=True)
    ad = ForeignKey(Person, verbose_name="area director", related_name='ad_%(class)s_set', blank=True, null=True)
    shepherd = ForeignKey(Email, related_name='shepherd_%(class)s_set', blank=True, null=True)
    expires = models.DateTimeField(blank=True, null=True)
    notify = models.CharField(max_length=255, blank=True)
    external_url = models.URLField(blank=True)
    uploaded_filename = models.TextField(blank=True)
    note = models.TextField(blank=True)
    internal_comments = models.TextField(blank=True)

    def file_extension(self):
        if not hasattr(self, '_cached_extension'):
            if self.uploaded_filename:
                _, ext= os.path.splitext(self.uploaded_filename)
                self._cached_extension = ext.lstrip(".").lower()
            else:
                self._cached_extension = "txt"
        return self._cached_extension

    def get_file_path(self):
        if not hasattr(self, '_cached_file_path'):
            if self.type_id == "draft":
                if self.is_dochistory():
                    self._cached_file_path = settings.INTERNET_ALL_DRAFTS_ARCHIVE_DIR
                else:
                    if self.get_state_slug() == "rfc":
                        self._cached_file_path = settings.RFC_PATH
                    else:
                        draft_state = self.get_state('draft')
                        if draft_state and draft_state.slug == 'active':
                            self._cached_file_path = settings.INTERNET_DRAFT_PATH
                        else:
                            self._cached_file_path = settings.INTERNET_ALL_DRAFTS_ARCHIVE_DIR
            elif self.type_id in ("agenda", "minutes", "slides", "bluesheets") and self.meeting_related():
                doc = self.doc if isinstance(self, DocHistory) else self
                if doc.session_set.exists():
                    meeting = doc.session_set.first().meeting
                    self._cached_file_path = os.path.join(meeting.get_materials_path(), self.type_id) + "/"
                else:
                    self._cached_file_path = ""
            elif self.type_id == "charter":
                self._cached_file_path = settings.CHARTER_PATH
            elif self.type_id == "conflrev": 
                self._cached_file_path = settings.CONFLICT_REVIEW_PATH
            elif self.type_id == "statchg":
                self._cached_file_path = settings.STATUS_CHANGE_PATH
            else:
                self._cached_file_path = settings.DOCUMENT_PATH_PATTERN.format(doc=self)
        return self._cached_file_path

    def get_base_name(self):
        if not hasattr(self, '_cached_base_name'):
            if self.uploaded_filename:
                self._cached_base_name = self.uploaded_filename
            elif self.type_id == 'draft':
                if self.is_dochistory():
                    self._cached_base_name = "%s-%s.txt" % (self.doc.name, self.rev)
                else:
                    if self.get_state_slug() == 'rfc':
                        self._cached_base_name = "%s.txt" % self.canonical_name()
                    else:
                        self._cached_base_name = "%s-%s.txt" % (self.name, self.rev)
            elif self.type_id in ["slides", "agenda", "minutes", "bluesheets", ] and self.meeting_related():
                    self._cached_base_name = "%s-%s.txt" % self.canonical_name() 
            elif self.type_id == 'review':
                # TODO: This will be wrong if a review is updated on the same day it was created (or updated more than once on the same day)
                self._cached_base_name = "%s.txt" % self.name
            else:
                if self.rev:
                    self._cached_base_name = "%s-%s.txt" % (self.canonical_name(), self.rev)
                else:
                    self._cached_base_name = "%s.txt" % (self.canonical_name(), )
        return self._cached_base_name

    def get_file_name(self):
        if not hasattr(self, '_cached_file_name'):
            self._cached_file_name = os.path.join(self.get_file_path(), self.get_base_name())
        return self._cached_file_name

    def revisions(self):
        revisions = []
        doc = self.doc if isinstance(self, DocHistory) else self
        for e in doc.docevent_set.filter(type='new_revision').distinct():
            if e.rev and not e.rev in revisions:
                revisions.append(e.rev)
        if not doc.rev in revisions:
            revisions.append(doc.rev)
        revisions.sort()
        return revisions


    def href(self, meeting=None):
        return self._get_ref(meeting=meeting,meeting_doc_refs=settings.MEETING_DOC_HREFS)


    def gref(self, meeting=None):
        return self._get_ref(meeting=meeting,meeting_doc_refs=settings.MEETING_DOC_GREFS)


    def _get_ref(self, meeting=None, meeting_doc_refs=settings.MEETING_DOC_HREFS):
        """
        Returns an url to the document text.  This differs from .get_absolute_url(),
        which returns an url to the datatracker page for the document.   
        """
        # If self.external_url truly is an url, use it.  This is a change from
        # the earlier resulution order, but there's at the moment one single
        # instance which matches this (with correct results), so we won't
        # break things all over the place.
        if not hasattr(self, '_cached_href'):
            validator = URLValidator()
            if self.external_url and self.external_url.split(':')[0] in validator.schemes:
                try:
                    validator(self.external_url)
                    return self.external_url
                except ValidationError:
                    log.unreachable('2018-12-28')
                    pass


            if self.type_id in settings.DOC_HREFS and self.type_id in meeting_doc_refs:
                if self.meeting_related():
                    self.is_meeting_related = True
                    format = meeting_doc_refs[self.type_id]
                else:
                    self.is_meeting_related = False
                    format = settings.DOC_HREFS[self.type_id]
            elif self.type_id in settings.DOC_HREFS:
                self.is_meeting_related = False
                if self.is_rfc():
                    format = settings.DOC_HREFS['rfc']
                else:
                    format = settings.DOC_HREFS[self.type_id]
            elif self.type_id in meeting_doc_refs:
                self.is_meeting_related = True
            else:
                return None

            if self.is_meeting_related:
                if not meeting:
                    # we need to do this because DocHistory items don't have
                    # any session_set entry:
                    doc = self.doc if isinstance(self, DocHistory) else self
                    sess = doc.session_set.first()
                    if not sess:
                        return ""
                    meeting = sess.meeting
                # After IETF 96, meeting materials acquired revision
                # handling, and the document naming changed.
                if meeting.number.isdigit() and int(meeting.number) > 96:
                    format = meeting_doc_refs[self.type_id]
                else:
                    format = settings.MEETING_DOC_OLD_HREFS[self.type_id]
                info = dict(doc=self, meeting=meeting)
            else:
                info = dict(doc=self)

            href = format.format(**info)
            if href.startswith('/'):
                href = settings.IDTRACKER_BASE_URL + href
            self._cached_href = href
        return self._cached_href

    def set_state(self, state):
        """Switch state type implicit in state to state. This just
        sets the state, doesn't log the change."""
        already_set = self.states.filter(type=state.type)
        others = [s for s in already_set if s != state]
        if others:
            self.states.remove(*others)
        if state not in already_set:
            self.states.add(state)
        self.state_cache = None # invalidate cache
        self._cached_state_slug = {}

    def unset_state(self, state_type):
        """Unset state of type so no state of that type is any longer set."""
        log.assertion('state_type != "draft-iesg"')
        self.states.remove(*self.states.filter(type=state_type))
        self.state_cache = None # invalidate cache
        self._cached_state_slug = {}

    def get_state(self, state_type=None):
        """Get state of type, or default state for document type if
        not specified. Uses a local cache to speed multiple state
        reads up."""
        if self.pk == None: # states is many-to-many so not in database implies no state
            return None

        if state_type == None:
            state_type = self.type_id

        if not hasattr(self, "state_cache") or self.state_cache == None:
            self.state_cache = {}
            for s in self.states.all():
                self.state_cache[s.type_id] = s

        return self.state_cache.get(state_type, None)

    def get_state_slug(self, state_type=None):
        """Get state of type, or default if not specified, returning
        the slug of the state or None. This frees the caller of having
        to check against None before accessing the slug for a
        comparison."""
        if not hasattr(self, '_cached_state_slug'):
            self._cached_state_slug = {}
        if not state_type in self._cached_state_slug:
            s = self.get_state(state_type)
            self._cached_state_slug[state_type] = s.slug if s else None
        return self._cached_state_slug[state_type]

    def friendly_state(self):
        """ Return a concise text description of the document's current state."""
        state = self.get_state()
        if not state:
            return "Unknown state"
    
        if self.type_id == 'draft':
            iesg_state = self.get_state("draft-iesg")
            iesg_state_summary = None
            if iesg_state:
                iesg_substate = [t for t in self.tags.all() if t.slug in IESG_SUBSTATE_TAGS]
                # There really shouldn't be more than one tag in iesg_substate, but this will do something sort-of-sensible if there is
                iesg_state_summary = iesg_state.name
                if iesg_substate:
                     iesg_state_summary = iesg_state_summary + "::"+"::".join(tag.name for tag in iesg_substate)
             
            if state.slug == "rfc":
                return "RFC %s (%s)" % (self.rfc_number(), self.std_level)
            elif state.slug == "repl":
                rs = self.related_that("replaces")
                if rs:
                    return mark_safe("Replaced by " + ", ".join("<a href=\"%s\">%s</a>" % (urlreverse('ietf.doc.views_doc.document_main', kwargs=dict(name=alias.document.name)), alias.document) for alias in rs))
                else:
                    return "Replaced"
            elif state.slug == "active":
                log.assertion('iesg_state')
                if iesg_state:
                    if iesg_state.slug == "dead":
                        # Many drafts in the draft-iesg "Dead" state are not dead
                        # in other state machines; they're just not currently under 
                        # IESG processing. Show them as "I-D Exists (IESG: Dead)" instead...
                        return "I-D Exists (IESG: %s)" % iesg_state_summary
                    elif iesg_state.slug == "lc":
                        e = self.latest_event(LastCallDocEvent, type="sent_last_call")
                        if e:
                            return iesg_state_summary + " (ends %s)" % e.expires.date().isoformat()
    
                    return iesg_state_summary
                else:
                    return "I-D Exists"
            else:
                if iesg_state and iesg_state.slug == "dead":
                    return state.name + " (IESG: %s)" % iesg_state_summary
                # Expired/Withdrawn by Submitter/IETF
                return state.name
        else:
            return state.name

    def is_rfc(self):
        if not hasattr(self, '_cached_is_rfc'):
            self._cached_is_rfc = self.pk and self.type_id == 'draft' and self.states.filter(type='draft',slug='rfc').exists()
        return self._cached_is_rfc

    def rfc_number(self):
        if not hasattr(self, '_cached_rfc_number'):
            self._cached_rfc_number = None
            if self.is_rfc():
                n = self.canonical_name()
                if n.startswith("rfc"):
                    self._cached_rfc_number = n[3:]
                else:
                    logger.error("Document self.is_rfc() is True but self.canonical_name() is %s" % n)
        return self._cached_rfc_number

    @property
    def rfcnum(self):
        return self.rfc_number()

    def author_list(self):
        return ", ".join(author.email_id for author in self.documentauthor_set.all() if author.email_id)

    def authors(self):
        return [ a.person for a in self.documentauthor_set.all() ]

    # This, and several other ballot related functions here, assume that there is only one active ballot for a document at any point in time.
    # If that assumption is violated, they will only expose the most recently created ballot
    def ballot_open(self, ballot_type_slug):
        e = self.latest_event(BallotDocEvent, ballot_type__slug=ballot_type_slug)
        return e if e and not e.type == "closed_ballot" else None

    def latest_ballot(self):
        """Returns the most recently created ballot"""
        ballot = self.latest_event(BallotDocEvent, type__in=("created_ballot", "closed_ballot"))
        return ballot

    def active_ballot(self):
        """Returns the most recently created ballot if it isn't closed."""
        ballot = self.latest_ballot()
        if ballot and ballot.type == "created_ballot":
            return ballot
        else:
            return None

    def has_rfc_editor_note(self):
        e = self.latest_event(WriteupDocEvent, type="changed_rfc_editor_note_text")
        return e != None and (e.text != "")

    def meeting_related(self):
        if self.type_id in ("agenda","minutes","bluesheets","slides","recording"):
             return self.type_id != "slides" or self.get_state_slug('reuse_policy')=='single'
        return False

    def relations_that(self, relationship):
        """Return the related-document objects that describe a given relationship targeting self."""
        if isinstance(relationship, six.string_types):
            relationship = ( relationship, )
        if not isinstance(relationship, tuple):
            raise TypeError("Expected a string or tuple, received %s" % type(relationship))
        if isinstance(self, Document):
            return RelatedDocument.objects.filter(target__docs=self, relationship__in=relationship).select_related('source')
        elif isinstance(self, DocHistory):
            return RelatedDocHistory.objects.filter(target__docs=self.doc, relationship__in=relationship).select_related('source')
        else:
            raise TypeError("Expected method called on Document or DocHistory")

    def all_relations_that(self, relationship, related=None):
        if not related:
            related = tuple([])
        rels = self.relations_that(relationship)
        for r in rels:
            if not r in related:
                related += ( r, )
                related = r.source.all_relations_that(relationship, related)
        return related

    def relations_that_doc(self, relationship):
        """Return the related-document objects that describe a given relationship from self to other documents."""
        if isinstance(relationship, six.string_types):
            relationship = ( relationship, )
        if not isinstance(relationship, tuple):
            raise TypeError("Expected a string or tuple, received %s" % type(relationship))
        if isinstance(self, Document):
            return RelatedDocument.objects.filter(source=self, relationship__in=relationship).select_related('target')
        elif isinstance(self, DocHistory):
            return RelatedDocHistory.objects.filter(source=self, relationship__in=relationship).select_related('target')
        else:
            raise TypeError("Expected method called on Document or DocHistory")

    def all_relations_that_doc(self, relationship, related=None):
        if not related:
            related = tuple([])
        rels = self.relations_that_doc(relationship)
        for r in rels:
            if not r in related:
                related += ( r, )
                for doc in r.target.docs.all():
                    related = doc.all_relations_that_doc(relationship, related)
        return related

    def related_that(self, relationship):
        return list(set([x.source.docalias.get(name=x.source.name) for x in self.relations_that(relationship)]))

    def all_related_that(self, relationship, related=None):
        return list(set([x.source.docalias.get(name=x.source.name) for x in self.all_relations_that(relationship)]))

    def related_that_doc(self, relationship):
        return list(set([x.target for x in self.relations_that_doc(relationship)]))

    def all_related_that_doc(self, relationship, related=None):
        return list(set([x.target for x in self.all_relations_that_doc(relationship)]))

    def replaces(self):
        return set([ d for r in self.related_that_doc("replaces") for d in r.docs.all() ])

    def replaces_canonical_name(self):
        s = set([ r.document for r in self.related_that_doc("replaces")])
        first = list(s)[0] if s else None
        return None if first is None else first.filename_with_rev()

    def replaced_by(self):
        return set([ r.document for r in self.related_that("replaces") ])

    def text(self):
        path = self.get_file_name()
        root, ext =  os.path.splitext(path)
        txtpath = root+'.txt'
        if ext != '.txt' and os.path.exists(txtpath):
            path = txtpath
        try:
            with io.open(path, 'rb') as file:
                raw = file.read()
        except IOError:
            return None
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
        #
        return text

    def text_or_error(self):
        return self.text() or "Error; cannot read '%s'"%self.get_base_name()

    def htmlized(self):
        name = self.get_base_name()
        text = self.text()
        if name.endswith('.html'):
            return text
        if not name.endswith('.txt'):
            return None
        html = ""
        if text:
            cache = caches['htmlized']
            cache_key = name.split('.')[0]
            try:
                html = cache.get(cache_key)
            except EOFError:
                html = None
            if not html:
                # The path here has to match the urlpattern for htmlized
                # documents in order to produce correct intra-document links
                html = rfc2html.markup(text, path=settings.HTMLIZER_URL_PREFIX)
                if html:
                    cache.set(cache_key, html, settings.HTMLIZER_CACHE_TIME)
        return html

    class Meta:
        abstract = True

STATUSCHANGE_RELATIONS = ('tops','tois','tohist','toinf','tobcp','toexp')

@python_2_unicode_compatible
class RelatedDocument(models.Model):
    source = ForeignKey('Document')
    target = ForeignKey('DocAlias')
    relationship = ForeignKey(DocRelationshipName)
    def action(self):
        return self.relationship.name
    def __str__(self):
        return u"%s %s %s" % (self.source.name, self.relationship.name.lower(), self.target.name)

    def is_downref(self):

        if self.source.type.slug!='draft' or self.relationship.slug not in ['refnorm','refold','refunk']:
            return None

        state = self.source.get_state()
        if state and state.slug == 'rfc':
            source_lvl = self.source.std_level.slug if self.source.std_level else None
        elif self.source.intended_std_level:
            source_lvl = self.source.intended_std_level.slug
        else:
            source_lvl = None

        if source_lvl not in ['bcp','ps','ds','std']:
            return None

        if self.target.document.get_state().slug == 'rfc':
            if not self.target.document.std_level:
                target_lvl = 'unkn'
            else:
                target_lvl = self.target.document.std_level.slug
        else:
            if not self.target.document.intended_std_level:
                target_lvl = 'unkn'
            else:
                target_lvl = self.target.document.intended_std_level.slug

        rank = { 'ps':1, 'ds':2, 'std':3, 'bcp':3 }

        if ( target_lvl not in rank ) or ( rank[target_lvl] < rank[source_lvl] ):
            if self.relationship.slug == 'refnorm' and target_lvl!='unkn':
                return "Downref"
            else:
                return "Possible Downref"

        return None

    def is_approved_downref(self):

        if self.target.document.get_state().slug == 'rfc':
           if RelatedDocument.objects.filter(relationship_id='downref-approval', target=self.target):
              return "Approved Downref"

        return False

class DocumentAuthorInfo(models.Model):
    person = ForeignKey(Person)
    # email should only be null for some historic documents
    email = ForeignKey(Email, help_text="Email address used by author for submission", blank=True, null=True)
    affiliation = models.CharField(max_length=100, blank=True, help_text="Organization/company used by author for submission")
    country = models.CharField(max_length=255, blank=True, help_text="Country used by author for submission")
    order = models.IntegerField(default=1)

    def formatted_email(self):

        if self.email:
            return formataddr((self.person.plain_ascii(), self.email.address))
        else:
            return ""

    class Meta:
        abstract = True
        ordering = ["document", "order"]

@python_2_unicode_compatible
class DocumentAuthor(DocumentAuthorInfo):
    document = ForeignKey('Document')

    def __str__(self):
        return u"%s %s (%s)" % (self.document.name, self.person, self.order)


validate_docname = RegexValidator(
    r'^[-a-z0-9]+$',
    "Provide a valid document name consisting of lowercase letters, numbers and hyphens.",
    'invalid'
)

@python_2_unicode_compatible
class Document(DocumentInfo):
    name = models.CharField(max_length=255, validators=[validate_docname,], unique=True)           # immutable

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        """
        Returns an url to the document view.  This differs from .href(),
        which returns an url to the document content.
        """
        if not hasattr(self, '_cached_absolute_url'):
            name = self.name
            if self.type_id == "draft" and self.get_state_slug() == "rfc":
                name = self.canonical_name()
                url = urlreverse('ietf.doc.views_doc.document_main', kwargs={ 'name': name }, urlconf="ietf.urls")
            elif self.type_id in ('slides','bluesheets','recording'):
                session = self.session_set.first()
                if session:
                    meeting = session.meeting
                    if self.type_id == 'recording':
                        url = self.external_url
                    else:
                        filename = self.uploaded_filename
                        url = '%sproceedings/%s/%s/%s' % (settings.IETF_HOST_URL,meeting.number,self.type_id,filename)
            else:
                url = urlreverse('ietf.doc.views_doc.document_main', kwargs={ 'name': name }, urlconf="ietf.urls")
            self._cached_absolute_url = url
        return self._cached_absolute_url

    def file_tag(self):
        return "<%s>" % self.filename_with_rev()

    def filename_with_rev(self):
        return "%s-%s.txt" % (self.name, self.rev)
    
    def latest_event(self, *args, **filter_args):
        """Get latest event of optional Python type and with filter
        arguments, e.g. d.latest_event(type="xyz") returns an DocEvent
        while d.latest_event(WriteupDocEvent, type="xyz") returns a
        WriteupDocEvent event."""
        model = args[0] if args else DocEvent
        e = model.objects.filter(doc=self).filter(**filter_args).order_by('-time', '-id').first()
        return e

    def canonical_name(self):
        if not hasattr(self, '_canonical_name'):
            name = self.name
            if self.type_id == "draft" and self.get_state_slug() == "rfc":
                a = self.docalias.filter(name__startswith="rfc").order_by('-name').first()
                if a:
                    name = a.name
            elif self.type_id == "charter":
                from ietf.doc.utils_charter import charter_name_for_group # Imported locally to avoid circular imports
                try:
                    name = charter_name_for_group(self.chartered_group)
                except Group.DoesNotExist:
                    pass
            self._canonical_name = name
        return self._canonical_name


    def canonical_docalias(self):
        return self.docalias.get(name=self.name)

    def display_name(self):
        name = self.canonical_name()
        if name.startswith('rfc'):
            name = name.upper()
        return name

    def save_with_history(self, events):
        """Save document and put a snapshot in the history models where they
        can be retrieved later. You must pass in at least one event
        with a description of what happened."""

        assert events, "You must always add at least one event to describe the changes in the history log"
        self.time = max(self.time, events[0].time)

        self._has_an_event_so_saving_is_allowed = True
        self.save()
        del self._has_an_event_so_saving_is_allowed

        from ietf.doc.utils import save_document_in_history
        save_document_in_history(self)

    def save(self, *args, **kwargs):
        # if there's no primary key yet, we can allow the save to go
        # through to break the cycle between the document and any
        # events
        assert kwargs.get("force_insert", False) or getattr(self, "_has_an_event_so_saving_is_allowed", None), "Use .save_with_history to save documents"
        super(Document, self).save(*args, **kwargs)

    def telechat_date(self, e=None):
        if not e:
            e = self.latest_event(TelechatDocEvent, type="scheduled_for_telechat")
        return e.telechat_date if e and e.telechat_date and e.telechat_date >= datetime.date.today() else None

    def past_telechat_date(self):
        "Return the latest telechat date if it isn't in the future; else None"
        e = self.latest_event(TelechatDocEvent, type="scheduled_for_telechat")
        return e.telechat_date if e and e.telechat_date and e.telechat_date < datetime.date.today() else None

    def previous_telechat_date(self):
        "Return the most recent telechat date in the past, if any (even if there's another in the future)"
        e = self.latest_event(TelechatDocEvent, type="scheduled_for_telechat", telechat_date__lt=datetime.datetime.now())
        return e.telechat_date if e else None

    def request_closed_time(self, review_req):
        e = self.latest_event(ReviewRequestDocEvent, type="closed_review_request", review_request=review_req)
        return e.time if e and e.time else None

    def area_acronym(self):
        g = self.group
        if g:
            if g.type_id == "area":
                return g.acronym
            elif g.type_id != "individ" and g.parent:
                return g.parent.acronym
        else:
            return None
    
    def group_acronym(self):
        g = self.group
        if g and g.type_id != "area":
            return g.acronym
        else:
            return "none"

    @memoize
    def returning_item(self):
        e = self.latest_event(TelechatDocEvent, type="scheduled_for_telechat")
        return e.returning_item if e else None

    # This is brittle. Resist the temptation to make it more brittle by combining the search against those description
    # strings to one command. It is coincidence that those states have the same description - one might change.
    # Also, this needs further review - is it really the case that there would be no other changed_document events
    # between when the state was changed to defer and when some bit of code wants to know if we are deferred? Why
    # isn't this just returning whether the state is currently a defer state for that document type?
    def active_defer_event(self):
        if self.type_id == "draft" and self.get_state_slug("draft-iesg") == "defer":
            return self.latest_event(type="changed_state", desc__icontains="State changed to <b>IESG Evaluation - Defer</b>")
        elif self.type_id == "conflrev" and self.get_state_slug("conflrev") == "defer":
            return self.latest_event(type="changed_state", desc__icontains="State changed to <b>IESG Evaluation - Defer</b>")
        elif self.type_id == "statchg" and self.get_state_slug("statchg") == "defer":
            return self.latest_event(type="changed_state", desc__icontains="State changed to <b>IESG Evaluation - Defer</b>")
        return None

    def most_recent_ietflc(self):
        """Returns the most recent IETF LastCallDocEvent for this document"""
        return self.latest_event(LastCallDocEvent,type="sent_last_call")

    def displayname_with_link(self):
        return mark_safe('<a href="%s">%s-%s</a>' % (self.get_absolute_url(), self.name , self.rev))

    def ipr(self,states=('posted','removed')):
        """Returns the IPR disclosures against this document (as a queryset over IprDocRel)."""
        from ietf.ipr.models import IprDocRel
        return IprDocRel.objects.filter(document__docs=self, disclosure__state__in=states)

    def related_ipr(self):
        """Returns the IPR disclosures against this document and those documents this
        document directly or indirectly obsoletes or replaces
        """
        from ietf.ipr.models import IprDocRel
        iprs = IprDocRel.objects.filter(document__in=list(self.docalias.all())+self.all_related_that_doc(('obs','replaces'))).filter(disclosure__state__in=('posted','removed')).values_list('disclosure', flat=True).distinct()
        return iprs

    def future_presentations(self):
        """ returns related SessionPresentation objects for meetings that
            have not yet ended. This implementation allows for 2 week meetings """
        candidate_presentations = self.sessionpresentation_set.filter(session__meeting__date__gte=datetime.date.today()-datetime.timedelta(days=15))
        return sorted([pres for pres in candidate_presentations if pres.session.meeting.end_date()>=datetime.date.today()], key=lambda x:x.session.meeting.date)

    def last_presented(self):
        """ returns related SessionPresentation objects for the most recent meeting in the past"""
        # Assumes no two meetings have the same start date - if the assumption is violated, one will be chosen arbitrariy
        candidate_presentations = self.sessionpresentation_set.filter(session__meeting__date__lte=datetime.date.today())
        candidate_meetings = set([p.session.meeting for p in candidate_presentations if p.session.meeting.end_date()<datetime.date.today()])
        if candidate_meetings:
            mtg = sorted(list(candidate_meetings),key=lambda x:x.date,reverse=True)[0]
            return self.sessionpresentation_set.filter(session__meeting=mtg)
        else:
            return None

    def submission(self):
        s = self.submission_set.filter(rev=self.rev)
        s = s.first()
        return s

    def pub_date(self):
        """This is the rfc publication date (datetime) for RFCs, 
        and the new-revision datetime for other documents."""
        if self.get_state_slug() == "rfc":
            event = self.latest_event(type='published_rfc')
        else:
            event = self.latest_event(type='new_revision')
        return event.time

    def is_dochistory(self):
        return False

    def fake_history_obj(self, rev):
        """
        Mock up a fake DocHistory object with the given revision, for
        situations where we need an entry but there is none in the DocHistory
        table.
        XXX TODO: Add missing objects to DocHistory instead
        """
        history = DocHistory.objects.filter(doc=self, rev=rev).order_by("time")
        if history.exists():
            return history.first()
        else:
            # fake one
            events = self.docevent_set.order_by("time", "id")
            rev_events = events.filter(rev=rev)
            new_rev_events = rev_events.filter(type='new_revision')
            if new_rev_events.exists():
                time = new_rev_events.first().time
            elif rev_events.exists():
                time = rev_events.first().time
            else:
                time = datetime.datetime.fromtimestamp(0)
            dh = DocHistory(name=self.name, rev=rev, doc=self, time=time, type=self.type, title=self.title,
                             stream=self.stream, group=self.group)

        return dh


class DocumentURL(models.Model):
    doc  = ForeignKey(Document)
    tag  = ForeignKey(DocUrlTagName)
    desc = models.CharField(max_length=255, default='', blank=True)
    url  = models.URLField(max_length=2083) # 2083 is the legal max for URLs

@python_2_unicode_compatible
class RelatedDocHistory(models.Model):
    source = ForeignKey('DocHistory')
    target = ForeignKey('DocAlias', related_name="reversely_related_document_history_set")
    relationship = ForeignKey(DocRelationshipName)
    def __str__(self):
        return u"%s %s %s" % (self.source.doc.name, self.relationship.name.lower(), self.target.name)

@python_2_unicode_compatible
class DocHistoryAuthor(DocumentAuthorInfo):
    # use same naming convention as non-history version to make it a bit
    # easier to write generic code
    document = ForeignKey('DocHistory', related_name="documentauthor_set")

    def __str__(self):
        return u"%s %s (%s)" % (self.document.doc.name, self.person, self.order)

@python_2_unicode_compatible
class DocHistory(DocumentInfo):
    doc = ForeignKey(Document, related_name="history_set")
    # the name here is used to capture the canonical name at the time
    # - it would perhaps be more elegant to simply call the attribute
    # canonical_name and replace the function on Document with a
    # property
    name = models.CharField(max_length=255)

    def __str__(self):
        return force_text(self.doc.name)

    def canonical_name(self):
        if hasattr(self, '_canonical_name'):
            return self._canonical_name
        return self.name

    def latest_event(self, *args, **kwargs):
        kwargs["time__lte"] = self.time
        return self.doc.latest_event(*args, **kwargs)

    def future_presentations(self):
        return self.doc.future_presentations()

    def last_presented(self):
        return self.doc.last_presented()

    @property
    def groupmilestone_set(self):
        return self.doc.groupmilestone_set

    @property
    def docalias(self):
        return self.doc.docalias

    def is_dochistory(self):
        return True

    def related_ipr(self):
        return self.doc.related_ipr()

    class Meta:
        verbose_name = "document history"
        verbose_name_plural = "document histories"

@python_2_unicode_compatible
class DocAlias(models.Model):
    """This is used for documents that may appear under multiple names,
    and in particular for RFCs, which for continuity still keep the
    same immutable Document.name, in the tables, but will be referred
    to by RFC number, primarily, after achieving RFC status.
    """
    name = models.CharField(max_length=255, unique=True)
    docs = models.ManyToManyField(Document, related_name='docalias')

    @property
    def document(self):
        return self.docs.first()

    def __str__(self):
        return u"%s-->%s" % (self.name, ','.join([force_text(d.name) for d in self.docs.all() if isinstance(d, Document) ]))
    document_link = admin_link("document")
    class Meta:
        verbose_name = "document alias"
        verbose_name_plural = "document aliases"

class DocReminder(models.Model):
    event = ForeignKey('DocEvent')
    type = ForeignKey(DocReminderTypeName)
    due = models.DateTimeField()
    active = models.BooleanField(default=True)


EVENT_TYPES = [
    # core events
    ("new_revision", "Added new revision"),
    ("new_submission", "Uploaded new revision"),
    ("changed_document", "Changed document metadata"),
    ("added_comment", "Added comment"),
    ("added_message", "Added message"),
    ("edited_authors", "Edited the documents author list"),

    ("deleted", "Deleted document"),

    ("changed_state", "Changed state"),

    # misc draft/RFC events
    ("changed_stream", "Changed document stream"),
    ("expired_document", "Expired document"),
    ("extended_expiry", "Extended expiry of document"),
    ("requested_resurrect", "Requested resurrect"),
    ("completed_resurrect", "Completed resurrect"),
    ("changed_consensus", "Changed consensus"),
    ("published_rfc", "Published RFC"),
    ("added_suggested_replaces", "Added suggested replacement relationships"),
    ("reviewed_suggested_replaces", "Reviewed suggested replacement relationships"),

    # WG events
    ("changed_group", "Changed group"),
    ("changed_protocol_writeup", "Changed protocol writeup"),
    ("changed_charter_milestone", "Changed charter milestone"),

    # charter events
    ("initial_review", "Set initial review time"),
    ("changed_review_announcement", "Changed WG Review text"),
    ("changed_action_announcement", "Changed WG Action text"),

    # IESG events
    ("started_iesg_process", "Started IESG process on document"),

    ("created_ballot", "Created ballot"),
    ("closed_ballot", "Closed ballot"),
    ("sent_ballot_announcement", "Sent ballot announcement"),
    ("changed_ballot_position", "Changed ballot position"),
    
    ("changed_ballot_approval_text", "Changed ballot approval text"),
    ("changed_ballot_writeup_text", "Changed ballot writeup text"),
    ("changed_rfc_editor_note_text", "Changed RFC Editor Note text"),

    ("changed_last_call_text", "Changed last call text"),
    ("requested_last_call", "Requested last call"),
    ("sent_last_call", "Sent last call"),

    ("scheduled_for_telechat", "Scheduled for telechat"),

    ("iesg_approved", "IESG approved document (no problem)"),
    ("iesg_disapproved", "IESG disapproved document (do not publish)"),
    
    ("approved_in_minute", "Approved in minute"),

    # IANA events
    ("iana_review", "IANA review comment"),
    ("rfc_in_iana_registry", "RFC is in IANA registry"),

    # RFC Editor
    ("rfc_editor_received_announcement", "Announcement was received by RFC Editor"),
    ("requested_publication", "Publication at RFC Editor requested"),
    ("sync_from_rfc_editor", "Received updated information from RFC Editor"),

    # review
    ("requested_review", "Requested review"),
    ("assigned_review_request", "Assigned review request"),
    ("closed_review_request", "Closed review request"),
    ("closed_review_assignment", "Closed review assignment"),

    # downref
    ("downref_approved", "Downref approved"),
    ]

@python_2_unicode_compatible
class DocEvent(models.Model):
    """An occurrence for a document, used for tracking who, when and what."""
    time = models.DateTimeField(default=datetime.datetime.now, help_text="When the event happened", db_index=True)
    type = models.CharField(max_length=50, choices=EVENT_TYPES)
    by = ForeignKey(Person)
    doc = ForeignKey(Document)
    rev = models.CharField(verbose_name="revision", max_length=16, null=True, blank=True)
    desc = models.TextField()

    def for_current_revision(self):
        e = self.doc.latest_event(NewRevisionDocEvent,type='new_revision')
        return not e or (self.time, self.pk) >= (e.time, e.pk)

    def get_dochistory(self):
        return DocHistory.objects.filter(time__lte=self.time,doc__name=self.doc.name).order_by('-time', '-pk').first()

    def __str__(self):
        return u"%s %s by %s at %s" % (self.doc.name, self.get_type_display().lower(), self.by.plain_name(), self.time)

    def save(self, *args, **kwargs):
        super(DocEvent, self).save(*args, **kwargs)        
        log.assertion('self.rev != None')

    class Meta:
        ordering = ['-time', '-id']
        indexes = [
            models.Index(fields=['type', 'doc']),
        ]
        
class NewRevisionDocEvent(DocEvent):
    pass

class StateDocEvent(DocEvent):
    state_type = ForeignKey(StateType)
    state = ForeignKey(State, blank=True, null=True)

class ConsensusDocEvent(DocEvent):
    consensus = models.NullBooleanField(default=None)

# IESG events
@python_2_unicode_compatible
class BallotType(models.Model):
    doc_type = ForeignKey(DocTypeName, blank=True, null=True)
    slug = models.SlugField()
    name = models.CharField(max_length=255)
    question = models.TextField(blank=True)
    used = models.BooleanField(default=True)
    order = models.IntegerField(default=0)
    positions = models.ManyToManyField(BallotPositionName, blank=True)

    def __str__(self):
        return u"%s: %s" % (self.name, self.doc_type.name)
    
    class Meta:
        ordering = ['order']

class BallotDocEvent(DocEvent):
    ballot_type = ForeignKey(BallotType)

    def active_ad_positions(self):
        """Return dict mapping each active AD to a current ballot position (or None if they haven't voted)."""
        res = {}
    
        active_ads = get_active_ads()
        positions = BallotPositionDocEvent.objects.filter(type="changed_ballot_position",ad__in=active_ads, ballot=self).select_related('ad', 'pos').order_by("-time", "-id")

        for pos in positions:
            if pos.ad not in res:
                res[pos.ad] = pos

        for ad in active_ads:
            if ad not in res:
                res[ad] = None
        return res

    def all_positions(self):
        """Return array holding the current and past positions per AD"""

        positions = []
        seen = {}
        active_ads = get_active_ads()
        for e in BallotPositionDocEvent.objects.filter(type="changed_ballot_position", ballot=self).select_related('ad', 'pos').order_by("-time", '-id'):
            if e.ad not in seen:
                e.old_ad = e.ad not in active_ads
                e.old_positions = []
                positions.append(e)
                seen[e.ad] = e
            else:
                latest = seen[e.ad]
                if latest.old_positions:
                    prev = latest.old_positions[-1]
                else:
                    prev = latest.pos
    
                if e.pos != prev:
                    latest.old_positions.append(e.pos)

        # get rid of trailling "No record" positions, some old ballots
        # have plenty of these
        for p in positions:
            while p.old_positions and p.old_positions[-1].slug == "norecord":
                p.old_positions.pop()

        # add any missing ADs through fake No Record events
        if self.doc.active_ballot() == self:
            norecord = BallotPositionName.objects.get(slug="norecord")
            for ad in active_ads:
                if ad not in seen:
                    e = BallotPositionDocEvent(type="changed_ballot_position", doc=self.doc, rev=self.doc.rev, ad=ad)
                    e.by = ad
                    e.pos = norecord
                    e.old_ad = False
                    e.old_positions = []
                    positions.append(e)

        positions.sort(key=lambda p: (p.old_ad, p.ad.last_name()))
        return positions


class BallotPositionDocEvent(DocEvent):
    ballot = ForeignKey(BallotDocEvent, null=True, default=None) # default=None is a temporary migration period fix, should be removed when charter branch is live
    ad = ForeignKey(Person)
    pos = ForeignKey(BallotPositionName, verbose_name="position", default="norecord")
    discuss = models.TextField(help_text="Discuss text if position is discuss", blank=True)
    discuss_time = models.DateTimeField(help_text="Time discuss text was written", blank=True, null=True)
    comment = models.TextField(help_text="Optional comment", blank=True)
    comment_time = models.DateTimeField(help_text="Time optional comment was written", blank=True, null=True)
    send_email = models.NullBooleanField(default=None)

    @memoize
    def any_email_sent(self):
        # When the send_email field is introduced, old positions will have it
        # set to None.  We still essentially return True, False, or don't know:
        sent_list = BallotPositionDocEvent.objects.filter(ballot=self.ballot, time__lte=self.time, ad=self.ad).values_list('send_email', flat=True)
        false = any( s==False for s in sent_list )
        true  = any( s==True for s in sent_list )
        return True if true else False if false else None


class WriteupDocEvent(DocEvent):
    text = models.TextField(blank=True)

class LastCallDocEvent(DocEvent):
    expires = models.DateTimeField(blank=True, null=True)
    
class TelechatDocEvent(DocEvent):
    telechat_date = models.DateField(blank=True, null=True)
    returning_item = models.BooleanField(default=False)

class ReviewRequestDocEvent(DocEvent):
    review_request = ForeignKey('review.ReviewRequest')
    state = ForeignKey(ReviewRequestStateName, blank=True, null=True)

class ReviewAssignmentDocEvent(DocEvent):
    review_assignment = ForeignKey('review.ReviewAssignment')
    state = ForeignKey(ReviewAssignmentStateName, blank=True, null=True)

# charter events
class InitialReviewDocEvent(DocEvent):
    expires = models.DateTimeField(blank=True, null=True)

class AddedMessageEvent(DocEvent):
    import ietf.message.models
    message     = ForeignKey(ietf.message.models.Message, null=True, blank=True,related_name='doc_manualevents')
    msgtype     = models.CharField(max_length=25)
    in_reply_to = ForeignKey(ietf.message.models.Message, null=True, blank=True,related_name='doc_irtomanual')


class SubmissionDocEvent(DocEvent):
    import ietf.submit.models
    submission = ForeignKey(ietf.submit.models.Submission)

# dumping store for removed events
@python_2_unicode_compatible
class DeletedEvent(models.Model):
    content_type = ForeignKey(ContentType)
    json = models.TextField(help_text="Deleted object in JSON format, with attribute names chosen to be suitable for passing into the relevant create method.")
    by = ForeignKey(Person)
    time = models.DateTimeField(default=datetime.datetime.now)

    def __str__(self):
        return u"%s by %s %s" % (self.content_type, self.by, self.time)

class EditedAuthorsDocEvent(DocEvent):
    """ Capture the reasoning or authority for changing a document author list.
        Allows programs to recognize and not change lists that have been manually verified and corrected.
        Example 'basis' values might be from ['manually adjusted','recomputed by parsing document', etc.]
    """
    basis = models.CharField(help_text="What is the source or reasoning for the changes to the author list",max_length=255)
