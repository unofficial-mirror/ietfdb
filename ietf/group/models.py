# Copyright The IETF Trust 2007-2019, All Rights Reserved
# -*- coding: utf-8 -*-

import datetime
import email.utils
import jsonfield
import os
import re
from urlparse import urljoin

from django.core.validators import RegexValidator
from django.db import models
from django.db.models.deletion import CASCADE

from simple_history.models import HistoricalRecords

import debug                            # pyflakes:ignore

from ietf.group.colors import fg_group_colors, bg_group_colors
from ietf.name.models import GroupStateName, GroupTypeName, DocTagName, GroupMilestoneStateName, RoleName, AgendaTypeName
from ietf.person.models import Email, Person
from ietf.utils.mail import formataddr
from ietf.utils import log
from ietf.utils.models import ForeignKey, OneToOneField


class GroupInfo(models.Model):
    time = models.DateTimeField(default=datetime.datetime.now)
    name = models.CharField(max_length=80)
    state = ForeignKey(GroupStateName, null=True)
    type = ForeignKey(GroupTypeName, null=True)
    parent = ForeignKey('Group', blank=True, null=True)
    description = models.TextField(blank=True)
    list_email = models.CharField(max_length=64, blank=True)
    list_subscribe = models.CharField(max_length=255, blank=True)
    list_archive = models.CharField(max_length=255, blank=True)
    comments = models.TextField(blank=True)

    unused_states = models.ManyToManyField('doc.State', help_text="Document states that have been disabled for the group.", blank=True)
    unused_tags = models.ManyToManyField(DocTagName, help_text="Document tags that have been disabled for the group.", blank=True)

    def __unicode__(self):
        return self.name

    def ad_role(self):
        return self.role_set.filter(name='ad').first()

    @property
    def features(self):
        if not hasattr(self, "features_cache"):
            self.features_cache = GroupFeatures.objects.get(type=self.type)
        return self.features_cache

    def about_url(self):
        # bridge gap between group-type prefixed URLs and /group/ ones
        from django.urls import reverse as urlreverse
        kwargs = { 'acronym': self.acronym }
        if self.features.acts_like_wg:
            kwargs["group_type"] = self.type_id
        return urlreverse(self.features.about_page, kwargs=kwargs)

    def interim_approval_roles(self):
        return list(set([ role for role in self.parent.role_set.filter(name__in=['ad', 'chair']) ]))

    def is_bof(self):
        return (self.state.slug in ["bof", "bof-conc"])

    class Meta:
        abstract = True

class GroupManager(models.Manager):
    def active_wgs(self):
        return self.get_queryset().filter(type='wg', state__in=('bof','proposed','active'))

