"""The firmware repository and rollouts."""
import hashlib
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from backup_data.models import RouterBackup
from firmware import rollout as engine
from firmware.models import FirmwareImage, FirmwareRollout, RolloutTarget
from router_manager import device_types
from router_manager.models import RouterInformation
from routerfleet.testing import FakeSSHClient, make_profile, make_router, make_user, temporary_media_root

CONTENT = b'\x00firmware-image-bytes' * 100


def make_image(slug='routeros', version='7.18', **fields):
    image = FirmwareImage(device_type=device_types.get(slug), version=version, filename=f'fw-{version}.npk',
                          size=len(CONTENT), sha256=hashlib.sha256(CONTENT).hexdigest(), **fields)
    image.file.save(image.filename, ContentFile(CONTENT), save=False)
    image.save()
    return image


class RepositoryTests(TestCase):
    def setUp(self):
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)
        self.client.force_login(make_user(40))

    def upload(self, **fields):
        data = {'device_type': str(device_types.get('routeros').uuid), 'version': '7.18.2',
                'file': SimpleUploadedFile('routeros-7.18.2-arm64.npk', CONTENT)}
        data.update(fields)
        return self.client.post('/system/firmware/upload/', data)

    def test_upload_stores_the_checksum(self):
        response = self.upload(expected_sha256=hashlib.sha256(CONTENT).hexdigest(), models_pattern='^hAP')
        image = FirmwareImage.objects.get()
        self.assertRedirects(response, f'/system/firmware/image/?uuid={image.uuid}', fetch_redirect_response=False)
        self.assertEqual(image.sha256, hashlib.sha256(CONTENT).hexdigest())
        self.assertEqual(image.size, len(CONTENT))
        self.assertEqual(image.filename, 'routeros-7.18.2-arm64.npk')
        self.assertEqual(image.uploaded_by_name, make_user.__name__ and image.uploaded_by.username)

    def test_a_file_that_is_not_the_vendors_is_refused(self):
        self.upload(expected_sha256='0' * 64)
        self.assertFalse(FirmwareImage.objects.exists())

    def test_bad_values_are_refused(self):
        for fields in ({'version': ''}, {'version': '7.18; reboot'}, {'models_pattern': '(['},
                       {'device_type': str(device_types.get('monitoring').uuid)}, {'expected_sha256': 'xyz'},
                       {'file': SimpleUploadedFile('empty.npk', b'')}):
            with self.subTest(fields=fields):
                self.upload(**fields)
                self.assertFalse(FirmwareImage.objects.exists())
        with self.settings(FIRMWARE_MAX_BYTES=10):
            self.upload()
        self.assertFalse(FirmwareImage.objects.exists())

    def test_file_names_are_safe_on_a_device(self):
        from firmware.views import safe_filename
        self.assertEqual(safe_filename('../../etc/x"; reboot;.npk'), 'x___reboot_.npk')
        self.assertEqual(safe_filename('.hidden'), 'hidden')
        self.assertEqual(safe_filename('C:\\fw\\image v2.bin'), 'image_v2.bin')

    def test_matching_and_newest(self):
        make_image(version='7.17')
        newest = make_image(version='7.18', models_pattern='^hAP')
        make_image(version='7.19', architecture='mipsbe')
        found = {'model_name': 'hAP ax3', 'architecture': 'arm64'}
        self.assertEqual(FirmwareImage.newest_for(device_types.get('routeros'), found), newest)

    def test_repository_check_feeds_the_update_status(self):
        from routerlib.router_functions import available_version
        make_image(version='7.19')
        routeros = device_types.get('routeros')
        routeros.check_device, routeros.check_repository = False, True
        self.assertEqual(available_version(routeros, {'os_version': '7.18 (stable)'}, {}, ''), ('7.19', True))

    def test_only_configuration_managers(self):
        self.client.force_login(make_user(30))
        self.assertContains(self.client.get('/system/firmware/'), 'Access Denied')
        self.upload()
        self.assertFalse(FirmwareImage.objects.exists())


