import requests

from . import channels
from .models import MessageSettings, MessageChannel

from django import forms
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Fieldset, Submit, Div, Field, HTML
from datetime import datetime


class MessageSettingsForm(forms.ModelForm):
    class Meta:
        model = MessageSettings
        fields = [
            'max_length', 'max_retry', 'retry_interval', 'concatenate_status_change', 'status_change_delay',
            'concatenate_backup_fails', 'backup_fails_delay', 'daily_report_time'
        ]

    def __init__(self, *args, **kwargs):
        super(MessageSettingsForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.layout = Layout(
            Fieldset(
                'Message Settings',
            ),
            Div(
                Div(Field('max_length'), css_class='col-md-6'),
                Div(Field('daily_report_time'), css_class='col-md-6'),
                css_class='row'),
            Div(
                Div(Field('max_retry'), css_class='col-md-6'),
                Div(Field('retry_interval'), css_class='col-md-6'),
                css_class='row'),
            Div(
                Div(
                    Div(
                        Div(Field('concatenate_status_change'), css_class='col-md-12'),
                        Div(Field('status_change_delay'), css_class='col-md-12'),
                        css_class='row'),
                    css_class='col-md-6'),
                Div(
                    Div(
                        Div(Field('concatenate_backup_fails'), css_class='col-md-12'),
                        Div(Field('backup_fails_delay'), css_class='col-md-12'),
                        css_class='row'),
                    css_class='col-md-6'),
                css_class='row'),
            Div(
                Div(
                    Submit('submit', 'Save', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/system/notifications/">Back</a> '),
                    css_class='col-md-12'
                ),
            css_class='row')
        )

    def clean(self):
        cleaned_data = super().clean()

        max_length = cleaned_data.get('max_length')
        if max_length is not None and (max_length < 500 or max_length > 2000):
            self.add_error('max_length', 'Max length must be between 500 and 2000.')

        daily_report_time = cleaned_data.get('daily_report_time')
        if daily_report_time is not None:
            try:
                datetime.strptime(daily_report_time, '%H:%M')
            except ValueError:
                self.add_error('daily_report_time', 'Invalid time format. Use HH:MM.')

        max_retry = cleaned_data.get('max_retry')
        if max_retry is not None and (max_retry < 0 or max_retry > 5):
            self.add_error('max_retry', 'Max retry must be between 0 and 5.')

        retry_interval = cleaned_data.get('retry_interval')
        if retry_interval is not None and (retry_interval < 30 or retry_interval > 600):
            self.add_error('retry_interval', 'Retry interval must be between 30 and 600 seconds.')

        status_change_delay = cleaned_data.get('status_change_delay')
        if status_change_delay is not None and (status_change_delay < 60 or status_change_delay > 600):
            self.add_error('status_change_delay', 'Status change delay must be between 60 and 600 seconds.')

        backup_fails_delay = cleaned_data.get('backup_fails_delay')
        if backup_fails_delay is not None and (backup_fails_delay < 60 or backup_fails_delay > 3600):
            self.add_error('backup_fails_delay', 'Backup fails delay must be between 60 and 3600 seconds.')
        return cleaned_data


class MessageChannelForm(forms.ModelForm):
    # Stored encrypted, which makes the model field a TextField; it stays a
    # one-line input here, and the length rule below still applies
    token = forms.CharField(required=False, widget=forms.TextInput)

    class Meta:
        model = MessageChannel
        fields = [
            'name', 'enabled', 'channel_type', 'destination', 'token', 'status_change_offline', 'status_change_online',
            'backup_fail', 'daily_status_report', 'daily_backup_report', 'backup_success', 'command_failed', 'firmware_update',
            'server_url', 'priority'
        ]

    def __init__(self, *args, **kwargs):
        super(MessageChannelForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.fields['enabled'].label = 'Channel Enabled'
        self.fields['server_url'].required = False
        self.fields['priority'].required = False
        # New channels: email, ntfy and Telegram (CallMeBot was removed)
        self.fields['channel_type'].choices = [c for c in self.fields['channel_type'].choices if c[0] != 'callmebot']
        self.helper.layout = Layout(
            Fieldset(
                'Message Channel',
            ),
            Div(
                Div(Field('name'), css_class='col-md-6'),
                Div(Field('channel_type'), css_class='col-md-6'),
                css_class='row'),
            Div(
                Div(Field('destination'), css_class='col-md-6'),
                Div(Field('token'), css_class='col-md-6'),
                css_class='row'),

            Div(
                Div(
                    Div(HTML('<h4>Notification Settings</h4>'), css_class='col-md-12'),

                    Div(Field('status_change_offline'), css_class='col-md-6'),
                    Div(Field('status_change_online'), css_class='col-md-6'),
                    Div(Field('daily_status_report'), css_class='col-md-6'),
                    Div(Field('daily_backup_report'), css_class='col-md-6'),
                    Div(Field('backup_fail'), css_class='col-md-6'),
                    Div(Field('backup_success'), css_class='col-md-6'),
                    Div(Field('command_failed'), css_class='col-md-6'),
                    Div(Field('firmware_update'), css_class='col-md-6'),
                    Div(Field('enabled'), css_class='col-md-6'),
                    css_class='row'),
                Div(
                    Submit('submit', 'Save', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/system/notifications/">Back</a> '),
                    css_class='col-md-12'
                ),
            css_class='row')
        )

    def clean(self):
        cleaned_data = super().clean()
        enabled = cleaned_data.get('enabled')

        destination = cleaned_data.get('destination')
        if destination is not None and len(destination) > 100:
            self.add_error('destination', 'Destination must be less than 100 characters.')

        token = cleaned_data.get('token')
        if token is not None and len(token) > 100:
            self.add_error('token', 'Token must be less than 100 characters.')

        channel_type = cleaned_data.get('channel_type')
        if channel_type == 'callmebot':
            raise forms.ValidationError(channels.REMOVED)
        if channel_type != 'ntfy':
            cleaned_data['server_url'] = channels.PUBLIC_NTFY
        else:
            cleaned_data['server_url'] = (cleaned_data.get('server_url') or channels.PUBLIC_NTFY).strip().rstrip('/')
            problem = channels.ntfy_server_errors(cleaned_data['server_url'], token)
            if problem:
                self.add_error('server_url', problem)
        priority = cleaned_data.get('priority') or 3
        if not 1 <= priority <= 5:
            self.add_error('priority', 'Priority is from 1 (lowest) to 5 (urgent).')
        cleaned_data['priority'] = priority
        if channel_type == 'email':
            # A shared mailbox: the destination is its address; no token
            from django.core.validators import validate_email
            try:
                validate_email(destination or '')
            except ValidationError:
                self.add_error('destination', 'Enter the email address to send to, e.g. noc@example.com.')
            self.cleaned_data['token'] = ''
            if self.errors or not enabled:
                return cleaned_data
            from . import email
            try:
                email.send(destination, 'RouterFleet Improved: channel test', 'Test message from RouterFleet')
            except Exception as e:   # noqa: BLE001 - shown, the password scrubbed
                from .models import SmtpSettings
                raise forms.ValidationError(f'Test email failed: {email.scrub(e, SmtpSettings.load())}') from None
            return cleaned_data
        if channel_type == 'ntfy' and not destination:
            self.add_error('destination', 'ntfy requires a topic.')

        for field, error in channels.format_errors(channel_type, token, destination).items():
            self.add_error(field, error)
        if self.errors or not enabled or channel_type not in channels.FORMATS:
            return cleaned_data

        required = {'telegram': ('Telegram', True), 'ntfy': ('ntfy', False)}
        label, needs_token = required[channel_type]
        if not destination or (needs_token and not token):
            raise forms.ValidationError(f'{label} requires {"a token and " if needs_token else "a "}destination.')

        # A message is sent before the channel is saved, so a wrong setting shows now
        try:
            message = channels.send(channel_type, token, destination, 'Test message from RouterFleet',
                                    server_url=cleaned_data.get('server_url'), priority=cleaned_data.get('priority'))
        except requests.exceptions.RequestException as e:
            raise forms.ValidationError(f'Test message failed. Network error: {channels.scrub(e, token)}') from None
        if message.status_code != 200:
            remote_error = channels.scrub(message.text[:200], token) if message.text else 'No error message received'
            raise forms.ValidationError(f'Test message failed. {label} API status code {message.status_code}. Error: {remote_error}')

        return cleaned_data
