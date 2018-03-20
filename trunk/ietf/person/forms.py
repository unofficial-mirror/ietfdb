# Copyright The IETF Trust 2017, All Rights Reserved

from __future__ import unicode_literals

from django import forms
from ietf.person.models import Person


class MergeForm(forms.Form):
    source = forms.IntegerField(label='Source Person ID')
    target = forms.IntegerField(label='Target Person ID')

    def clean_source(self):
        return self.get_person(self.cleaned_data['source'])

    def clean_target(self):
        return self.get_person(self.cleaned_data['target'])

    def get_person(self, pk):
        try:
            return Person.objects.get(pk=pk)
        except Person.DoesNotExist:
            raise forms.ValidationError("ID does not exist")