class RolloutTests(TestCase):
    def setUp(self):
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)
        self.image = make_image(version='7.18')
        profile = make_profile()
        self.routers = [make_router(router_type='routeros', name=f'r{i}', backup_profile=profile) for i in range(4)]
        for router in self.routers:
            RouterInformation.objects.create(router=router, os_version='7.17')
        self.rollout = FirmwareRollout.objects.create(image=self.image, batch_size=2)
        for position, router in enumerate(self.routers):
            RolloutTarget.objects.create(rollout=self.rollout, router=router, position=position)
        self.device_version = {}

    def backups_succeed(self):
        RouterBackup.objects.filter(success=False, error=False).update(success=True)

    def fake_device(self, size=None):
        size = len(CONTENT) if size is None else size
        return FakeSSHClient({f':put [/file get [find name="{self.image.filename}"] size]': f'{size}\n',
                              '/system reboot': OSError('connection closed')})

    def run_until(self, state, client, rounds=12):
        scp = mock.MagicMock()

        def inventory(info):
            info.os_version = self.device_version.get(info.router_id, '7.17')
            info.save()
            return True, ''
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch('scp.SCPClient', return_value=scp), \
                mock.patch('routerlib.router_functions.get_router_information', side_effect=inventory), \
                mock.patch.object(engine, 'REBOOT_GRACE', timezone.timedelta(0)):
            for _ in range(rounds):
                engine.advance_all()
                self.backups_succeed()
                for target in RolloutTarget.objects.filter(state='rebooting'):
                    self.device_version[target.router_id] = '7.18'
                self.rollout.refresh_from_db()
                if self.rollout.state == state:
                    break
        return scp

    def states(self):
        return list(self.rollout.targets.order_by('position').values_list('state', flat=True))

    def test_the_canary_goes_alone_then_batches(self):
        client = self.fake_device()
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch('scp.SCPClient', return_value=mock.MagicMock()):
            engine.advance_all()
        self.assertEqual(self.states(), ['backing_up', 'waiting', 'waiting', 'waiting'])
        self.assertTrue(RouterBackup.objects.filter(router=self.routers[0], schedule_type='instant').exists())
        scp = self.run_until('done', client, rounds=30)
        self.assertEqual(self.rollout.state, 'done', self.rollout.message)
        self.assertEqual(self.states(), ['done'] * 4)
        scp.put.assert_called()
        self.assertEqual(scp.put.call_args.args[1], f'/{self.image.filename}')
        self.assertIn('/system reboot', client.commands)

    def test_a_damaged_upload_stops_before_installing(self):
        client = self.fake_device(size=1)
        self.run_until('failed', client)
        self.assertEqual(self.rollout.state, 'failed')
        self.assertEqual(self.states(), ['failed', 'skipped', 'skipped', 'skipped'])
        self.assertIn('not intact', self.rollout.targets.get(position=0).message)
        self.assertNotIn('/system reboot', client.commands)

    def test_a_device_that_does_not_come_back_fails_at_the_deadline(self):
        client = self.fake_device()
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch('scp.SCPClient', return_value=mock.MagicMock()), \
                mock.patch('routerlib.router_functions.get_router_information', return_value=(False, 'timed out')), \
                mock.patch.object(engine, 'REBOOT_GRACE', timezone.timedelta(0)):
            for _ in range(8):
                engine.advance_all()
                self.backups_succeed()
            target = self.rollout.targets.get(position=0)
            self.assertEqual(target.state, 'rebooting')
            RolloutTarget.objects.filter(pk=target.pk).update(deadline=timezone.now() - timezone.timedelta(minutes=1))
            engine.advance_all()
        self.rollout.refresh_from_db()
        self.assertEqual(self.rollout.state, 'failed')
        self.assertIn('did not come back with 7.18', self.rollout.targets.get(position=0).message)

    def test_a_device_without_install_procedure_is_refused(self):
        from fleet_commander.models import CommandVariant
        CommandVariant.objects.filter(command__system_key='firmware_install', router_type='routeros').update(payload='')
        engine.advance_all()
        self.rollout.refresh_from_db()
        self.assertEqual(self.rollout.state, 'failed')
        self.assertIn('no firmware install procedure', self.rollout.message)

    def test_abort_skips_the_rest(self):
        engine.abort(self.rollout, make_user(40))
        self.assertEqual(self.rollout.state, 'aborted')
        self.assertEqual(self.states(), ['skipped'] * 4)

    def test_a_locked_target_is_not_worked_on_twice(self):
        RolloutTarget.objects.filter(position=0).update(lock=timezone.now())
        engine.advance_all()
        self.assertEqual(self.states()[0], 'waiting')

    def test_starting_a_rollout_from_the_page(self):
        self.client.force_login(make_user(40))
        FirmwareRollout.objects.all().delete()
        response = self.client.post(f'/system/firmware/rollout/start/?uuid={self.image.uuid}', {
            'routers': [str(r.uuid) for r in self.routers[:3]], 'canary': str(self.routers[2].uuid),
            'batch_size': '2', 'confirmation': 'update'})
        rollout = FirmwareRollout.objects.get()
        self.assertRedirects(response, f'/system/firmware/rollout/?uuid={rollout.uuid}', fetch_redirect_response=False)
        self.assertEqual(rollout.targets.get(position=0).router, self.routers[2])
        self.assertEqual(rollout.targets.count(), 3)
        # Without the confirmation nothing starts
        self.client.post(f'/system/firmware/rollout/start/?uuid={self.image.uuid}',
                         {'routers': [str(self.routers[3].uuid)], 'batch_size': '2'})
        self.assertEqual(FirmwareRollout.objects.count(), 1)