class Group(GroupInfo):
    objects = GroupManager()

    acronym = models.SlugField(max_length=40, unique=True, db_index=True)
    charter = OneToOneField('doc.Document', related_name='chartered_group', blank=True, null=True)

    def latest_event(self, *args, **filter_args):
        """Get latest event of optional Python type and with filter
        arguments, e.g. g.latest_event(type="xyz") returns a GroupEvent
        while g.latest_event(ChangeStateGroupEvent, type="xyz") returns a
        ChangeStateGroupEvent event."""
        model = args[0] if args else GroupEvent
        e = model.objects.filter(group=self).filter(**filter_args).order_by('-time', '-id')[:1]
        return e[0] if e else None

    def has_role(self, user, role_names):
        if isinstance(role_names, str) or isinstance(role_names, unicode):
            role_names = [role_names]
        return user.is_authenticated and self.role_set.filter(name__in=role_names, person__user=user).exists()

    def is_decendant_of(self, sought_parent):
        parent = self.parent
        decendants = [ self, ]
        while (parent != None) and (parent not in decendants):
            decendants = [ parent ] + decendants
            if parent.acronym == sought_parent:
                return True
            parent = parent.parent
        log.assertion('parent not in decendants')
        return False

    def get_chair(self):
        chair = self.role_set.filter(name__slug='chair')[:1]
        return chair and chair[0] or None

    # these are copied to Group because it is still proxied.
    @property
    def upcase_acronym(self):
        return self.acronym.upper()

    @property
    def fg_color(self):
        return fg_group_colors[self.upcase_acronym]

    @property
    def bg_color(self):
        return bg_group_colors[self.upcase_acronym]

    def json_url(self):
        return "/group/%s.json" % (self.acronym,)

    def json_dict(self, host_scheme):
        group1= dict()
        group1['href'] = urljoin(host_scheme, self.json_url())
        group1['acronym'] = self.acronym
        group1['name']    = self.name
        group1['state']   = self.state.slug
        group1['type']    = self.type.slug
        if self.parent is not None:
            group1['parent_href']  = urljoin(host_scheme, self.parent.json_url())
        # uncomment when people URL handle is created
        try:
            if self.ad_role() is not None:
                group1['ad_href']      = urljoin(host_scheme, self.ad_role().person.json_url())
        except Person.DoesNotExist:
            pass
        group1['list_email'] = self.list_email
        group1['list_subscribe'] = self.list_subscribe
        group1['list_archive'] = self.list_archive
        group1['comments']     = self.comments
        return group1

    def has_tools_page(self):
        return self.type_id in ['wg', ] and self.state_id in ['active', 'dormant', 'replaced', 'conclude']

    def liaison_approvers(self):
        '''Returns roles that have liaison statement approval authority for group'''

        # a list of tuples, group query kwargs, role query kwargs
        GROUP_APPROVAL_MAPPING = [
            ({'acronym':'ietf'},{'name':'chair'}),
            ({'acronym':'iab'},{'name':'chair'}),
            ({'type':'area'},{'name':'ad'}),
            ({'type':'wg'},{'name':'ad'}), ]
        
        for group_kwargs,role_kwargs in GROUP_APPROVAL_MAPPING:
            if self in Group.objects.filter(**group_kwargs):
                # TODO is there a cleaner way?
                if self.type == 'wg':
                    return self.parent.role_set.filter(**role_kwargs)
                else:
                    return self.role_set.filter(**role_kwargs)
        return self.role_set.none()

    def status_for_meeting(self,meeting):
        end_date = meeting.end_date()+datetime.timedelta(days=1)
        previous_meeting = meeting.previous_meeting()
        status_events = self.groupevent_set.filter(type='status_update',time__lte=end_date).order_by('-time')
        if previous_meeting:
            status_events = status_events.filter(time__gte=previous_meeting.end_date()+datetime.timedelta(days=1))
        return status_events.first()

    def get_description(self):
        """
        Return self.description if set, otherwise the first paragraph of the
        charter if any, else a short error message.  Used to provide a
        fallback for self.description in group.resources.GroupResource.
        """
        desc = 'No description available'
        if self.description:
            desc = self.description
        elif self.charter:
            path = self.charter.get_file_name()
            if os.path.exists(path):
                text = self.charter.text()
                # split into paragraphs and grab the first non-empty one
                if text:
                    desc = [ p for p in re.split('\r?\n\s*\r?\n\s*', text) if p.strip() ][0]
        return desc


validate_comma_separated_materials = RegexValidator(
    regex=r"[a-z0-9_-]+(,[a-z0-9_-]+)*",
    message="Enter a comma-separated list of material types",
    code='invalid',
)
validate_comma_separated_roles = RegexValidator(
    regex=r"[a-z0-9_-]+(,[a-z0-9_-]+)*",
    message="Enter a comma-separated list of role slugs",
    code='invalid',
)

class GroupFeatures(models.Model):
    type = OneToOneField(GroupTypeName, primary_key=True, null=False, related_name='features')
    history = HistoricalRecords()
    #
    has_milestones          = models.BooleanField("Milestones", default=False)
    has_chartering_process  = models.BooleanField("Chartering", default=False)
    has_documents           = models.BooleanField("Documents",  default=False) # i.e. drafts/RFCs
    has_dependencies        = models.BooleanField("Dependencies",default=False)
    has_session_materials   = models.BooleanField("Sess Matrl.",  default=False)
    has_nonsession_materials= models.BooleanField("Other Matrl.",  default=False)
    has_meetings            = models.BooleanField("Meetings",   default=False)
    has_reviews             = models.BooleanField("Reviews",    default=False)
    has_default_jabber      = models.BooleanField("Jabber",     default=False)
    #
    acts_like_wg            = models.BooleanField("WG-Like",    default=False)
    create_wiki             = models.BooleanField("Wiki",       default=False)
    custom_group_roles      = models.BooleanField("Cust. Roles",default=False)
    customize_workflow      = models.BooleanField("Workflow",   default=False)
    is_schedulable          = models.BooleanField("Schedulable",default=False)
    show_on_agenda          = models.BooleanField("On Agenda",  default=False)
    req_subm_approval       = models.BooleanField("Subm. Approval",  default=False)
    #
    agenda_type             = models.ForeignKey(AgendaTypeName, null=True, default="ietf", on_delete=CASCADE)
    about_page              = models.CharField(max_length=64, blank=False, default="ietf.group.views.group_about" )
    default_tab             = models.CharField(max_length=64, blank=False, default="ietf.group.views.group_about" )
    material_types          = jsonfield.JSONField(max_length=64, blank=False, default=["slides"])
    admin_roles             = jsonfield.JSONField(max_length=64, blank=False, default=["chair"])
    docman_roles            = jsonfield.JSONField(max_length=128, blank=False, default=["ad","chair","delegate","secr"])
    groupman_roles          = jsonfield.JSONField(max_length=128, blank=False, default=["ad","chair",])
    matman_roles            = jsonfield.JSONField(max_length=128, blank=False, default=["ad","chair","delegate","secr"])
    role_order              = jsonfield.JSONField(max_length=128, blank=False, default=["chair","secr","member"],
                                                help_text="The order in which roles are shown, for instance on photo pages.  Enter valid JSON.")


