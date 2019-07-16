# Copyright The IETF Trust 2016-2019, All Rights Reserved
# -*- coding: utf-8 -*-


from __future__ import absolute_import, print_function, unicode_literals

import datetime
import itertools
import re
import six

from collections import defaultdict, namedtuple

from django.db.models import Q, Max, F
from django.urls import reverse as urlreverse
from django.contrib.sites.models import Site

import debug                            # pyflakes:ignore

from ietf.group.models import Group, Role
from ietf.doc.models import (Document, ReviewRequestDocEvent, ReviewAssignmentDocEvent, State,
                             LastCallDocEvent, TelechatDocEvent,
                             DocumentAuthor, DocAlias)
from ietf.iesg.models import TelechatDate
from ietf.person.models import Person
from ietf.ietfauth.utils import has_role, is_authorized_in_doc_stream
from ietf.review.models import (ReviewRequest, ReviewAssignment, ReviewRequestStateName, ReviewTypeName, 
                                ReviewerSettings, UnavailablePeriod, ReviewWish, NextReviewerInTeam,
                                ReviewTeamSettings, ReviewSecretarySettings)
from ietf.utils.mail import send_mail, get_email_addresses_from_text
from ietf.doc.utils import extract_complete_replaces_ancestor_mapping_for_docs

def active_review_teams():
    return Group.objects.filter(reviewteamsettings__isnull=False,state="active")

def close_review_request_states():
    return ReviewRequestStateName.objects.filter(used=True).exclude(slug__in=["requested", "assigned"])

def can_request_review_of_doc(user, doc):
    if not user.is_authenticated:
        return False

    if doc.type_id == 'draft' and doc.get_state_slug() != 'active':
        return False

    return (is_authorized_in_doc_stream(user, doc)
            or Role.objects.filter(person__user=user, name="secr", group__in=active_review_teams()).exists())

def can_manage_review_requests_for_team(user, team, allow_personnel_outside_team=True):
    if not user.is_authenticated:
        return False

    return (Role.objects.filter(name="secr", person__user=user, group=team).exists()
            or (allow_personnel_outside_team and has_role(user, "Secretariat")))

def can_access_review_stats_for_team(user, team):
    if not user.is_authenticated:
        return False

    return (Role.objects.filter(name__in=("secr", "reviewer"), person__user=user, group=team).exists()
            or has_role(user, ["Secretariat", "Area Director"]))

def review_assignments_to_list_for_docs(docs):
    assignment_qs = ReviewAssignment.objects.filter(
        state__in=["assigned", "accepted", "part-completed", "completed"],
    ).prefetch_related("result")

    doc_names = [d.name for d in docs]

    return extract_revision_ordered_review_assignments_for_documents_and_replaced(assignment_qs, doc_names)

def augment_review_requests_with_events(review_reqs):
    req_dict = { r.pk: r for r in review_reqs }
    for e in ReviewRequestDocEvent.objects.filter(review_request__in=review_reqs, type__in=["assigned_review_request", "closed_review_request"]).order_by("time"):
        setattr(req_dict[e.review_request_id], e.type + "_event", e)

def no_review_from_teams_on_doc(doc, rev):
    return Group.objects.filter(
        reviewrequest__doc__name=doc.name,
        reviewrequest__requested_rev=rev,
        reviewrequest__state__slug="no-review-version",
    ).distinct()

def unavailable_periods_to_list(past_days=14):
    return UnavailablePeriod.objects.filter(
        Q(end_date=None) | Q(end_date__gte=datetime.date.today() - datetime.timedelta(days=past_days)),
    ).order_by("start_date")

def current_unavailable_periods_for_reviewers(team):
    """Return dict with currently active unavailable periods for reviewers."""
    today = datetime.date.today()

    unavailable_period_qs = UnavailablePeriod.objects.filter(
        Q(end_date__gte=today) | Q(end_date=None),
        Q(start_date__lte=today) | Q(start_date=None),
        team=team,
    ).order_by("end_date")

    res = defaultdict(list)
    for period in unavailable_period_qs:
        res[period.person_id].append(period)

    return res

def reviewer_rotation_list(team, skip_unavailable=False, dont_skip=[]):
    """Returns person id -> index in rotation (next reviewer has index 0)."""
    reviewers = list(Person.objects.filter(role__name="reviewer", role__group=team))
    reviewers.sort(key=lambda p: p.last_name())

    next_reviewer_index = 0

    # now to figure out where the rotation is currently at
    saved_reviewer = NextReviewerInTeam.objects.filter(team=team).select_related("next_reviewer").first()
    if saved_reviewer:
        n = saved_reviewer.next_reviewer

        if n not in reviewers:
            # saved reviewer might not still be here, if not just
            # insert and use that position (Python will wrap around,
            # so no harm done by using the index on the original list
            # afterwards)
            reviewers_with_next = reviewers[:] + [n]
            reviewers_with_next.sort(key=lambda p: p.last_name())
            next_reviewer_index = reviewers_with_next.index(n)
        else:
            next_reviewer_index = reviewers.index(n)

    rotation_list = reviewers[next_reviewer_index:] + reviewers[:next_reviewer_index]

    if skip_unavailable:
        # prune reviewers not in the rotation (but not the assigned
        # reviewer who must have been available for assignment anyway)
        reviewers_to_skip = set()

        unavailable_periods = current_unavailable_periods_for_reviewers(team)
        for person_id, periods in unavailable_periods.items():
            if periods and person_id not in dont_skip:
                reviewers_to_skip.add(person_id)

        days_needed_for_reviewers = days_needed_to_fulfill_min_interval_for_reviewers(team)
        for person_id, days_needed in days_needed_for_reviewers.items():
            if person_id not in dont_skip:
                reviewers_to_skip.add(person_id)

        rotation_list = [p.pk for p in rotation_list if p.pk not in reviewers_to_skip]

    return rotation_list

