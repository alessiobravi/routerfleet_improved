"""Two preferences of each user: how long a sign-in lasts after the last use
(24 hours unless changed - before, two weeks from signing in for everyone) and
how list pages are drawn (cards, as before). Add-only."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('user_manager', '0007_user_phone')]

    operations = [
        migrations.AddField(
            model_name='useracl', name='session_hours',
            field=models.PositiveIntegerField(choices=[(1, '1 hour'), (4, '4 hours'), (8, '8 hours'), (12, '12 hours'),
                                                       (24, '24 hours'), (72, '3 days'), (168, '1 week'),
                                                       (336, '2 weeks')], default=24)),
        migrations.AddField(
            model_name='useracl', name='list_style',
            field=models.CharField(choices=[('cards', 'Cards'), ('grouped', 'Grouped')], default='cards', max_length=10)),
    ]
