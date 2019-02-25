import os
import re
import datetime
import email
import pytz
import xml2rfc
import tempfile
from email.utils import formataddr
from unidecode import unidecode

from django import forms
from django.conf import settings
from django.utils.html import mark_safe
from django.urls import reverse as urlreverse

import debug                            # pyflakes:ignore

from ietf.doc.models import Document
from ietf.group.models import Group
from ietf.ietfauth.utils import has_role
from ietf.doc.fields import SearchableDocAliasesField
from ietf.ipr.mail import utc_from_string
from ietf.meeting.models import Meeting
from ietf.message.models import Message
from ietf.name.models import FormalLanguageName, GroupTypeName
from ietf.submit.models import Submission, Preapproval
from ietf.submit.utils import validate_submission_rev, validate_submission_document_date
from ietf.submit.parsers.pdf_parser import PDFParser
from ietf.submit.parsers.plain_parser import PlainParser
from ietf.submit.parsers.ps_parser import PSParser
from ietf.submit.parsers.xml_parser import XMLParser
from ietf.utils.draft import Draft

class SubmissionBaseUploadForm(forms.Form):
    xml = forms.FileField(label=u'.xml format', required=True)

    def __init__(self, request, *args, **kwargs):
        super(SubmissionBaseUploadForm, self).__init__(*args, **kwargs)

        self.remote_ip = request.META.get('REMOTE_ADDR', None)

        self.request = request
        self.in_first_cut_off = False
        self.cutoff_warning = ""
        self.shutdown = False
        self.set_cutoff_warnings()

        self.group = None
        self.filename = None
        self.revision = None
        self.title = None
        self.abstract = None
        self.authors = []
        self.parsed_draft = None
        self.file_types = []
        # No code currently (14 Sep 2017) uses this class directly; it is
        # only used through its subclasses.  The two assignments below are
        # set to trigger an exception if it is used directly only to make
        # sure that adequate consideration is made if it is decided to use it
        # directly in the future.  Feel free to set these appropriately to
        # avoid the exceptions in that case:
        self.formats = None             # None will raise an exception in clean() if this isn't changed in a subclass
        self.base_formats = None        # None will raise an exception in clean() if this isn't changed in a subclass

    def set_cutoff_warnings(self):
        now = datetime.datetime.now(pytz.utc)
        meeting = Meeting.get_current_meeting()
        #
        cutoff_00 = meeting.get_00_cutoff()
        cutoff_01 = meeting.get_01_cutoff()
        reopen    = meeting.get_reopen_time()
        #
        cutoff_00_str = cutoff_00.strftime("%Y-%m-%d %H:%M %Z")
        cutoff_01_str = cutoff_01.strftime("%Y-%m-%d %H:%M %Z")
        reopen_str    = reopen.strftime("%Y-%m-%d %H:%M %Z")
        if cutoff_00 == cutoff_01:
            if now.date() >= (cutoff_00.date() - meeting.idsubmit_cutoff_warning_days) and now.date() < cutoff_00.date():
                self.cutoff_warning = ( 'The last submission time for Internet-Drafts before %s is %s.<br/><br/>' % (meeting, cutoff_00_str))
            elif now <= cutoff_00:
                self.cutoff_warning = (
                    'The last submission time for new Internet-Drafts before the meeting is %s.<br/>'
                    'After that, you will not be able to submit drafts until after %s (IETF-meeting local time)' % (cutoff_00_str, reopen_str, ))
        else:
            if now.date() >= (cutoff_00.date() - meeting.idsubmit_cutoff_warning_days) and now.date() < cutoff_00.date():
                self.cutoff_warning = ( 'The last submission time for new documents (i.e., version -00 Internet-Drafts) before %s is %s.<br/><br/>' % (meeting, cutoff_00_str) +
                                        'The last submission time for revisions to existing documents before %s is %s.<br/>' % (meeting, cutoff_01_str) )
            elif now.date() >= cutoff_00.date() and now <= cutoff_01:
                # We are in the first_cut_off
                if now < cutoff_00:
                    self.cutoff_warning = (
                        'The last submission time for new documents (i.e., version -00 Internet-Drafts) before the meeting is %s.<br/>'
                        'After that, you will not be able to submit a new document until after %s (IETF-meeting local time)' % (cutoff_00_str, reopen_str, ))
                else:  # No 00 version allowed
                    self.cutoff_warning = (
                        'The last submission time for new documents (i.e., version -00 Internet-Drafts) was %s.<br/>'
                        'You will not be able to submit a new document until after %s (IETF-meeting local time).<br/><br>'
                        'You can still submit a version -01 or higher Internet-Draft until %s' % (cutoff_00_str, reopen_str, cutoff_01_str, ))
                    self.in_first_cut_off = True
        if now > cutoff_01 and now < reopen:
            self.cutoff_warning = (
                'The last submission time for the I-D submission was %s.<br/><br>'
                'The I-D submission tool will be reopened after %s (IETF-meeting local time).' % (cutoff_01_str, reopen_str))
            self.shutdown = True

    def clean_file(self, field_name, parser_class):
        f = self.cleaned_data[field_name]
        if not f:
            return f

        parsed_info = parser_class(f).critical_parse()
        if parsed_info.errors:
            raise forms.ValidationError(parsed_info.errors)

        return f

    def clean_xml(self):
        return self.clean_file("xml", XMLParser)

    def clean(self):
        if self.shutdown and not has_role(self.request.user, "Secretariat"):
            raise forms.ValidationError('The submission tool is currently shut down')

        for ext in self.formats:
            f = self.cleaned_data.get(ext, None)
            if not f:
                continue
            self.file_types.append('.%s' % ext)
        if not ('.txt' in self.file_types or '.xml' in self.file_types):
            raise forms.ValidationError('Unexpected submission file types; found %s, but %s is required' % (', '.join(self.file_types), ' or '.join(self.base_formats)))

        #debug.show('self.cleaned_data["xml"]')
        if self.cleaned_data.get('xml'):
            #if not self.cleaned_data.get('txt'):
            xml_file = self.cleaned_data.get('xml')
            name, ext = os.path.splitext(os.path.basename(xml_file.name))
            tfh, tfn = tempfile.mkstemp(prefix=name+'-', suffix='.xml')
            try:
                # We need to write the xml file to disk in order to hand it
                # over to the xml parser.  XXX FIXME: investigate updating
                # xml2rfc to be able to work with file handles to in-memory
                # files.
                with open(tfn, 'wb+') as tf:
                    for chunk in xml_file.chunks():
                        tf.write(chunk)
                os.environ["XML_LIBRARY"] = settings.XML_LIBRARY
                try:
                    parser = xml2rfc.XmlRfcParser(str(tfn), quiet=True)
                    self.xmltree = parser.parse(normalize=True)
                    ok, errors = self.xmltree.validate()
                except Exception as exc:
                    raise forms.ValidationError("An exception occurred when trying to process the XML file: %s" % exc)
                if not ok:
                    # Each error has properties:
                    #
                    #     message:  the message text
                    #     domain:   the domain ID (see lxml.etree.ErrorDomains)
                    #     type:     the message type ID (see lxml.etree.ErrorTypes)
                    #     level:    the log level ID (see lxml.etree.ErrorLevels)
                    #     line:     the line at which the message originated (if applicable)
                    #     column:   the character column at which the message originated (if applicable)
                    #     filename: the name of the file in which the message originated (if applicable)
                    raise forms.ValidationError(
                            [ forms.ValidationError("One or more XML validation errors occurred when processing the XML file:") ] +
                            [ forms.ValidationError("%s: Line %s: %s" % (xml_file.name, e.line, e.message), code="%s"%e.type) for e in errors ]
                        )
                self.xmlroot = self.xmltree.getroot()
                draftname = self.xmlroot.attrib.get('docName')
                if draftname is None:
                    raise forms.ValidationError("No docName attribute found in the xml root element")
                revmatch = re.search("-[0-9][0-9]$", draftname)
                if revmatch:
                    self.revision = draftname[-2:]
                    self.filename = draftname[:-3]
                else:
                    self.revision = None
                    self.filename = draftname
                self.title = self.xmlroot.findtext('front/title').strip()
                if type(self.title) is unicode:
                    self.title = unidecode(self.title)
                self.abstract = (self.xmlroot.findtext('front/abstract') or '').strip()
                if type(self.abstract) is unicode:
                    self.abstract = unidecode(self.abstract)
                author_info = self.xmlroot.findall('front/author')
                for author in author_info:
                    info = {
                        "name": author.attrib.get('fullname'),
                        "email": author.findtext('address/email'),
                        "affiliation": author.findtext('organization'),
                        "country": author.findtext('address/postal/country'),
                    }
                    for item in info:
                        if info[item]:
                            info[item] = info[item].strip()
                    self.authors.append(info)
            except forms.ValidationError:
                raise
            finally:
                os.close(tfh)
                os.unlink(tfn)

        if self.cleaned_data.get('txt'):
            # try to parse it
            txt_file = self.cleaned_data['txt']
            txt_file.seek(0)
            bytes = txt_file.read()
            txt_file.seek(0)
            try:
                text = bytes.decode('utf8')
            except UnicodeDecodeError as e:
                raise forms.ValidationError('Failed decoding the uploaded file: "%s"' % str(e))
            #
            self.parsed_draft = Draft(text, txt_file.name)
            self.filename = self.parsed_draft.filename
            self.revision = self.parsed_draft.revision
            self.title    = self.parsed_draft.get_title()


        if not self.filename:
            raise forms.ValidationError("Could not extract a valid draft name from the upload"
                "To fix this in a text upload, please make sure that the full draft name including "
                "revision number appears centered on its own line below the document title on the "
                "first page.  In an xml upload, please make sure that the top-level <rfc/> "
                "element has a docName attribute which provides the full draft name including "
                "revision number.")

        if not self.revision:
            raise forms.ValidationError("Could not extract a valid draft revision from the upload.  "
                "To fix this in a text upload, please make sure that the full draft name including "
                "revision number appears centered on its own line below the document title on the "
                "first page.  In an xml upload, please make sure that the top-level <rfc/> "
                "element has a docName attribute which provides the full draft name including "
                "revision number.")

        if not self.title:
            raise forms.ValidationError("Could not extract a valid title from the upload")

        if self.cleaned_data.get('txt') or self.cleaned_data.get('xml'):
            # check group
            self.group = self.deduce_group()

            # check existing
            existing = Submission.objects.filter(name=self.filename, rev=self.revision).exclude(state__in=("posted", "cancel", "waiting-for-draft"))
            if existing:
                raise forms.ValidationError(mark_safe('A submission with same name and revision is currently being processed. <a href="%s">Check the status here.</a>' % urlreverse("ietf.submit.views.submission_status", kwargs={ 'submission_id': existing[0].pk })))

            # cut-off
            if self.revision == '00' and self.in_first_cut_off:
                raise forms.ValidationError(mark_safe(self.cutoff_warning))

            # check thresholds
            today = datetime.date.today()

            self.check_submissions_tresholds(
                "for the draft %s" % self.filename,
                dict(name=self.filename, rev=self.revision, submission_date=today),
                settings.IDSUBMIT_MAX_DAILY_SAME_DRAFT_NAME, settings.IDSUBMIT_MAX_DAILY_SAME_DRAFT_NAME_SIZE,
            )
            self.check_submissions_tresholds(
                "for the same submitter",
                dict(remote_ip=self.remote_ip, submission_date=today),
                settings.IDSUBMIT_MAX_DAILY_SAME_SUBMITTER, settings.IDSUBMIT_MAX_DAILY_SAME_SUBMITTER_SIZE,
            )
            if self.group:
                self.check_submissions_tresholds(
                    "for the group \"%s\"" % (self.group.acronym),
                    dict(group=self.group, submission_date=today),
                    settings.IDSUBMIT_MAX_DAILY_SAME_GROUP, settings.IDSUBMIT_MAX_DAILY_SAME_GROUP_SIZE,
                )
            self.check_submissions_tresholds(
                "across all submitters",
                dict(submission_date=today),
                settings.IDSUBMIT_MAX_DAILY_SUBMISSIONS, settings.IDSUBMIT_MAX_DAILY_SUBMISSIONS_SIZE,
            )

        return super(SubmissionBaseUploadForm, self).clean()

    def check_submissions_tresholds(self, which, filter_kwargs, max_amount, max_size):
        submissions = Submission.objects.filter(**filter_kwargs)

        if len(submissions) > max_amount:
            raise forms.ValidationError("Max submissions %s has been reached for today (maximum is %s submissions)." % (which, max_amount))
        if sum(s.file_size for s in submissions if s.file_size) > max_size * 1024 * 1024:
            raise forms.ValidationError("Max uploaded amount %s has been reached for today (maximum is %s MB)." % (which, max_size))

    def deduce_group(self):
        """Figure out group from name or previously submitted draft, returns None if individual."""
        name = self.filename
        existing_draft = Document.objects.filter(name=name, type="draft")
        if existing_draft:
            group = existing_draft[0].group
            if group and group.type_id not in ("individ", "area"):
                return group
            else:
                return None
        else:
            name_parts = name.split("-")
            if len(name_parts) < 3:
                raise forms.ValidationError(u"The draft name \"%s\" is missing a third part, please rename it" % name)

            if name.startswith('draft-ietf-') or name.startswith("draft-irtf-"):

                if name_parts[1] == "ietf":
                    group_type = "wg"
                elif name_parts[1] == "irtf":
                    group_type = "rg"

                # first check groups with dashes
                for g in Group.objects.filter(acronym__contains="-", type=group_type):
                    if name.startswith('draft-%s-%s-' % (name_parts[1], g.acronym)):
                        return g

                try:
                    return Group.objects.get(acronym=name_parts[2], type=group_type)
                except Group.DoesNotExist:
                    raise forms.ValidationError('There is no active group with acronym \'%s\', please rename your draft' % name_parts[2])

            elif name.startswith("draft-rfc-"):
                return Group.objects.get(acronym="iesg")
            elif name.startswith("draft-rfc-editor-") or name.startswith("draft-rfced-") or name.startswith("draft-rfceditor-"):
                return Group.objects.get(acronym="rfceditor")
            else:
                ntype = name_parts[1].lower()
                # This covers group types iesg, iana, iab, ise, and others:
                if GroupTypeName.objects.filter(slug=ntype).exists():
                    group = Group.objects.filter(acronym=ntype).first()
                    if group:
                        return group
                    else:
                        raise forms.ValidationError('Draft names starting with draft-%s- are restricted, please pick a differen name' % ntype)
            return None