def days_needed_to_fulfill_min_interval_for_reviewers(team):
    """Returns person_id -> days needed until min_interval is fulfilled
    for reviewer (in case it is necessary to wait, otherwise reviewer
    is absent in result)."""
    latest_assignments = dict(ReviewAssignment.objects.filter(
        review_request__team=team,
    ).values_list("reviewer__person").annotate(Max("assigned_on")))

    min_intervals = dict(ReviewerSettings.objects.filter(team=team).values_list("person_id", "min_interval"))

    now = datetime.datetime.now()

    res = {}
    for person_id, latest_assignment_time in latest_assignments.items():
        if latest_assignment_time is not None:
            min_interval = min_intervals.get(person_id)
            if min_interval is None:
                continue

            days_needed = max(0, min_interval - (now - latest_assignment_time).days)
            if days_needed > 0:
                res[person_id] = days_needed

    return res

ReviewAssignmentData = namedtuple("ReviewAssignmentData", [
    "assignment_pk", "doc_name", "doc_pages", "req_time", "state", "assigned_time", "deadline", "reviewed_rev", "result", "team", "reviewer",
    "late_days",
    "request_to_assignment_days", "assignment_to_closure_days", "request_to_closure_days"])


def extract_review_assignment_data(teams=None, reviewers=None, time_from=None, time_to=None, ordering=[]):
    """Yield data on each review assignment, sorted by (*ordering, assigned_on)
    for easy use with itertools.groupby. Valid entries in *ordering are "team" and "reviewer"."""

    filters = Q()

    if teams:
        filters &= Q(review_request__team__in=teams)

    if reviewers:
        filters &= Q(reviewer__person__in=reviewers)

    if time_from:
        filters &= Q(review_request__time__gte=time_from)

    if time_to:
        filters &= Q(review_request__time__lte=time_to)

    # This doesn't do the left-outer join on docevent that the previous code did. These variables could be renamed
    event_qs = ReviewAssignment.objects.filter(filters)

    event_qs = event_qs.values_list(
        "pk", "review_request__doc__name", "review_request__doc__pages", "review_request__time", "state", "review_request__deadline", "reviewed_rev", "result", "review_request__team",
        "reviewer__person", "assigned_on", "completed_on"
    )

    event_qs = event_qs.order_by(*[o.replace("reviewer", "reviewer__person").replace("team","review_request__team") for o in ordering] + ["review_request__time", "assigned_on", "pk", "completed_on"])

    def positive_days(time_from, time_to):
        if time_from is None or time_to is None:
            return None

        delta = time_to - time_from
        seconds = delta.total_seconds()
        if seconds > 0:
            return seconds / float(24 * 60 * 60)
        else:
            return 0.0

    requested_time = assigned_time = closed_time = None

    for assignment in event_qs:

        assignment_pk, doc_name, doc_pages, req_time, state, deadline, reviewed_rev, result, team, reviewer, assigned_on, completed_on = assignment

        requested_time = req_time
        assigned_time = assigned_on
        closed_time = completed_on

        late_days = positive_days(datetime.datetime.combine(deadline, datetime.time.max), closed_time)
        request_to_assignment_days = positive_days(requested_time, assigned_time)
        assignment_to_closure_days = positive_days(assigned_time, closed_time)
        request_to_closure_days = positive_days(requested_time, closed_time)

        d = ReviewAssignmentData(assignment_pk, doc_name, doc_pages, req_time, state, assigned_time, deadline, reviewed_rev, result, team, reviewer,
                              late_days, request_to_assignment_days, assignment_to_closure_days,
                              request_to_closure_days)

        yield d


def aggregate_raw_period_review_assignment_stats(review_assignment_data, count=None):
    """Take a sequence of review request data from
    extract_review_assignment_data and aggregate them."""

    state_dict = defaultdict(int)
    late_state_dict = defaultdict(int)
    result_dict = defaultdict(int)
    assignment_to_closure_days_list = []
    assignment_to_closure_days_count = 0

    for (assignment_pk, doc, doc_pages, req_time, state, assigned_time, deadline, reviewed_rev, result, team, reviewer,
         late_days, request_to_assignment_days, assignment_to_closure_days, request_to_closure_days) in review_assignment_data:
        if count == "pages":
            c = doc_pages
        else:
            c = 1

        state_dict[state] += c

        if late_days is not None and late_days > 0:
            late_state_dict[state] += c

        if state in ("completed", "part-completed"):
            result_dict[result] += c
            if assignment_to_closure_days is not None:
                assignment_to_closure_days_list.append(assignment_to_closure_days)
                assignment_to_closure_days_count += c

    return state_dict, late_state_dict, result_dict, assignment_to_closure_days_list, assignment_to_closure_days_count

