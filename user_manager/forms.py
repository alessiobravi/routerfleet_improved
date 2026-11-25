import re
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import UserAcl
from django.core.exceptions import ValidationError


class UserAddressLinkForm(forms.ModelForm):
    """The settings every user manages for themselves."""

    class Meta:
        model = UserAcl
        fields = ('address_link',)


PHONE = re.compile(r'^\+?[0-9][0-9 ()./-]{3,28}$')


class UserAclForm(UserCreationForm):
    user_level = forms.ChoiceField(choices=UserAcl.user_level.field.choices, required=True, label="User Level")
    phone = forms.CharField(required=False, max_length=30, label='Phone')

    class Meta(UserCreationForm.Meta):
        model = User
        fields = UserCreationForm.Meta.fields + ('first_name', 'last_name', 'email', 'user_level',)

    def __init__(self, *args, **kwargs):
        self.user_id = kwargs.pop('user_id', None)
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['password1'].required = False
            self.fields['password2'].required = False
            self.fields['username'].widget.attrs['readonly'] = True
        self.fields['email'].required = False
        self.fields['first_name'].label, self.fields['last_name'].label = 'Name', 'Surname'
        if self.instance and self.instance.pk:
            acl = UserAcl.objects.filter(user=self.instance).first()
            self.fields['phone'].initial = acl.phone if acl else ''
        self.fields['email'].help_text = 'For email notifications; each user chooses them in My Settings.'

    def clean_phone(self):
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone and not PHONE.match(phone):
            raise ValidationError('Enter a phone number such as +39 055 123 4567.')
        return phone

    def clean_username(self):
        username = self.cleaned_data.get('username')
        # Case-insensitive: sign-in limits and lockouts match names that way
        if User.objects.filter(username__iexact=username).exclude(pk=self.user_id).exists():
            raise ValidationError("A user with that username already exists.")
        return username

    def save(self, commit=True):
        # The plain model form, not UserCreationForm.save(): that one sets the
        # password from password1 even when it is empty, which would leave an
        # edited user with an unusable password
        user = forms.ModelForm.save(self, commit=False)
        new_password = self.cleaned_data.get("password1")
        if new_password:
            user.set_password(new_password)
        # Always saved: an edit that changes only the email (or nothing but the
        # level) used to be dropped without a word
        user.save()

        # The user's own email channel follows the address
        from message_center import email as email_notifications
        channel = email_notifications.personal_channel(user)
        if channel is not None:
            events = {field for field, _, _ in email_notifications.PERSONAL_EVENTS if getattr(channel, field)}
            email_notifications.save_personal_channel(user, user.email, events)

        if commit:
            user_acl, created = UserAcl.objects.update_or_create(
                user=user,
                defaults={'user_level': self.cleaned_data.get('user_level'),
                          'phone': self.cleaned_data.get('phone', '')}
            )
        return user