class SubmissionManualUploadForm(SubmissionBaseUploadForm):
    xml = forms.FileField(label=u'.xml format', required=False) # xml field with required=False instead of True
    txt = forms.FileField(label=u'.txt format', required=False)
    pdf = forms.FileField(label=u'.pdf format', required=False)
    ps  = forms.FileField(label=u'.ps format', required=False)

    def __init__(self, request, *args, **kwargs):
        super(SubmissionManualUploadForm, self).__init__(request, *args, **kwargs)
        self.formats = ['txt', 'pdf', 'xml', 'ps', ]
        self.base_formats = ['txt', 'xml', ]

    def clean_txt(self):
        return self.clean_file("txt", PlainParser)

    def clean_pdf(self):
        return self.clean_file("pdf", PDFParser)

    def clean_ps(self):
        return self.clean_file("ps",  PSParser)

class SubmissionAutoUploadForm(SubmissionBaseUploadForm):
    user = forms.EmailField(required=True)

    def __init__(self, request, *args, **kwargs):
        super(SubmissionAutoUploadForm, self).__init__(request, *args, **kwargs)
        self.formats = ['xml', ]
        self.base_formats = ['xml', ]

class NameEmailForm(forms.Form):
    name = forms.CharField(required=True)
    email = forms.EmailField(label=u'Email address', required=True)

    def __init__(self, *args, **kwargs):
        super(NameEmailForm, self).__init__(*args, **kwargs)

        self.fields["name"].widget.attrs["class"] = "name"
        self.fields["email"].widget.attrs["class"] = "email"

    def clean_name(self):
        return self.cleaned_data["name"].replace("\n", "").replace("\r", "").replace("<", "").replace(">", "").strip()

    def clean_email(self):
        return self.cleaned_data["email"].replace("\n", "").replace("\r", "").replace("<", "").replace(">", "").strip()