def sum_period_review_assignment_stats(raw_aggregation):
    """Compute statistics from aggregated review request data for one aggregation point."""
    state_dict, late_state_dict, result_dict, assignment_to_closure_days_list, assignment_to_closure_days_count = raw_aggregation

    res = {}
    res["state"] = state_dict
    res["result"] = result_dict

    res["open"] = sum(state_dict.get(s, 0) for s in ("assigned", "accepted"))
    res["completed"] = sum(state_dict.get(s, 0) for s in ("completed", "part-completed"))
    res["not_completed"] = sum(state_dict.get(s, 0) for s in state_dict if s in ("rejected", "withdrawn", "overtaken", "no-response"))

    res["open_late"] = sum(late_state_dict.get(s, 0) for s in ("assigned", "accepted"))
    res["open_in_time"] = res["open"] - res["open_late"]
    res["completed_late"] = sum(late_state_dict.get(s, 0) for s in ("completed", "part-completed"))
    res["completed_in_time"] = res["completed"] - res["completed_late"]

    res["average_assignment_to_closure_days"] = float(sum(assignment_to_closure_days_list)) / (assignment_to_closure_days_count or 1) if assignment_to_closure_days_list else None

    return res

def sum_raw_review_assignment_aggregations(raw_aggregations):
    """Collapse a sequence of aggregations into one aggregation."""
    state_dict = defaultdict(int)
    late_state_dict = defaultdict(int)
    result_dict = defaultdict(int)
    assignment_to_closure_days_list = []
    assignment_to_closure_days_count = 0

    for raw_aggr in raw_aggregations:
        i_state_dict, i_late_state_dict, i_result_dict, i_assignment_to_closure_days_list, i_assignment_to_closure_days_count = raw_aggr
        for s, v in i_state_dict.items():
            state_dict[s] += v
        for s, v in i_late_state_dict.items():
            late_state_dict[s] += v
        for r, v in i_result_dict.items():
            result_dict[r] += v

        assignment_to_closure_days_list.extend(i_assignment_to_closure_days_list)
        assignment_to_closure_days_count += i_assignment_to_closure_days_count

    return state_dict, late_state_dict, result_dict, assignment_to_closure_days_list, assignment_to_closure_days_count

def latest_review_assignments_for_reviewers(team, days_back=365):
    """Collect and return stats for reviewers on latest assignments, in
    extract_review_assignment_data format."""

    extracted_data = extract_review_assignment_data(
        teams=[team],
        time_from=datetime.date.today() - datetime.timedelta(days=days_back),
        ordering=["reviewer"],
    )

    assignment_data_for_reviewers = {
        reviewer: list(reversed(list(req_data_items)))
        for reviewer, req_data_items in itertools.groupby(extracted_data, key=lambda data: data.reviewer)
    }

    return assignment_data_for_reviewers

def email_review_assignment_change(request, review_assignment, subject, msg, by, notify_secretary, notify_reviewer, notify_requested_by):

    system_email = Person.objects.get(name="(System)").formatted_email()

    to = set() 

    def extract_email_addresses(objs):
        for o in objs:
            if o and o.person!=by:
                e = o.formatted_email()
                if e != system_email:
                    to.add(e)

    if notify_secretary:
        rts = ReviewTeamSettings.objects.filter(group=review_assignment.review_request.team).first()
        if rts and rts.secr_mail_alias and rts.secr_mail_alias.strip() != '':
            for addr in get_email_addresses_from_text(rts.secr_mail_alias):
                to.add(addr)
        else:
            extract_email_addresses(Role.objects.filter(name="secr", group=review_assignment.review_request.team).distinct())
    if notify_reviewer:
        extract_email_addresses([review_assignment.reviewer])
    if notify_requested_by:
        extract_email_addresses([review_assignment.review_request.requested_by.email()])
        
    if not to:
        return

    to = list(to)

    url = urlreverse("ietf.doc.views_review.review_request_forced_login", kwargs={ "name": review_assignment.review_request.doc.name, "request_id": review_assignment.review_request.pk })
    url = request.build_absolute_uri(url)
    send_mail(request, to, request.user.person.formatted_email(), subject, "review/review_request_changed.txt", {
        "review_req_url": url,
        "review_req": review_assignment.review_request,
        "msg": msg,
    })
 

