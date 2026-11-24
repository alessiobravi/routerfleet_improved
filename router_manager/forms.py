from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, HTML
from backup.models import BackupProfile
from .models import Router, RouterGroup, SSHKey
import ipaddress
import re

# A hostname as it may be written (RFC 1123), whether or not it resolves yet:
# a device can be added before its name exists in DNS
HOSTNAME = re.compile(r'^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.?$')

# The test itself is in app.js (no inline script: Content-Security-Policy)
TEST_CONNECTION_HTML = '''
 <button type="button" class="btn btn-outline-primary" id="test-connection"{% if instance.uuid %} data-uuid="{{ instance.uuid }}"{% endif %}
         title="Connects with the values in this form, saves nothing"><i class="fas fa-plug"></i> Test connection</button>
 <span id="test-connection-result" class="d-block mt-2 small" role="status" aria-live="polite"></span>
'''


def _account_hint():
    from django.conf import settings
    from django.utils.html import format_html
    docs = getattr(settings, 'PROJECT_REPOSITORY_URL', '').rstrip('/') + '/blob/main/docs/devices.md#a-dedicated-account'
    return format_html(
        '<i class="fas fa-shield-halved me-1" aria-hidden="true"></i>Best a dedicated account with only the rights '
        'RouterFleet needs, not the administrator: reading the configuration is enough for backups and router '
        'information; updates, commands and the console need more. '
        '<a href="{}" target="_blank" rel="noopener">Examples per vendor</a>', docs)


ACCOUNT_HINT = _account_hint()


class RouterForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, required=False)
    enable_password = forms.CharField(widget=forms.PasswordInput, required=False, label='Enable password',
                                      help_text='Only for device types with an enable step (Cisco), when the account '
                                                'does not land in privileged mode. Empty keeps the stored one.')

    class Meta:
        model = Router
        fields = ['name', 'port', 'address', 'username', 'password', 'ssh_key', 'monitoring', 'router_type', 'enabled',
                  'backup_profile', 'keep_unchanged', 'console_telnet', 'telnet_port', 'enable_password']

    def __init__(self, *args, **kwargs):
        super(RouterForm, self).__init__(*args, **kwargs)
        from router_manager import device_types
        self.fields['router_type'] = forms.ChoiceField(
            choices=[('', '---------')] + device_types.choices(include=self.instance.router_type or None),
            label='Device type', initial=self.instance.router_type or None)
        self._stored_enable_password = self.instance.enable_password if self.instance.pk else None
        self.fields['enable_password'].initial = ''
        if self._stored_enable_password:
            self.fields['enable_password'].widget.attrs['placeholder'] = '************'
        # Only used by the console's Telnet: empty means the standard port
        self.fields['telnet_port'].required = False
        self.fields['telnet_port'].help_text = 'Used only when the Telnet console is allowed.'
        self.fields['username'].help_text = ACCOUNT_HINT
        self.fields['console_telnet'].help_text = ('Telnet sends everything, passwords included, unencrypted. '
                                                   'Off unless the device has no SSH.')
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        if self.instance.pk:
            delete_html = "<a href='#' class='btn btn-outline-danger' data-command='delete' data-rf-call='openCommandDialog'>Delete</a>"
            if self.instance.password:
                self.fields['password'].widget.attrs['placeholder'] = '************'
        else:
            delete_html = ''
        self.fields['keep_unchanged'].required = False
        self.fields['keep_unchanged'].help_text = ('Whether a scheduled backup is stored again when the configuration '
                                                   'did not change. Instant backups are always stored.')
        self.helper.layout = Layout(
            Row(
                Column('name', css_class='col-md-6 mb-0'),
                Column('ssh_key', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('username', css_class='col-md-6 mb-0'),
                Column('password', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('address', css_class='col-md-6 mb-0'),
                Column('port', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),

            Row(
                Column('backup_profile', css_class='col-md-6 mb-0'),
                Column('keep_unchanged', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),
            'router_type',
            'enable_password',
            'monitoring',
            'enabled',
            Row(
                Column('console_telnet', css_class='col-md-8 mb-0'),
                Column('telnet_port', css_class='col-md-4 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column(
                    Submit('submit', 'Save', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/router/list/">Back</a> '),
                    HTML(delete_html),
                    HTML(TEST_CONNECTION_HTML),
                    css_class='col-md-12'),
                css_class='form-row'
            )
        )

    def clean_keep_unchanged(self):
        # Not sent (an older form, a script): as the backup profile says
        return self.cleaned_data.get('keep_unchanged') or 'profile'

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        ssh_key = cleaned_data.get('ssh_key')
        password = cleaned_data.get('password')
        address = cleaned_data.get('address')
        router_type = cleaned_data.get('router_type')
        backup_profile = cleaned_data.get('backup_profile')
        port = cleaned_data.get('port')

        if name:
            name = name.strip()
            cleaned_data['name'] = name

        if address:
            address = address.lower()
            cleaned_data['address'] = address

            try:
                ipaddress.ip_address(address)
            except ValueError:
                if not HOSTNAME.match(address):
                    raise forms.ValidationError('The address field must be a valid hostname or IP address.') from None

        from router_manager import device_types
        chosen_type = device_types.get(router_type)
        # Empty keeps the stored enable password
        if not cleaned_data.get('enable_password'):
            cleaned_data['enable_password'] = self._stored_enable_password
        if chosen_type is not None and not chosen_type.managed:
            cleaned_data['password'] = ''
            cleaned_data['ssh_key'] = None
            cleaned_data['enable_password'] = None
            if backup_profile:
                raise forms.ValidationError(f'{chosen_type.name} devices cannot have a backup profile')
            return cleaned_data
        else:
            if not port:
                raise forms.ValidationError('You must provide a port')
            if not 1 <= port <= 65535:
                raise forms.ValidationError('Invalid port number')

        if ssh_key and password:
            raise forms.ValidationError('You must provide a password or an SSH Key, not both')
        if not ssh_key and not password and not self.instance.password:
            raise forms.ValidationError('You must provide a password or an SSH Key')

        if not password and self.instance.password:
            cleaned_data['password'] = self.instance.password

        if ssh_key and not password:
            cleaned_data['password'] = ''

        telnet_port = cleaned_data.get('telnet_port') or 23
        if not 1 <= telnet_port <= 65535:
            raise forms.ValidationError('Invalid Telnet port number')
        cleaned_data['telnet_port'] = telnet_port

        # Saving never connects to the device: a router that does not answer
        # can still be added, disabled or have its monitoring switched off.
        # "Test connection" checks the credentials on request.
        return cleaned_data


class RouterBulkEditForm(forms.Form):
    # Every field is optional: a blank field means "leave this router's value unchanged"
    port = forms.IntegerField(required=False, min_value=1, max_value=65535)
    username = forms.CharField(required=False, max_length=100)
    password = forms.CharField(required=False, widget=forms.PasswordInput)
    ssh_key = forms.ChoiceField(required=False)
    backup_profile = forms.ChoiceField(required=False)
    monitoring = forms.ChoiceField(required=False, choices=[('', 'Unchanged'), ('true', 'Enable'), ('false', 'Disable')])
    enabled = forms.ChoiceField(required=False, choices=[('', 'Unchanged'), ('true', 'Enable'), ('false', 'Disable')])
    internal_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 4, 'cols': 40}))

    def __init__(self, *args, **kwargs):
        super(RouterBulkEditForm, self).__init__(*args, **kwargs)
        self.fields['ssh_key'].choices = (
            [('', 'Unchanged'), ('clear', 'Clear SSH key')]
            + [(str(ssh_key.uuid), ssh_key.name) for ssh_key in SSHKey.objects.all().order_by('name')]
        )
        self.fields['backup_profile'].choices = (
            [('', 'Unchanged'), ('clear', 'Clear backup profile')]
            + [(str(backup_profile.uuid), backup_profile.name) for backup_profile in BackupProfile.objects.all().order_by('name')]
        )
        self.fields['username'].help_text = ACCOUNT_HINT
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('username', css_class='col-md-6 mb-0'),
                Column('port', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('password', css_class='col-md-6 mb-0'),
                Column('ssh_key', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),
            Row(
                Column('monitoring', css_class='col-md-6 mb-0'),
                Column('enabled', css_class='col-md-6 mb-0'),
                css_class='form-row'
            ),
            'backup_profile',
            'internal_notes',
            Row(
                Column(
                    Submit('submit', 'Apply Changes', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/router/list/">Cancel</a> '),
                    css_class='col-md-12'),
                css_class='form-row'
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        username = cleaned_data.get('username')
        if username:
            username = username.strip()
            cleaned_data['username'] = username

        updatable_fields = ['port', 'username', 'password', 'ssh_key', 'backup_profile', 'monitoring', 'enabled', 'internal_notes']
        if not any(cleaned_data.get(field) for field in updatable_fields):
            raise forms.ValidationError('You must provide at least one field to update')

        ssh_key = cleaned_data.get('ssh_key')
        if cleaned_data.get('password') and ssh_key and ssh_key != 'clear':
            raise forms.ValidationError('You must provide a password or an SSH Key, not both')
        if ssh_key == 'clear' and not cleaned_data.get('password'):
            raise forms.ValidationError('Clearing the SSH key without setting a password would leave the routers without authentication')
        return cleaned_data


class RouterGroupForm(forms.ModelForm):
    class Meta:
        model = RouterGroup
        fields = ['name', 'default_group', 'internal_notes', 'routers']
        widgets = {
            'internal_notes': forms.Textarea(attrs={'rows': 4, 'cols': 40}),
        }

    def __init__(self, *args, **kwargs):
        super(RouterGroupForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        if self.instance.pk:
            delete_html = "<a href='#' class='btn btn-outline-danger' data-command='delete' data-rf-call='openCommandDialog'>Delete</a>"
        else:
            delete_html = ''
        self.helper.layout = Layout(
            'name',
            'internal_notes',
            'routers',
            'default_group',
            Row(
                Column(
                    Submit('submit', 'Save', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/router/group_list/">Back</a> '),
                    HTML(delete_html),
                    css_class='col-md-12'),
                css_class='form-row'
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        name = cleaned_data.get('name')

        if name:
            name = name.strip()
            cleaned_data['name'] = name

        return cleaned_data

    def save(self, commit=True):
        group = super().save(commit=commit)

        # Only one group can be the default, so the others give it up. This belongs
        # in save() and not in clean(): clean() runs before the form has decided
        # whether it accepts the input at all, so a duplicate name - which
        # _post_clean rejects - would have taken the default away from another
        # group without saving anything, and the change log would record a change
        # that never happened.
        #
        # One save() per group, and not a queryset update(): update() sends no
        # signal, so the log would show the new default being set and let the old
        # one silently go false.
        if commit and group.default_group:
            for other in RouterGroup.objects.filter(default_group=True).exclude(pk=group.pk):
                other.default_group = False
                other.save()
        return group


class SSHKeyForm(forms.ModelForm):
    class Meta:
        model = SSHKey
        fields = ['name', 'public_key', 'private_key']
        widgets = {
            'public_key': forms.Textarea(attrs={'rows': 4, 'cols': 40}),
            'private_key': forms.Textarea(attrs={'rows': 4, 'cols': 40}),
        }

    def __init__(self, *args, **kwargs):
        super(SSHKeyForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        if self.instance.pk:
            delete_html = "<a href='#' class='btn btn-outline-danger' data-command='delete' data-rf-call='openCommandDialog'>Delete</a>"
        else:
            delete_html = ''
        self.helper.layout = Layout(
            Row(
                Column('name', css_class='col-md-12 mb-0'),
            ),
            Row(
                Column('public_key', css_class='col-md-12 mb-0'),
            ),
            Row(
                Column('private_key', css_class='col-md-12 mb-0'),
            ),
            Row(
                Column(
                    Submit('submit', 'Save', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/router/ssh_keys/">Back</a> '),
                    HTML(delete_html),
                    css_class='col-md-12'),
                css_class='form-row'
            )
        )