class AuthorForm(NameEmailForm):
    affiliation = forms.CharField(max_length=100, required=False)
    country = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, **kwargs):
        super(AuthorForm, self).__init__(*args, **kwargs)
        self.fields["email"].required = False

class SubmitterForm(NameEmailForm):
    #Fields for secretariat only
    approvals_received = forms.BooleanField(label=u'Approvals received', required=False, initial=False)

    def cleaned_line(self):
        line = self.cleaned_data["name"]
        email = self.cleaned_data.get("email")
        if email:
            line = formataddr((line, email))
        return line

class ReplacesForm(forms.Form):
    replaces = SearchableDocAliasesField(required=False, help_text="Any drafts that this document replaces (approval required for replacing a draft you are not the author of)")

    def __init__(self, *args, **kwargs):
        self.name = kwargs.pop("name")
        super(ReplacesForm, self).__init__(*args, **kwargs)

    def clean_replaces(self):
        for alias in self.cleaned_data['replaces']:
            if alias.document.name == self.name:
                raise forms.ValidationError("A draft cannot replace itself.")
            if alias.document.type_id != "draft":
                raise forms.ValidationError("A draft can only replace another draft")
            if alias.document.get_state_slug() == "rfc":
                raise forms.ValidationError("A draft cannot replace an RFC")
            if alias.document.get_state_slug('draft-iesg') in ('approved','ann','rfcqueue'):
                raise forms.ValidationError(alias.name+" is approved by the IESG and cannot be replaced")
        return self.cleaned_data['replaces']