def email_review_request_change(request, review_req, subject, msg, by, notify_secretary, notify_reviewer, notify_requested_by):

    """Notify stakeholders about change, skipping a party if the change
    was done by that party."""

    system_email = Person.objects.get(name="(System)").formatted_email()

    to = set() 

    def extract_email_addresses(objs):
        for o in objs:
            if o and o.person!=by:
                e = o.formatted_email()
                if e != system_email:
                    to.add(e)

    if notify_secretary:
        rts = ReviewTeamSettings.objects.filter(group=review_req.team).first()
        if rts and rts.secr_mail_alias and rts.secr_mail_alias.strip() != '':
            for addr in get_email_addresses_from_text(rts.secr_mail_alias):
                to.add(addr)
        else:
            extract_email_addresses(Role.objects.filter(name="secr", group=review_req.team).distinct())
    if notify_reviewer:
        for assignment in review_req.reviewassignment_set.all():
            extract_email_addresses([assignment.reviewer])
    if notify_requested_by:
        extract_email_addresses([review_req.requested_by.email()])
        
    if not to:
        return

    to = list(to)

    url = urlreverse("ietf.doc.views_review.review_request_forced_login", kwargs={ "name": review_req.doc.name, "request_id": review_req.pk })
    url = request.build_absolute_uri(url)
    send_mail(request, to, request.user.person.formatted_email(), subject, "review/review_request_changed.txt", {
                "review_req_url": url,
                "review_req": review_req,
                "msg": msg,
            },
        )

def email_reviewer_availability_change(request, team, reviewer_role, msg, by):
    """Notify possibly both secretary and reviewer about change, skipping
    a party if the change was done by that party."""

    system_email = Person.objects.get(name="(System)").formatted_email()

    to = []

    def extract_email_addresses(objs):
        if any(o.person == by for o in objs if o):
            l = []
        else:
            l = []
            for o in objs:
                if o:
                    e = o.formatted_email()
                    if e != system_email:
                        l.append(e)

        for e in l:
            if e not in to:
                to.append(e)

    extract_email_addresses(Role.objects.filter(name="secr", group=team).distinct())

    extract_email_addresses([reviewer_role])

    if not to:
        return

    subject = "Reviewer availability of {} changed in {}".format(reviewer_role.person, team.acronym)

    url = urlreverse("ietf.group.views.reviewer_overview", kwargs={ "group_type": team.type_id, "acronym": team.acronym })
    url = request.build_absolute_uri(url)
    send_mail(request, to, None, subject, "review/reviewer_availability_changed.txt", {
        "reviewer_overview_url": url,
        "reviewer": reviewer_role.person,
        "team": team,
        "msg": msg,
        "by": by,
    })

def assign_review_request_to_reviewer(request, review_req, reviewer, add_skip=False):
    assert review_req.state_id in ("requested", "assigned")

    if review_req.reviewassignment_set.filter(reviewer=reviewer).exists():
        return

    # Note that assigning a review no longer unassigns other reviews

    if review_req.state_id != 'assigned':
        review_req.state_id = 'assigned'
        review_req.save()
        
    assignment = review_req.reviewassignment_set.create(state_id='assigned', reviewer = reviewer, assigned_on = datetime.datetime.now())

    if reviewer:
        possibly_advance_next_reviewer_for_team(review_req.team, reviewer.person_id, add_skip)

    ReviewRequestDocEvent.objects.create(
        type="assigned_review_request",
        doc=review_req.doc,
        rev=review_req.doc.rev,
        by=request.user.person,
        desc="Request for {} review by {} is assigned to {}".format(
            review_req.type.name,
            review_req.team.acronym.upper(),
            reviewer.person if reviewer else "(None)",
        ),
        review_request=review_req,
        state_id='assigned',
    )

    ReviewAssignmentDocEvent.objects.create(
        type="assigned_review_request",
        doc=review_req.doc,
        rev=review_req.doc.rev,
        by=request.user.person,
        desc="Request for {} review by {} is assigned to {}".format(
            review_req.type.name,
            review_req.team.acronym.upper(),
            reviewer.person if reviewer else "(None)",
        ),
        review_assignment=assignment,
        state_id='assigned',
    )

    msg = "%s has assigned you as a reviewer for this document." % request.user.person.ascii
    prev_team_reviews = ReviewAssignment.objects.filter(
        review_request__doc=review_req.doc,
        state="completed",
        review_request__team=review_req.team,
    )
    if prev_team_reviews.exists():
        msg = msg + '\n\nThis team has completed other reviews of this document:\n'
        for assignment in prev_team_reviews:
            msg += '%s %s -%s %s\n'% (
                     assignment.completed_on.strftime('%d %b %Y'), 
                     assignment.reviewer.person.ascii,
                     assignment.reviewed_rev or assignment.review_request.requested_rev,
                     assignment.result.name,
                   )

    email_review_request_change(
        request, review_req,
        "%s %s assignment: %s" % (review_req.team.acronym.capitalize(), review_req.type.name,review_req.doc.name),
        msg ,
        by=request.user.person, notify_secretary=False, notify_reviewer=True, notify_requested_by=False)

