"""A lock shows as a red lock icon, not as a tag."""
from django.test import TestCase
from django.utils import timezone

from router_manager.models import RouterStatus
from routerfleet.testing import make_router, make_user


class LockIconTests(TestCase):
    def test_router_list_shows_a_red_lock_with_the_time(self):
        router = make_router(name='locked-1')
        RouterStatus.objects.filter(router=router).update(backup_lock=timezone.now())
        self.client.force_login(make_user(50))
        self.client.cookies['router_visible_columns'] = '%5B%22name%22%2C%22lock_information%22%5D'
        page = self.client.get('/router/list/')
        self.assertContains(page, 'fa-lock text-danger')
        self.assertContains(page, 'Backup locked since')
        self.assertNotContains(page, '>locked<')