class GroupHistory(GroupInfo):
    group = ForeignKey(Group, related_name='history_set')
    acronym = models.CharField(max_length=40)

    class Meta:
        verbose_name_plural="group histories"

class GroupURL(models.Model):
    group = ForeignKey(Group)
    name = models.CharField(max_length=255)
    url = models.URLField()

    def __unicode__(self):
        return u"%s (%s)" % (self.url, self.name)

class GroupMilestoneInfo(models.Model):
    group = ForeignKey(Group)
    # a group has two sets of milestones, current milestones
    # (active/under review/deleted) and charter milestones (active
    # during a charter/recharter event), events for charter milestones
    # are stored on the charter document
    state = ForeignKey(GroupMilestoneStateName)
    desc = models.CharField(verbose_name="Description", max_length=500)
    due = models.DateField()
    resolved = models.CharField(max_length=50, blank=True, help_text="Explanation of why milestone is resolved (usually \"Done\"), or empty if still due.")

    docs = models.ManyToManyField('doc.Document', blank=True)

    def __unicode__(self):
        return self.desc[:20] + "..."
    class Meta:
        abstract = True
        ordering = ['due', 'id']

class GroupMilestone(GroupMilestoneInfo):
    time = models.DateTimeField(auto_now=True)

class GroupMilestoneHistory(GroupMilestoneInfo):
    time = models.DateTimeField()
    milestone = ForeignKey(GroupMilestone, related_name="history_set")

class GroupStateTransitions(models.Model):
    """Captures that a group has overriden the default available
    document state transitions for a certain state."""
    group = ForeignKey(Group)
    state = ForeignKey('doc.State', help_text="State for which the next states should be overridden")
    next_states = models.ManyToManyField('doc.State', related_name='previous_groupstatetransitions_states')

    def __unicode__(self):
        return u'%s "%s" -> %s' % (self.group.acronym, self.state.name, [s.name for s in self.next_states.all()])

GROUP_EVENT_CHOICES = [
    ("changed_state", "Changed state"),
    ("added_comment", "Added comment"),
    ("info_changed", "Changed metadata"),
    ("requested_close", "Requested closing group"),
    ("changed_milestone", "Changed milestone"),
    ("sent_notification", "Sent notification"),
    ("status_update", "Status update"),
    ]

class GroupEvent(models.Model):
    """An occurrence for a group, used for tracking who, when and what."""
    group = ForeignKey(Group)
    time = models.DateTimeField(default=datetime.datetime.now, help_text="When the event happened")
    type = models.CharField(max_length=50, choices=GROUP_EVENT_CHOICES)
    by = ForeignKey(Person)
    desc = models.TextField()

    def __unicode__(self):
        return u"%s %s at %s" % (self.by.plain_name(), self.get_type_display().lower(), self.time)

    class Meta:
        ordering = ['-time', 'id']

class ChangeStateGroupEvent(GroupEvent):
    state = ForeignKey(GroupStateName)

class MilestoneGroupEvent(GroupEvent):
    milestone = ForeignKey(GroupMilestone)

class Role(models.Model):
    name = ForeignKey(RoleName)
    group = ForeignKey(Group)
    person = ForeignKey(Person)
    email = ForeignKey(Email, help_text="Email address used by person for this role.")
    def __unicode__(self):
        return u"%s is %s in %s" % (self.person.plain_name(), self.name.name, self.group.acronym or self.group.name)

    def formatted_ascii_email(self):
        return email.utils.formataddr((self.person.plain_ascii(), self.email.address))

    def formatted_email(self):
        return formataddr((self.person.plain_name(), self.email.address))

    class Meta:
        ordering = ['name_id', ]

class RoleHistory(models.Model):
    # RoleHistory doesn't have a time field as it's not supposed to be
    # used on its own - there should always be a GroupHistory
    # accompanying a change in roles, so lookup the appropriate
    # GroupHistory instead
    name = ForeignKey(RoleName)
    group = ForeignKey(GroupHistory)
    person = ForeignKey(Person)
    email = ForeignKey(Email, help_text="Email address used by person for this role.")
    def __unicode__(self):
        return u"%s is %s in %s" % (self.person.plain_name(), self.name.name, self.group.acronym)

    class Meta:
        verbose_name_plural = "role histories"