def possibly_advance_next_reviewer_for_team(team, assigned_review_to_person_id, add_skip=False):
    assert assigned_review_to_person_id is not None

    rotation_list = reviewer_rotation_list(team, skip_unavailable=True, dont_skip=[assigned_review_to_person_id])

    def reviewer_at_index(i):
        if not rotation_list:
            return None

        return rotation_list[i % len(rotation_list)]

    def reviewer_settings_for(person_id):
        return (ReviewerSettings.objects.filter(team=team, person=person_id).first()
                or ReviewerSettings(team=team, person_id=person_id))

    current_i = 0

    if assigned_review_to_person_id == reviewer_at_index(current_i):
        # move 1 ahead
        current_i += 1

    if add_skip:
        settings = reviewer_settings_for(assigned_review_to_person_id)
        settings.skip_next += 1
        settings.save()

    if not rotation_list:
        return

    while True:
        # as a clean-up step go through any with a skip next > 0
        current_reviewer_person_id = reviewer_at_index(current_i)
        settings = reviewer_settings_for(current_reviewer_person_id)
        if settings.skip_next > 0:
            settings.skip_next -= 1
            settings.save()
            current_i += 1
        else:
            nr = NextReviewerInTeam.objects.filter(team=team).first() or NextReviewerInTeam(team=team)
            nr.next_reviewer_id = current_reviewer_person_id
            nr.save()

            break

def close_review_request(request, review_req, close_state):
    suggested_req = review_req.pk is None

    review_req.state = close_state
# This field no longer exists, and it's not clear what the later reference was...
#    if close_state.slug == "no-review-version":
#        review_req.reviewed_rev = review_req.requested_rev or review_req.doc.rev # save rev for later reference
    review_req.save()

    if not suggested_req:
        ReviewRequestDocEvent.objects.create(
            type="closed_review_request",
            doc=review_req.doc,
            rev=review_req.doc.rev,
            by=request.user.person,
            desc="Closed request for {} review by {} with state '{}'".format(
                review_req.type.name, review_req.team.acronym.upper(), close_state.name),
            review_request=review_req,
            state=review_req.state,
        )

        for assignment in review_req.reviewassignment_set.filter(state_id__in=['assigned','accepted']):
            assignment.state_id = 'withdrawn'
            assignment.save()
            ReviewAssignmentDocEvent.objects.create(
                type='closed_review_assignment',
                doc=review_req.doc,
                rev=review_req.doc.rev,
                by=request.user.person,
                desc="Request closed, assignment withdrawn: {} {} {} review".format(assignment.reviewer.person.plain_name(), assignment.review_request.type.name, assignment.review_request.team.acronym.upper()),
                review_assignment=assignment,
                state=assignment.state,
            )

        email_review_request_change(
            request, review_req,
            "Closed review request for {}: {}".format(review_req.doc.name, close_state.name),
            "Review request has been closed by {}.".format(request.user.person),
            by=request.user.person, notify_secretary=False, notify_reviewer=True, notify_requested_by=True)

def suggested_review_requests_for_team(team):

    if not team.reviewteamsettings.autosuggest:
        return []

    system_person = Person.objects.get(name="(System)")

    seen_deadlines = {}

    requests = {}

    now = datetime.datetime.now()

    reviewable_docs_qs = Document.objects.filter(type="draft").exclude(stream="ise")

    requested_state = ReviewRequestStateName.objects.get(slug="requested", used=True)

    last_call_type = ReviewTypeName.objects.get(slug="lc")
    if last_call_type in team.reviewteamsettings.review_types.all():
        # in Last Call
        last_call_docs = reviewable_docs_qs.filter(
            states=State.objects.get(type="draft-iesg", slug="lc", used=True)
        )
        last_call_expiry_events = { e.doc_id: e for e in LastCallDocEvent.objects.order_by("time", "id") }
        for doc in last_call_docs:
            e = last_call_expiry_events[doc.pk] if doc.pk in last_call_expiry_events else LastCallDocEvent(expires=now, time=now)

            deadline = e.expires.date()

            if deadline > seen_deadlines.get(doc.pk, datetime.date.max) or deadline < now.date():
                continue

            requests[doc.pk] = ReviewRequest(
                time=e.time,
                type=last_call_type,
                doc=doc,
                team=team,
                deadline=deadline,
                requested_by=system_person,
                state=requested_state,
            )

            seen_deadlines[doc.pk] = deadline


    telechat_type = ReviewTypeName.objects.get(slug="telechat")
    if telechat_type in team.reviewteamsettings.review_types.all():
        # on Telechat Agenda
        telechat_dates = list(TelechatDate.objects.active().order_by('date').values_list("date", flat=True)[:4])

        telechat_deadline_delta = datetime.timedelta(days=2)

        telechat_docs = reviewable_docs_qs.filter(
            docevent__telechatdocevent__telechat_date__in=telechat_dates
        )

        # we need to check the latest telechat event for each document
        # scheduled for the telechat, as the appearance might have been
        # cancelled/moved
        telechat_events = TelechatDocEvent.objects.filter(
            # turn into list so we don't get a complex and slow join sent down to the DB
            doc__id__in=list(telechat_docs.values_list("pk", flat=True)),
        ).values_list(
            "doc", "pk", "time", "telechat_date"
        ).order_by("doc", "-time", "-id").distinct()

        for doc_pk, events in itertools.groupby(telechat_events, lambda t: t[0]):
            _, _, event_time, event_telechat_date = list(events)[0]

            deadline = None
            if event_telechat_date in telechat_dates:
                deadline = event_telechat_date - telechat_deadline_delta

            if not deadline or deadline > seen_deadlines.get(doc_pk, datetime.date.max):
                continue

            requests[doc_pk] = ReviewRequest(
                time=event_time,
                type=telechat_type,
                doc_id=doc_pk,
                team=team,
                deadline=deadline,
                requested_by=system_person,
                state=requested_state,
            )

            seen_deadlines[doc_pk] = deadline

    # filter those with existing explicit requests 
    existing_requests = defaultdict(list)
    for r in ReviewRequest.objects.filter(doc__id__in=iter(requests.keys()), team=team):
        existing_requests[r.doc_id].append(r)

    def blocks(existing, request):
        if existing.doc_id != request.doc_id:
            return False

        no_review_document = existing.state_id == "no-review-document"
        no_review_rev = ( existing.state_id == "no-review-version") and (not existing.requested_rev or existing.requested_rev == request.doc.rev)
        pending = (existing.state_id == "assigned" 
                   and existing.reviewassignment_set.filter(state_id__in=("assigned", "accepted")).exists()
                   and (not existing.requested_rev or existing.requested_rev == request.doc.rev))
        request_closed = existing.state_id not in ('requested','assigned')
        # at least one assignment was completed for the requested version or the current doc version if no specific version was requested:
        some_assignment_completed = existing.reviewassignment_set.filter(reviewed_rev=existing.requested_rev or existing.doc.rev, state_id='completed').exists()

        return any([no_review_document, no_review_rev, pending, request_closed, some_assignment_completed])

    res = [r for r in requests.values()
           if not any(blocks(e, r) for e in existing_requests[r.doc_id])]
    res.sort(key=lambda r: (r.deadline, r.doc_id), reverse=True)
    return res