class PageAndBranchTests(TestCase):
    def setUp(self):
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)
        self.client.force_login(make_user(40))
        self.image = make_image(version='7.18')
        self.router = make_router(router_type='routeros', name='fw1', backup_profile=make_profile())
        RouterInformation.objects.create(router=self.router, os_version='7.17', model_name='hAP ax3')

    def rollout(self, state='waiting', **target):
        rollout = FirmwareRollout.objects.create(image=self.image)
        RolloutTarget.objects.create(rollout=rollout, router=self.router, position=0, state=state, **target)
        return rollout

    def test_pages_render(self):
        rollout = self.rollout()
        for path in ('/system/firmware/', f'/system/firmware/image/?uuid={self.image.uuid}',
                     f'/system/firmware/rollout/start/?uuid={self.image.uuid}',
                     f'/system/firmware/rollout/?uuid={rollout.uuid}'):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, '7.18')
        self.assertContains(self.client.get(f'/system/firmware/rollout/start/?uuid={self.image.uuid}'), 'fw1')

    def test_switch_off_and_delete(self):
        url = f'/system/firmware/image/?uuid={self.image.uuid}'
        self.client.post(url, {'action': 'toggle'})
        self.image.refresh_from_db()
        self.assertFalse(self.image.enabled)
        path = self.image.file.path
        self.client.post(url, {'action': 'delete', 'confirmation': 'delete'})
        self.assertFalse(FirmwareImage.objects.exists())
        import os
        self.assertFalse(os.path.exists(path), 'the file went with it')

    def test_an_image_in_a_rollout_is_not_deleted(self):
        self.rollout()
        self.client.post(f'/system/firmware/image/?uuid={self.image.uuid}', {'action': 'delete', 'confirmation': 'delete'})
        self.assertTrue(FirmwareImage.objects.exists())

    def test_abort_from_the_page_and_the_cron_endpoint(self):
        rollout = self.rollout()
        self.client.post(f'/system/firmware/rollout/?uuid={rollout.uuid}', {'action': 'abort', 'confirmation': 'abort'})
        rollout.refresh_from_db()
        self.assertEqual(rollout.state, 'aborted')
        self.assertEqual(self.client.get('/cron/firmware_rollouts/').json(), {'running': 0})

    def test_a_failed_backup_stops(self):
        backup = RouterBackup.objects.create(router=self.router, schedule_type='instant', error=True,
                                             error_message='refused')
        rollout = self.rollout('backing_up', backup=backup)
        engine.advance_all()
        rollout.refresh_from_db()
        self.assertEqual(rollout.state, 'failed')
        self.assertIn('pre-update backup failed: refused', rollout.message)

    def test_a_backup_that_never_finishes_stops(self):
        backup = RouterBackup.objects.create(router=self.router, schedule_type='instant')
        rollout = self.rollout('backing_up', backup=backup, deadline=timezone.now() - timezone.timedelta(minutes=1))
        engine.advance_all()
        rollout.refresh_from_db()
        self.assertIn('did not finish within 30 minutes', rollout.message)

    def test_an_upload_past_its_deadline_stops(self):
        rollout = self.rollout('uploading', deadline=timezone.now() - timezone.timedelta(minutes=1))
        engine.advance_all()
        rollout.refresh_from_db()
        self.assertIn('upload did not complete', rollout.message)

    def test_a_wrong_image_type_is_refused(self):
        other = make_router(router_type='openwrt', name='ow1', backup_profile=make_profile())
        rollout = FirmwareRollout.objects.create(image=self.image)
        RolloutTarget.objects.create(rollout=rollout, router=other, position=0)
        engine.advance_all()
        rollout.refresh_from_db()
        self.assertIn('the image is for Mikrotik (RouterOS)', rollout.message)

    def test_a_model_the_image_is_not_for(self):
        self.image.models_pattern = '^CCR'
        self.image.save()
        rollout = self.rollout()
        engine.advance_all()
        rollout.refresh_from_db()
        self.assertIn('not for this model', rollout.message)

    def test_sha256_verification(self):
        image = make_image(slug='openwrt', version='24.10.1')
        router = make_router(router_type='openwrt', name='ow2', backup_profile=make_profile())
        rollout = FirmwareRollout.objects.create(image=image)
        target = RolloutTarget.objects.create(rollout=rollout, router=router, position=0, state='verifying')
        good = FakeSSHClient({f'sha256sum /tmp/{image.filename}': f'{image.sha256}  /tmp/{image.filename}\n'})
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=good):
            engine.advance_all()
        target.refresh_from_db()
        self.assertEqual(target.state, 'installing')
        RolloutTarget.objects.filter(pk=target.pk).update(state='verifying')
        bad = FakeSSHClient({f'sha256sum /tmp/{image.filename}': 'ab' * 32 + '\n'})
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=bad):
            engine.advance_all()
        rollout.refresh_from_db()
        self.assertIn('SHA-256 does not match', rollout.message)