class EditSubmissionForm(forms.ModelForm):
    title = forms.CharField(required=True, max_length=255)
    rev = forms.CharField(label=u'Revision', max_length=2, required=True)
    document_date = forms.DateField(required=True)
    pages = forms.IntegerField(required=True)
    formal_languages = forms.ModelMultipleChoiceField(queryset=FormalLanguageName.objects.filter(used=True), widget=forms.CheckboxSelectMultiple, required=False)
    abstract = forms.CharField(widget=forms.Textarea, required=True, strip=False)

    note = forms.CharField(label=mark_safe(u'Comment to the Secretariat'), widget=forms.Textarea, required=False, strip=False)

    class Meta:
        model = Submission
        fields = ['title', 'rev', 'document_date', 'pages', 'formal_languages', 'abstract', 'note']

    def clean_rev(self):
        rev = self.cleaned_data["rev"]

        if len(rev) == 1:
            rev = "0" + rev

        error = validate_submission_rev(self.instance.name, rev)
        if error:
            raise forms.ValidationError(error)

        return rev

    def clean_document_date(self):
        document_date = self.cleaned_data['document_date']
        error = validate_submission_document_date(self.instance.submission_date, document_date)
        if error:
            raise forms.ValidationError(error)

        return document_date

class PreapprovalForm(forms.Form):
    name = forms.CharField(max_length=255, required=True, label="Pre-approved name", initial="draft-")

    def clean_name(self):
        n = self.cleaned_data['name'].strip().lower()

        if not n.startswith("draft-"):
            raise forms.ValidationError("Name doesn't start with \"draft-\".")
        if len(n.split(".")) > 1 and len(n.split(".")[-1]) == 3:
            raise forms.ValidationError("Name appears to end with a file extension .%s - do not include an extension." % n.split(".")[-1])

        components = n.split("-")
        if components[-1] == "00":
            raise forms.ValidationError("Name appears to end with a revision number -00 - do not include the revision.")
        if len(components) < 4:
            raise forms.ValidationError("Name has less than four dash-delimited components - can't form a valid group draft name.")
        if not components[-1]:
            raise forms.ValidationError("Name ends with a dash.")
        acronym = components[2]
        if acronym not in [ g.acronym for g in self.groups ]:
            raise forms.ValidationError("Group acronym not recognized as one you can approve drafts for.")

        if Preapproval.objects.filter(name=n):
            raise forms.ValidationError("Pre-approval for this name already exists.")
        if Submission.objects.filter(state="posted", name=n):
            raise forms.ValidationError("A draft with this name has already been submitted and accepted. A pre-approval would not make any difference.")

        return n