def extract_revision_ordered_review_assignments_for_documents_and_replaced(review_assignment_queryset, names):
    """Extracts all review assignments for document names (including replaced ancestors), return them neatly sorted."""

    names = set(names)

    replaces = extract_complete_replaces_ancestor_mapping_for_docs(names)

    assignments_for_each_doc = defaultdict(list)
    replacement_name_set = set(e for l in replaces.values() for e in l) | names
    for r in ( review_assignment_queryset.filter(review_request__doc__name__in=replacement_name_set)
                                        .order_by("-reviewed_rev","-assigned_on", "-id").iterator()):
        assignments_for_each_doc[r.review_request.doc.name].append(r)

    # now collect in breadth-first order to keep the revision order intact
    res = defaultdict(list)
    for name in names:
        front = replaces.get(name, [])
        res[name].extend(assignments_for_each_doc.get(name, []))

        seen = set()

        while front:
            replaces_assignments = []
            next_front = []
            for replaces_name in front:
                if replaces_name in seen:
                    continue

                seen.add(replaces_name)

                assignments = assignments_for_each_doc.get(replaces_name, [])
                if assignments:
                    replaces_assignments.append(assignments)

                next_front.extend(replaces.get(replaces_name, []))

            # in case there are multiple replaces, move the ones with
            # the latest reviews up front
            replaces_assignments.sort(key=lambda l: l[0].assigned_on, reverse=True)

            for assignments in replaces_assignments:
                res[name].extend(assignments)

            # move one level down
            front = next_front

    return res

def extract_revision_ordered_review_requests_for_documents_and_replaced(review_request_queryset, names):
    """Extracts all review requests for document names (including replaced ancestors), return them neatly sorted."""

    names = set(names)

    replaces = extract_complete_replaces_ancestor_mapping_for_docs(names)

    requests_for_each_doc = defaultdict(list)
    for r in review_request_queryset.filter(doc__name__in=set(e for l in replaces.values() for e in l) | names).order_by("-time", "-id").iterator():
        requests_for_each_doc[r.doc.name].append(r)

    # now collect in breadth-first order to keep the revision order intact
    res = defaultdict(list)
    for name in names:
        front = replaces.get(name, [])
        res[name].extend(requests_for_each_doc.get(name, []))

        seen = set()

        while front:
            replaces_reqs = []
            next_front = []
            for replaces_name in front:
                if replaces_name in seen:
                    continue

                seen.add(replaces_name)

                reqs = requests_for_each_doc.get(replaces_name, [])
                if reqs:
                    replaces_reqs.append(reqs)

                next_front.extend(replaces.get(replaces_name, []))

            # in case there are multiple replaces, move the ones with
            # the latest reviews up front
            replaces_reqs.sort(key=lambda l: l[0].time, reverse=True)

            for reqs in replaces_reqs:
                res[name].extend(reqs)

            # move one level down
            front = next_front

    return res

# TODO : Change this field to deal with multiple already assigned reviewers
def setup_reviewer_field(field, review_req):
    field.queryset = field.queryset.filter(role__name="reviewer", role__group=review_req.team)
    one_assignment = review_req.reviewassignment_set.first()
    if one_assignment:
        field.initial = one_assignment.reviewer_id

    choices = make_assignment_choices(field.queryset, review_req)
    if not field.required:
        choices = [("", field.empty_label)] + choices

    field.choices = choices