# --- Updating a device: the options, online updates -----------------------------

CHANNEL_VERSIONS = {'NEWESTa7.long-term': '7.24.7', 'NEWESTa7.stable': '7.25.2', 'NEWESTa7.testing': '7.25.2',
                    'NEWESTa7.development': '7.26beta7', 'NEWEST6.long-term': '6.49.23', 'NEWEST6.stable': '6.49.23'}


def fake_channel(device_type, url):
    return CHANNEL_VERSIONS.get(url.rsplit('/', 1)[-1], '')


def routeros_device(name, version, channel='stable', model='RB5009', architecture='arm64'):
    import json
    router = make_router(name=name, router_type='routeros', backup_profile=make_profile())
    RouterInformation.objects.update_or_create(router=router, defaults={
        'os_version': version, 'model_name': model, 'architecture': architecture, 'update_available': True,
        'json_data': json.dumps({'/system package update print': f' channel: {channel}\r\n latest-version: x\r\n'})})
    router.refresh_from_db()
    return router


class UpdateOptionsTests(TestCase):
    """The options, in order: matching files of the repository newest first,
    then the online update of each channel - newer versions of the same major
    version only."""

    def setUp(self):
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)

    def test_the_migrations_configure_routeros(self):
        from fleet_commander import system_commands
        routeros = device_types.get('routeros')
        self.assertIn('7 stable: https://upgrade.mikrotik.com/routeros/NEWESTa7.stable', routeros.update_channels)
        self.assertNotIn('NEWEST6.testing', routeros.update_channels, 'RouterOS 6 testing answers 7.x')
        payload = system_commands.payload_of('firmware_online_update', 'routeros')
        self.assertIn('channel={{ channel }}', payload)
        self.assertIn('/system package update install', payload)
        self.assertEqual(system_commands.payload_of('firmware_online_update', 'openwrt').strip(), '')

    def test_repository_files_first_then_every_channel(self):
        from firmware.options import options_for
        router = routeros_device('rb-1', '7.24.5 (stable)')
        make_image(version='7.25.1', architecture='arm64')
        make_image(version='7.25.2', architecture='arm64')
        make_image(version='7.25.9', architecture='mipsbe')     # another architecture
        make_image(version='7.24.1', architecture='arm64')      # older
        options = options_for(router, fetch=fake_channel)
        self.assertEqual([(o['kind'], o['version'], o['channel']) for o in options], [
            ('image', '7.25.2', ''), ('image', '7.25.1', ''),
            ('online', '7.24.7', 'long-term'), ('online', '7.25.2', 'stable'),
            ('online', '7.25.2', 'testing'), ('online', '7.26beta7', 'development')])
        self.assertTrue(options[0].get('recommended'))
        self.assertTrue(next(o for o in options if o['channel'] == 'stable')['current'])

    def test_without_a_file_the_channel_it_is_on_is_suggested(self):
        from firmware.options import options_for
        options = options_for(routeros_device('rb-2', '7.24.5 (stable)', channel='long-term'), fetch=fake_channel)
        self.assertEqual(next(o for o in options if o.get('recommended'))['channel'], 'long-term')

    def test_routeros_6_stays_on_6(self):
        from firmware.options import options_for
        options = options_for(routeros_device('rb-6', '6.49.21 (long-term)', channel='long-term'), fetch=fake_channel)
        self.assertEqual({o['version'] for o in options}, {'6.49.23'})
        self.assertEqual({o['channel'] for o in options}, {'long-term', 'stable'})

    def test_nothing_newer_nothing_offered(self):
        from firmware.options import options_for
        self.assertEqual(options_for(routeros_device('rb-3', '7.26beta7'), fetch=fake_channel), [])
        router = routeros_device('rb-4', '')
        self.assertEqual(options_for(router, fetch=fake_channel), [], 'no version read yet')

    def test_a_type_without_the_procedure_offers_no_online_update(self):
        from firmware.options import options_for
        from fleet_commander.models import CommandVariant
        CommandVariant.objects.filter(command__system_key='firmware_online_update', router_type='routeros').update(payload='')
        self.assertEqual(options_for(routeros_device('rb-5', '7.24.5'), fetch=fake_channel), [])

    def test_channel_answers_are_read_safely(self):
        from firmware import options
        routeros = device_types.get('routeros')
        options._cache.clear()
        answer = mock.MagicMock(status_code=200, text='7.25.2 1795173070\n')
        with mock.patch('firmware.options.requests.get', return_value=answer) as get, \
                mock.patch('routerlib.router_functions._safe_url', side_effect=lambda url: url):
            self.assertEqual(options.fetch_channel_version(routeros, 'https://vendor.example/a'), '7.25.2')
            self.assertEqual(options.fetch_channel_version(routeros, 'https://vendor.example/a'), '7.25.2')
            self.assertEqual(get.call_count, 1, 'kept for an hour')
        with mock.patch('routerlib.router_functions._safe_url', side_effect=ValueError('only https')):
            self.assertEqual(options.fetch_channel_version(routeros, 'http://vendor.example/b'), '')

    def test_channel_lines_are_validated(self):
        from firmware.options import channel_problems
        self.assertEqual(channel_problems('7 stable: https://a.example/x\n\n6 long-term: https://a.example/y'), [])
        self.assertEqual(len(channel_problems('stable: https://a.example/x\n7 stable: http://a.example/x')), 2)

    def test_exact_versions(self):
        from firmware.options import same_version
        self.assertTrue(same_version('7.26beta7 (development)', '7.26beta7'))
        self.assertFalse(same_version('7.26beta6', '7.26beta7'), 'a beta is not another beta')
        self.assertFalse(same_version('7.26', '7.26beta7'), 'nor its final release')