class SubmissionEmailForm(forms.Form):
    '''
    Used to add a message to a submission or to create a new submission.
    This message is NOT a reply to a previous message but has arrived out of band
    
    if submission_pk is None we are startign a new submission and name
    must be unique. Otehrwise the name must match the submission.name.
    '''
    name = forms.CharField(required=True, max_length=255, label="Draft name")
    submission_pk = forms.IntegerField(required=False, widget=forms.HiddenInput())
    direction = forms.ChoiceField(choices=(("incoming", "Incoming"), ("outgoing", "Outgoing")),
                                  widget=forms.RadioSelect)
    message = forms.CharField(required=True, widget=forms.Textarea, strip=False,
                              help_text="Copy the entire message including headers. To do so, view the source, select all, copy then paste into the text area above")
    #in_reply_to = MessageModelChoiceField(queryset=Message.objects,label="In Reply To",required=False)

    def __init__(self, *args, **kwargs):
        super(SubmissionEmailForm, self).__init__(*args, **kwargs)

    def clean_message(self):
        '''Returns a ietf.message.models.Message object'''
        self.message_text = self.cleaned_data['message']
        try:
            message = email.message_from_string(self.message_text)
        except Exception as e:
            self.add_error('message', e)
            return None
            
        for field in ('to','from','subject','date'):
            if not message[field]:
                raise forms.ValidationError('Error parsing email: {} field not found.'.format(field))
        date = utc_from_string(message['date'])
        if not isinstance(date,datetime.datetime):
            raise forms.ValidationError('Error parsing email date field')
        return message

    def clean(self):
        if any(self.errors):
            return self.cleaned_data
        super(SubmissionEmailForm, self).clean()
        name = self.cleaned_data['name']
        match = re.search(r"(draft-[a-z0-9-]*)-(\d\d)", name)
        if not match:
            self.add_error('name', 
                           "Submission name {} must start with 'draft-' and only contain digits, lowercase letters and dash characters and end with revision.".format(name))
        else:
            self.draft_name = match.group(1)    
            self.revision = match.group(2)

            error = validate_submission_rev(self.draft_name, self.revision)
            if error:
                raise forms.ValidationError(error)

        #in_reply_to = self.cleaned_data['in_reply_to']
        #message = self.cleaned_data['message']
        direction = self.cleaned_data['direction']
        if direction != 'incoming' and direction != 'outgoing':
            self.add_error('direction', "Must be one of 'outgoing' or 'incoming'")

        #if in_reply_to:
        #    if direction != 'incoming':
        #        raise forms.ValidationError('Only incoming messages can have In Reply To selected')
        #    date = utc_from_string(message['date'])
        #    if date < in_reply_to.time:
        #        raise forms.ValidationError('The incoming message must have a date later than the message it is replying to')

        return self.cleaned_data

class MessageModelForm(forms.ModelForm):
    in_reply_to_id = forms.CharField(required=False, widget=forms.HiddenInput())
    
    class Meta:
        model = Message
        fields = ['to','frm','cc','bcc','reply_to','subject','body']
        exclude = ['time','by','content_type','related_groups','related_docs']

    def __init__(self, *args, **kwargs):
        super(MessageModelForm, self).__init__(*args, **kwargs)
        self.fields['frm'].label='From'
        self.fields['frm'].widget.attrs['readonly'] = 'True'
        self.fields['reply_to'].widget.attrs['readonly'] = 'True'