def get_default_filter_re(person):
    if type(person) != Person:
        person = Person.objects.get(id=person)
    groups_to_avoid =  [ r.group for r in person.role_set.all() if r.name in r.group.features.groupman_roles and r.group.features.acts_like_wg ]
    if not groups_to_avoid:
        return '^draft-%s-.*$' % ( person.last_name().lower(), )
    else:
        return '^draft-(%s|%s)-.*$' % ( person.last_name().lower(), '|'.join(['ietf-%s' % g.acronym for g in groups_to_avoid]))

def make_assignment_choices(email_queryset, review_req):
    doc = review_req.doc
    team = review_req.team

    possible_emails = list(email_queryset)
    possible_person_ids = [e.person_id for e in possible_emails]

    aliases = DocAlias.objects.filter(docs=doc).values_list("name", flat=True)

    # settings
    reviewer_settings = {
        r.person_id: r
        for r in ReviewerSettings.objects.filter(team=team, person__in=possible_person_ids)
    }

    for p in possible_person_ids:
        if p not in reviewer_settings:
            reviewer_settings[p] = ReviewerSettings(team=team, filter_re = get_default_filter_re(p))

    # frequency
    days_needed_for_reviewers = days_needed_to_fulfill_min_interval_for_reviewers(team)

    # rotation
    rotation_index = { p.pk: i for i, p in enumerate(reviewer_rotation_list(team)) }

    # previous review of document
    has_reviewed_previous = ReviewRequest.objects.filter(
        doc=doc,
        reviewassignment__reviewer__person__in=possible_person_ids,
        reviewassignment__state="completed",
        team=team,
    ).distinct()

    if review_req.pk is not None:
        has_reviewed_previous = has_reviewed_previous.exclude(pk=review_req.pk)

    has_reviewed_previous = set(has_reviewed_previous.values_list("reviewassignment__reviewer__person", flat=True))

    # review wishes
    wish_to_review = set(ReviewWish.objects.filter(team=team, person__in=possible_person_ids, doc=doc).values_list("person", flat=True))

    # connections
    connections = {}
    # examine the closest connections last to let them override
    connections[doc.ad_id] = "is associated Area Director"
    for r in Role.objects.filter(group=doc.group_id, person__in=possible_person_ids).select_related("name"):
        connections[r.person_id] = "is group {}".format(r.name)
    if doc.shepherd:
        connections[doc.shepherd.person_id] = "is shepherd of document"
    for author in DocumentAuthor.objects.filter(document=doc, person__in=possible_person_ids).values_list("person", flat=True):
        connections[author] = "is author of document"

    # unavailable periods
    unavailable_periods = current_unavailable_periods_for_reviewers(team)

    # reviewers statistics
    assignment_data_for_reviewers = latest_review_assignments_for_reviewers(team)

    ranking = []
    for e in possible_emails:
        settings = reviewer_settings.get(e.person_id)

        # we sort the reviewers by separate axes, listing the most
        # important things first
        scores = []
        explanations = []

        def add_boolean_score(direction, expr, explanation=None):
            scores.append(direction if expr else -direction)
            if expr and explanation:
                explanations.append(explanation)

        # unavailable for review periods
        periods = unavailable_periods.get(e.person_id, [])
        unavailable_at_the_moment = periods and not (e.person_id in has_reviewed_previous and all(p.availability == "canfinish" for p in periods))
        add_boolean_score(-1, unavailable_at_the_moment)

        def format_period(p):
            if p.end_date:
                res = "unavailable until {}".format(p.end_date.isoformat())
            else:
                res = "unavailable indefinitely"
            return "{} ({})".format(res, p.get_availability_display())

        if periods:
            explanations.append(", ".join(format_period(p) for p in periods))

        # misc
        add_boolean_score(+1, e.person_id in has_reviewed_previous, "reviewed document before")
        add_boolean_score(+1, e.person_id in wish_to_review, "wishes to review document")
        add_boolean_score(-1, e.person_id in connections, connections.get(e.person_id)) # reviewer is somehow connected: bad
        add_boolean_score(-1, settings.filter_re and any(re.search(settings.filter_re, n) for n in aliases), "filter regexp matches")

        # minimum interval between reviews
        days_needed = days_needed_for_reviewers.get(e.person_id, 0)
        scores.append(-days_needed)
        if days_needed > 0:
            explanations.append("max frequency exceeded, ready in {} {}".format(days_needed, "day" if days_needed == 1 else "days"))

        # skip next
        scores.append(-settings.skip_next)
        if settings.skip_next > 0:
            explanations.append("skip next {}".format(settings.skip_next))

        # index
        index = rotation_index.get(e.person_id, 0)
        scores.append(-index)
        explanations.append("#{}".format(index + 1))

        # stats
        stats = []
        assignment_data = assignment_data_for_reviewers.get(e.person_id, [])

        currently_open = sum(1 for d in assignment_data if d.state in ["assigned", "accepted"])
        pages = sum(rd.doc_pages for rd in assignment_data if rd.state in ["assigned", "accepted"])
        if currently_open > 0:
            stats.append("currently {count} open, {pages} pages".format(count=currently_open, pages=pages))
        could_have_completed = [d for d in assignment_data if d.state in ["part-completed", "completed", "no-response"]]
        if could_have_completed:
            no_response     = len([d for d in assignment_data if d.state == 'no-response'])
            if no_response:
                stats.append("%s no response" % no_response)
            part_completed  = len([d for d in assignment_data if d.state == 'part-completed'])
            if part_completed:
                stats.append("%s partially complete" % part_completed)
            completed       = len([d for d in assignment_data if d.state == 'completed'])
            if completed:
                stats.append("%s fully completed" % completed)

        if stats:
            explanations.append(", ".join(stats))

        label = six.text_type(e.person)
        if explanations:
            label = "{}: {}".format(label, "; ".join(explanations))

        ranking.append({
            "email": e,
            "scores": scores,
            "label": label,
        })

    ranking.sort(key=lambda r: r["scores"], reverse=True)

    return [(r["email"].pk, r["label"]) for r in ranking]

