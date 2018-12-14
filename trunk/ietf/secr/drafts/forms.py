import re
import os

from django import forms

from ietf.doc.models import Document, State
from ietf.name.models import IntendedStdLevelName
from ietf.group.models import Group
from ietf.person.models import Person, Email
from ietf.person.fields import SearchableEmailField
from ietf.secr.groups.forms import get_person


# ---------------------------------------------
# Select Choices
# ---------------------------------------------
WITHDRAW_CHOICES = (('ietf','Withdraw by IETF'),('author','Withdraw by Author'))

# ---------------------------------------------
# Custom Fields
# ---------------------------------------------
class DocumentField(forms.FileField):
    '''A validating document upload field'''

    def __init__(self, unique=False, *args, **kwargs):
        self.extension = kwargs.pop('extension')
        self.filename = kwargs.pop('filename')
        self.rev = kwargs.pop('rev')
        super(DocumentField, self).__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        file = super(DocumentField, self).clean(data,initial)
        if file:
            # validate general file format
            m = re.search(r'.*-\d{2}\.(txt|pdf|ps|xml)', file.name)
            if not m:
                raise forms.ValidationError('File name must be in the form base-NN.[txt|pdf|ps|xml]')

            # ensure file extension is correct
            base,ext = os.path.splitext(file.name)
            if ext != self.extension:
                raise forms.ValidationError('Incorrect file extension: %s' % ext)

            # if this isn't a brand new submission we need to do some extra validations
            if self.filename:
                # validate filename
                if base[:-3] != self.filename:
                    raise forms.ValidationError, "Filename: %s doesn't match Draft filename." % base[:-3]
                # validate revision
                next_revision = str(int(self.rev)+1).zfill(2)
                if base[-2:] != next_revision:
                    raise forms.ValidationError, "Expected revision # %s" % (next_revision)

        return file

class GroupModelChoiceField(forms.ModelChoiceField):
    '''
    Custom ModelChoiceField sets queryset to include all active workgroups and the
    individual submission group, none.  Displays group acronyms as choices.  Call it without the
    queryset argument, for example:

    group = GroupModelChoiceField(required=True)
    '''
    def __init__(self, *args, **kwargs):
        kwargs['queryset'] = Group.objects.filter(type__in=('wg','individ'),state__in=('bof','proposed','active')).order_by('acronym')
        super(GroupModelChoiceField, self).__init__(*args, **kwargs)

    def label_from_instance(self, obj):
        return obj.acronym

class AliasModelChoiceField(forms.ModelChoiceField):
    '''
    Custom ModelChoiceField, just uses Alias name in the select choices as opposed to the
    more confusing alias -> doc format used by DocAlias.__unicode__
    '''
    def label_from_instance(self, obj):
        return obj.name

# ---------------------------------------------
# Forms
# ---------------------------------------------

class AuthorForm(forms.Form):
    '''
    The generic javascript for populating the email list based on the name selected expects to
    see an id_email field
    '''
    person = forms.CharField(max_length=50,widget=forms.TextInput(attrs={'class':'name-autocomplete'}),help_text="To see a list of people type the first name, or last name, or both.")
    email = forms.CharField(widget=forms.Select(),help_text="Select an email.")
    affiliation = forms.CharField(max_length=100, required=False, help_text="Affiliation")
    country = forms.CharField(max_length=255, required=False, help_text="Country")

    # check for id within parenthesis to ensure name was selected from the list
    def clean_person(self):
        person = self.cleaned_data.get('person', '')
        m = re.search(r'(\d+)', person)
        if person and not m:
            raise forms.ValidationError("You must select an entry from the list!")

        # return person object
        return get_person(person)

    # check that email exists and return the Email object
    def clean_email(self):
        email = self.cleaned_data['email']
        try:
            obj = Email.objects.get(address=email)
        except Email.ObjectDoesNoExist:
            raise forms.ValidationError("Email address not found!")

        # return email object
        return obj

