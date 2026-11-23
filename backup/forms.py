from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, HTML, Div
from .models import BackupProfile


class BackupProfileForm(forms.ModelForm):
    class Meta:
        model = BackupProfile
        fields = [
            'name', 'daily_backup', 'weekly_backup', 'monthly_backup',
            'daily_retention', 'weekly_retention', 'monthly_retention',
            'retain_backups_on_error', 'keep_unchanged', 'daily_day_monday', 'daily_day_tuesday',
            'daily_day_wednesday', 'daily_day_thursday', 'daily_day_friday',
            'daily_day_saturday', 'daily_day_sunday', 'weekly_day',
            'monthly_day', 'daily_hour', 'weekly_hour', 'monthly_hour',
            'max_retry', 'retry_interval', 'backup_interval', 'retrieve_interval', 'instant_retention',
            'parameter_sensitive', 'parameter_terse'
        ]
        # widgets = {
        #     'weekly_day': forms.Select(),
        #     'monthly_day': forms.Select(),
        #     'daily_hour': forms.Select(choices=HOUR_CHOICES),
        #     'weekly_hour': forms.Select(choices=HOUR_CHOICES),
        #     'monthly_hour': forms.Select(choices=HOUR_CHOICES),
        #     'max_retry': forms.Select(),
        #     'retry_interval': forms.Select(),
        #     'backup_interval': forms.Select(),
        # }

    def __init__(self, *args, **kwargs):
        super(BackupProfileForm, self).__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'post'
        if self.instance.pk and self.instance.name != 'default':
            delete_html = "<a href='#' class='btn btn-outline-danger' data-command='delete' data-rf-call='openCommandDialog'>Delete</a>"
        else:
            delete_html = ''

        self.fields['daily_day_monday'].label = 'Monday'
        self.fields['daily_day_tuesday'].label = 'Tuesday'
        self.fields['daily_day_wednesday'].label = 'Wednesday'
        self.fields['daily_day_thursday'].label = 'Thursday'
        self.fields['daily_day_friday'].label = 'Friday'
        self.fields['daily_day_saturday'].label = 'Saturday'
        self.fields['daily_day_sunday'].label = 'Sunday'
        self.fields['daily_backup'].label = 'Daily'
        self.fields['weekly_backup'].label = 'Weekly'
        self.fields['monthly_backup'].label = 'Monthly'
        self.fields['daily_retention'].label = 'Retention (days)'
        self.fields['weekly_retention'].label = 'Retention (days)'
        self.fields['monthly_retention'].label = 'Retention (days)'
        self.fields['instant_retention'].label = 'Instant Retention (days)'
        self.fields['parameter_sensitive'].label = 'Include passwords and keys'
        self.fields['parameter_sensitive'].help_text = (
            'For device types that have the option (RouterOS: <code>/export show-sensitive</code>): the text backup '
            'also contains passwords, pre-shared keys and private keys, so a restore brings them back. Off: they are '
            'left out, and the backup is safer to look at and share.')
        self.fields['parameter_terse'].label = 'One line per item'
        self.fields['parameter_terse'].help_text = (
            'For device types that have the option (RouterOS: <code>/export terse</code>): every item on a single '
            'line. Easier to compare between backups and to search; the configuration is the same.')
        self.fields['daily_backup'].help_text = 'Back up on the days and at the hour chosen below; keep each for its retention.'
        self.fields['weekly_backup'].help_text = 'One backup a week, kept longer: a second line of history.'
        self.fields['monthly_backup'].help_text = 'One backup a month, kept longest: the long-term archive.'
        for kind in ('daily', 'weekly', 'monthly'):
            self.fields[f'{kind}_retention'].help_text = (
                f'How many days a {kind} backup is kept before it is deleted automatically.')
        self.fields['instant_retention'].help_text = 'How many days a backup made with Backup now is kept.'
        self.fields['retain_backups_on_error'].label = 'Keep old backups while backups are failing'
        self.fields['retain_backups_on_error'].help_text = (
            'While a device\'s latest backup has failed, none of its backups are deleted for being older than their '
            'retention - so a device that is offline or unreachable for weeks does not lose its history. Deleting '
            'resumes after the next successful backup. Off: retention applies regardless (its newest stored '
            'configuration is kept in any case).')
        self.fields['keep_unchanged'].help_text = (
            'Store every scheduled backup in full, also when the configuration did not change. Off: an unchanged '
            'backup is recorded, but its content is not stored again. A device can decide otherwise in its own '
            'settings (Unchanged backups).')
        if self.instance.pk and self.instance.name == 'default':
            self.fields['name'].widget.attrs['readonly'] = True

        self.helper.layout = Layout(
            Div(Div('name', css_class='col-md-12'), css_class='row'),
            Div(Div(HTML('<h5 class="mt-2 mb-1">Which backups to make</h5><p class="small text-muted mb-2">Each kind '
                         'runs on its own schedule and is kept for its own number of days (its retention, set below '
                         'once switched on). Together they give recent backups for every day and older ones '
                         'further apart.</p>'), css_class='col-md-12'), css_class='row'),
            Div(
                Div('daily_backup', css_class='col-md-4'),
                Div('weekly_backup', css_class='col-md-4'),
                Div('monthly_backup', css_class='col-md-4'),
            css_class='row'),

            Div(
                Div(HTML('<hr><h4>Daily Backups</h4>'), css_class='col-md-12'),
                Div('daily_hour', css_class='col-md-6'),
                Div('daily_retention', css_class='col-md-6'),
                Div('daily_day_monday', css_class='col-md-4'),
                Div('daily_day_tuesday', css_class='col-md-4'),
                Div('daily_day_wednesday', css_class='col-md-4'),
                Div('daily_day_thursday',css_class='col-md-4'),
                Div('daily_day_friday', css_class='col-md-4'),
                Div('daily_day_saturday', css_class='col-md-4'),
                Div('daily_day_sunday', css_class='col-md-4'),
                css_id='daily_settings', css_class='row', data_rf_show_when='#id_daily_backup'
            ),

            Div(
                Div(HTML('<hr><h4>Weekly Backups</h4>'), css_class='col-md-12'),
                Div('weekly_hour', css_class='col-md-6'),
                Div('weekly_day', css_class='col-md-6'),
                Div('weekly_retention', css_class='col-md-6'),

                css_id='weekly_settings', css_class='row', data_rf_show_when='#id_weekly_backup'
            ),

            Div(
                Div(HTML('<hr><h4>Monthly Backups</h4>'), css_class='col-md-12'),
                Div('monthly_hour', css_class='col-md-6'),
                Div('monthly_day', css_class='col-md-6'),
                Div('monthly_retention', css_class='col-md-6'),

                css_id='monthly_settings', css_class='row', data_rf_show_when='#id_monthly_backup'
            ),

            Div(
                Div(HTML('<hr><h4>Export options</h4><p class="small text-muted">How the configuration is exported from RouterOS devices.</p>'), css_class='col-md-12'),
                Div('parameter_sensitive', css_class='col-md-6'),
                Div('parameter_terse', css_class='col-md-6'),
                css_id='misc_settings', css_class='row'
            ),

            Div(
                Div(HTML('<hr><h4>Backup Settings</h4>'), css_class='col-md-12'),
                Div('max_retry', css_class='col-md-6'),
                Div('retry_interval', css_class='col-md-6'),
                Div('backup_interval', css_class='col-md-6'),
                Div('retrieve_interval', css_class='col-md-6'),
                Div('instant_retention', css_class='col-md-6'),
                Div('retain_backups_on_error', css_class='col-md-12'),
                Div('keep_unchanged', css_class='col-md-12'),
                css_id='misc_settings', css_class='row'
            ),

            Row(
                Column(
                    Submit('submit', 'Save', css_class='btn btn-success'),
                    HTML(' <a class="btn btn-secondary" href="/backup/profile_list/">Back</a> '),
                    HTML(delete_html),
                    css_class='col-md-12'
                )
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        daily_backup = cleaned_data.get('daily_backup')
        weekly_backup = cleaned_data.get('weekly_backup')
        monthly_backup = cleaned_data.get('monthly_backup')

        daily_day_monday = cleaned_data.get('daily_day_monday')
        daily_day_tuesday = cleaned_data.get('daily_day_tuesday')
        daily_day_wednesday = cleaned_data.get('daily_day_wednesday')
        daily_day_thursday = cleaned_data.get('daily_day_thursday')
        daily_day_friday = cleaned_data.get('daily_day_friday')
        daily_day_saturday = cleaned_data.get('daily_day_saturday')
        daily_day_sunday = cleaned_data.get('daily_day_sunday')
        name = cleaned_data.get('name')

        if self.instance.pk:
            if self.instance.name == 'default' and name != 'default':
                raise forms.ValidationError('You cannot change the default profile name')

        if daily_backup:
            if not daily_day_monday and not daily_day_tuesday and not daily_day_wednesday and not daily_day_thursday and not daily_day_friday and not daily_day_saturday and not daily_day_sunday:
                raise forms.ValidationError('You must select at least one day for daily backups')

        if not daily_backup and not weekly_backup and not monthly_backup:
            raise forms.ValidationError('You must select at least one backup type')

        return cleaned_data