def review_assignments_needing_reviewer_reminder(remind_date):
    assignment_qs = ReviewAssignment.objects.filter(
        state__in=("assigned", "accepted"),
        reviewer__person__reviewersettings__remind_days_before_deadline__isnull=False,
        reviewer__person__reviewersettings__team=F("review_request__team"),
    ).values_list("pk", "review_request__deadline", "reviewer__person__reviewersettings__remind_days_before_deadline").distinct()

    assignment_pks = []
    for a_pk, deadline, remind_days in assignment_qs:
        if (deadline - remind_date).days == remind_days:
            assignment_pks.append(a_pk)

    return ReviewAssignment.objects.filter(pk__in=assignment_pks).select_related("reviewer", "reviewer__person", "state", "review_request__team")

def email_reviewer_reminder(review_request):
    team = review_request.team

    deadline_days = (review_request.deadline - datetime.date.today()).days

    subject = "Reminder: deadline for review of {} in {} is {}".format(review_request.doc.name, team.acronym, review_request.deadline.isoformat())

    import ietf.ietfauth.views
    overview_url = urlreverse(ietf.ietfauth.views.review_overview)
    import ietf.doc.views_review
    request_url = urlreverse(ietf.doc.views_review.review_request, kwargs={ "name": review_request.doc.name, "request_id": review_request.pk })

    domain = Site.objects.get_current().domain

    settings = ReviewerSettings.objects.filter(person=review_request.reviewer.person, team=team).first()
    remind_days = settings.remind_days_before_deadline if settings else 0

    send_mail(None, [review_request.reviewer.formatted_email()], None, subject, "review/reviewer_reminder.txt", {
        "reviewer_overview_url": "https://{}{}".format(domain, overview_url),
        "review_request_url": "https://{}{}".format(domain, request_url),
        "review_request": review_request,
        "deadline_days": deadline_days,
        "remind_days": remind_days,
    })

def review_assignments_needing_secretary_reminder(remind_date):
    assignment_qs = ReviewAssignment.objects.filter(
        state__in=("assigned", "accepted"),
        review_request__team__role__person__reviewsecretarysettings__remind_days_before_deadline__isnull=False,
        review_request__team__role__person__reviewsecretarysettings__team=F("review_request__team"),
    ).exclude(
        reviewer=None
    ).values_list("pk", "review_request__deadline", "review_request__team__role", "review_request__team__role__person__reviewsecretarysettings__remind_days_before_deadline").distinct()

    assignment_pks = {}
    for a_pk, deadline, secretary_role_pk, remind_days in assignment_qs:
        if (deadline - remind_date).days == remind_days:
            assignment_pks[a_pk] = secretary_role_pk

    review_assignments = { a.pk: a for a in ReviewAssignment.objects.filter(pk__in=list(assignment_pks.keys())).select_related("reviewer", "reviewer__person", "state", "review_request__team") }
    secretary_roles = { r.pk: r for r in Role.objects.filter(pk__in=list(assignment_pks.values())).select_related("email", "person") }

    return [ (review_assignments[a_pk], secretary_roles[secretary_role_pk]) for a_pk, secretary_role_pk in assignment_pks.items() ]

def email_secretary_reminder(review_request, secretary_role):
    team = review_request.team

    deadline_days = (review_request.deadline - datetime.date.today()).days

    subject = "Reminder: deadline for review of {} in {} is {}".format(review_request.doc.name, team.acronym, review_request.deadline.isoformat())

    import ietf.group.views
    settings_url = urlreverse(ietf.group.views.change_review_secretary_settings, kwargs={ "acronym": team.acronym, "group_type": team.type_id })
    import ietf.doc.views_review
    request_url = urlreverse(ietf.doc.views_review.review_request, kwargs={ "name": review_request.doc.name, "request_id": review_request.pk })

    domain = Site.objects.get_current().domain

    settings = ReviewSecretarySettings.objects.filter(person=secretary_role.person_id, team=team).first()
    remind_days = settings.remind_days_before_deadline if settings else 0

    send_mail(None, [review_request.reviewer.formatted_email()], None, subject, "review/secretary_reminder.txt", {
        "review_request_url": "https://{}{}".format(domain, request_url),
        "settings_url": "https://{}{}".format(domain, settings_url),
        "review_request": review_request,
        "deadline_days": deadline_days,
        "remind_days": remind_days,
    })