class EditModelForm(forms.ModelForm):
    #expiration_date = forms.DateField(required=False)
    state = forms.ModelChoiceField(queryset=State.objects.filter(type='draft'),empty_label=None)
    iesg_state = forms.ModelChoiceField(queryset=State.objects.filter(type='draft-iesg'),empty_label=None)
    group = GroupModelChoiceField(required=True)
    review_by_rfc_editor = forms.BooleanField(required=False)
    shepherd = SearchableEmailField(required=False, only_users=True)

    class Meta:
        model = Document
        fields = ('title','group','ad','shepherd','notify','stream','review_by_rfc_editor','name','rev','pages','intended_std_level','std_level','abstract','internal_comments')

    # use this method to set attrs which keeps other meta info from model.
    def __init__(self, *args, **kwargs):
        super(EditModelForm, self).__init__(*args, **kwargs)
        self.fields['ad'].queryset = Person.objects.filter(role__name='ad').distinct()
        self.fields['title'].label='Document Name'
        self.fields['title'].widget=forms.Textarea()
        self.fields['rev'].widget.attrs['size'] = 2
        self.fields['abstract'].widget.attrs['cols'] = 72
        self.initial['state'] = self.instance.get_state().pk
        self.initial['iesg_state'] = self.instance.get_state('draft-iesg').pk

        # setup special fields
        if self.instance:
            # setup replaced
            self.fields['review_by_rfc_editor'].initial = bool(self.instance.tags.filter(slug='rfc-rev'))

    def save(self, commit=False):
        m = super(EditModelForm, self).save(commit=False)
        state = self.cleaned_data['state']
        iesg_state = self.cleaned_data['iesg_state']

        if 'state' in self.changed_data:
            m.set_state(state)

        # note we're not sending notices here, is this desired
        if 'iesg_state' in self.changed_data:
            m.set_state(iesg_state)

        if 'review_by_rfc_editor' in self.changed_data:
            if self.cleaned_data.get('review_by_rfc_editor',''):
                m.tags.add('rfc-rev')
            else:
                m.tags.remove('rfc-rev')

        if 'shepherd' in self.changed_data:
            email = self.cleaned_data.get('shepherd')
            if email and not email.origin:
                email.origin = 'shepherd: %s' % m.name
                email.save()

        # handle replaced by

        return m

    # field must contain filename of existing draft
    def clean_replaced_by(self):
        name = self.cleaned_data.get('replaced_by', '')
        if name and not Document.objects.filter(name=name):
            raise forms.ValidationError("ERROR: Draft does not exist")
        return name

    def clean(self):
        super(EditModelForm, self).clean()
        cleaned_data = self.cleaned_data
        """
        expiration_date = cleaned_data.get('expiration_date','')
        status = cleaned_data.get('status','')
        replaced = cleaned_data.get('replaced',False)
        replaced_by = cleaned_data.get('replaced_by','')
        replaced_status_object = IDStatus.objects.get(status_id=5)
        expired_status_object = IDStatus.objects.get(status_id=2)
        # this condition seems to be valid
        #if expiration_date and status != expired_status_object:
        #    raise forms.ValidationError('Expiration Date set but status is %s' % (status))
        if status == expired_status_object and not expiration_date:
            raise forms.ValidationError('Status is Expired but Expirated Date is not set')
        if replaced and status != replaced_status_object:
            raise forms.ValidationError('You have checked Replaced but status is %s' % (status))
        if replaced and not replaced_by:
            raise forms.ValidationError('You have checked Replaced but Replaced By field is empty')
        """
        return cleaned_data

class EmailForm(forms.Form):
    # max_lengths come from db limits, cc is not limited
    action = forms.CharField(max_length=255, widget=forms.HiddenInput(), required=False)
    expiration_date = forms.CharField(max_length=255, widget=forms.HiddenInput(), required=False)
    withdraw_type = forms.CharField(max_length=255, widget=forms.HiddenInput(), required=False)
    replaced = forms.CharField(max_length=255, widget=forms.HiddenInput(), required=False)
    replaced_by = forms.CharField(max_length=255, widget=forms.HiddenInput(), required=False)
    filename = forms.CharField(max_length=255, widget=forms.HiddenInput(), required=False)
    to = forms.CharField(max_length=255)
    cc = forms.CharField(required=False)
    subject = forms.CharField(max_length=255)
    body = forms.CharField(widget=forms.Textarea(), strip=False)

    def __init__(self, *args, **kwargs):
        if 'hidden' in kwargs:
            self.hidden = kwargs.pop('hidden')
        else:
            self.hidden = False
        super(EmailForm, self).__init__(*args, **kwargs)

        if self.hidden:
            for key in self.fields.keys():
                self.fields[key].widget = forms.HiddenInput()

class ExtendForm(forms.Form):
    action = forms.CharField(max_length=255, widget=forms.HiddenInput(),initial='extend')
    expiration_date = forms.DateField()

class SearchForm(forms.Form):
    intended_std_level = forms.ModelChoiceField(queryset=IntendedStdLevelName.objects,label="Intended Status",required=False)
    document_title = forms.CharField(max_length=80,label='Document Title',required=False)
    group = forms.CharField(max_length=12,required=False)
    filename = forms.CharField(max_length=80,required=False)
    state = forms.ModelChoiceField(queryset=State.objects.filter(type='draft'),required=False)
    revision_date_start = forms.DateField(label='Revision Date (start)',required=False)
    revision_date_end = forms.DateField(label='Revision Date (end)',required=False)

class WithdrawForm(forms.Form):
    withdraw_type = forms.CharField(widget=forms.Select(choices=WITHDRAW_CHOICES),help_text='Select which type of withdraw to perform.')