class OnlineUpdateTests(TestCase):
    """The update page and an online rollout: backup, the device's own update
    of the chosen channel, back on exactly the version offered."""

    def setUp(self):
        self.media = temporary_media_root()
        self.media.__enter__()
        self.addCleanup(self.media.__exit__, None, None, None)
        self.client.force_login(make_user(40))
        self.router = routeros_device('rb-online', '7.24.5 (stable)')
        patcher = mock.patch('firmware.options.fetch_channel_version', side_effect=fake_channel)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_page_offers_the_options_and_needs_level_40(self):
        page = self.client.get(f'/system/firmware/update/?uuid={self.router.uuid}')
        self.assertContains(page, 'online update, stable channel')
        self.assertContains(page, '7.26beta7')
        self.assertContains(page, 'suggested')
        self.client.force_login(make_user(30))
        self.assertContains(self.client.get(f'/system/firmware/update/?uuid={self.router.uuid}'), 'Access Denied')

    def test_starting_needs_the_confirmation(self):
        url = f'/system/firmware/update/?uuid={self.router.uuid}'
        self.client.post(url, {'action': 'start', 'option': 'online:stable', 'routers[]': [self.router.uuid]})
        self.assertFalse(FirmwareRollout.objects.exists())
        response = self.client.post(url, {'action': 'start', 'option': 'online:stable', 'confirmation': 'update',
                                          'routers[]': [self.router.uuid]})
        rollout = FirmwareRollout.objects.get()
        self.assertRedirects(response, f'/system/firmware/rollout/?uuid={rollout.uuid}', fetch_redirect_response=False)
        self.assertEqual((rollout.source, rollout.channel, rollout.image), ('online', 'stable', None))
        self.assertEqual(rollout.targets.get().to_version, '7.25.2')
        self.assertIn('Online update, stable channel (7.25.2)', rollout.label)
        self.assertContains(self.client.get(f'/system/firmware/rollout/?uuid={rollout.uuid}'), 'Online update, stable channel')

    def test_a_selection_from_the_list(self):
        other = routeros_device('rb-online-2', '7.24.1 (stable)')
        page = self.client.post('/system/firmware/update/', {'routers[]': [self.router.uuid, other.uuid]})
        self.assertContains(page, 'Update 2 devices')
        self.assertContains(page, 'For 2 of the 2 devices')

    def run_rollout(self, comes_back_with):
        rollout = FirmwareRollout.objects.create(source='online', channel='stable', batch_size=1)
        target = RolloutTarget.objects.create(rollout=rollout, router=self.router, to_version='7.25.2')
        client = FakeSSHClient({})

        def inventory(info):
            RouterInformation.objects.filter(pk=info.pk).update(os_version=comes_back_with)
            return True, ''
        with mock.patch('routerlib.device_session.connect_to_ssh', return_value=client), \
                mock.patch('routerlib.router_functions.get_router_information', side_effect=inventory), \
                mock.patch.object(engine, 'REBOOT_GRACE', timezone.timedelta(0)):
            engine.advance(rollout)                      # waiting -> backing_up
            target.refresh_from_db()
            RouterBackup.objects.filter(pk=target.backup_id).update(success=True)
            engine.advance(rollout)                      # backing_up -> installing (no upload)
            target.refresh_from_db()
            self.assertEqual(target.state, 'installing')
            engine.advance(rollout)                      # the device's own update
            engine.advance(rollout)                      # back on the version?
        target.refresh_from_db()
        return target, client

    def test_an_online_rollout_runs_the_procedure_with_the_channel(self):
        target, client = self.run_rollout('7.25.2 (stable)')
        self.assertEqual(target.state, 'done', target.message)
        self.assertIn('/system package update set channel=stable', client.commands)
        self.assertIn('/system package update install', client.commands)
        self.assertNotIn('/system reboot', client.commands, 'not the file install')

    def test_another_version_is_not_done(self):
        target, _ = self.run_rollout('7.25.1 (stable)')
        self.assertNotEqual(target.state, 'done')
        self.assertIn('Waiting for 7.25.2', target.message)
