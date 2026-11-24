"""The upgrade rule, enforced: databases of the earlier projects named in
docs/upgrading.md, and of every release of this one, keep upgrading in place. That holds only while every released migration stays exactly as it
was - added to, never edited, renamed, squashed or deleted.

RELEASED pins every migration released up to 0.15.1, the predecessors' among
them. A new migration is added to it once it has been released; an entry is
never changed or removed.
"""
import hashlib
import pathlib
import re

from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase

from routerfleet import test_upgrade

ROOT = pathlib.Path(__file__).resolve().parent.parent

RELEASED = {
    'audit_log/migrations/0001_initial.py': '798b0442ee6f268a979a354775c4b1271048884bf24dfacd58af0ea130c1ca4e',
    'audit_log/migrations/0002_changerecord_change_object_and_more.py': '08355a68e881e297561d6a3e044379918b9df9357c1a26f2e5ff383242e13430',
    'backup/migrations/0001_initial.py': 'd903a0c9aca0cf1cf0ba5213e95a7f9f44226d1a9f9cf726f03644fd5e3e1588',
    'backup/migrations/0002_alter_backupprofile_weekly_day.py': 'ef7379a66fd82fca695a101ae50ad73862f8f9578d1e5928d75e9a1e44249690',
    'backup/migrations/0003_alter_backupprofile_retry_interval.py': '9b1c495382ca9eef11ce155dd0e282ec37147f8d13eeb9d10cf7aedd60ff79f9',
    'backup/migrations/0004_backupprofile_instant_retenion_and_more.py': '5e63fce85e83e1f94065516f274d30a346437c3fcf7ad1bdb7b06f4961de8802',
    'backup/migrations/0005_rename_daily_retenion_backupprofile_daily_retention_and_more.py': 'c4ecbfb52fd4218f45469bd4afd446e6a068f7642091ce058050e0adcc42e8a3',
    'backup/migrations/0006_alter_backupprofile_retrieve_interval.py': '31b24c0f5a03a1024bc282b0ee4ddd66cdddfe987ddab109863b25ae90e062ce',
    'backup/migrations/0007_backupprofile_profile_error_information.py': '90fce7d5d427814bdbd9de47ad97e4770a52aefecb88b48cb223f0a894dccaf3',
    'backup/migrations/0008_alter_backupprofile_backup_interval.py': 'c5001a5dfa39c85e1bbeee948ca4a1cb9be2b86e2a9e104101c59253057a7517',
    'backup/migrations/0009_backupprofile_parameter_sensitive_and_more.py': '6f1923babf29b0ca6ea0f351ac97d7cdaafcdf0e97f5d1095215d5411b53fde7',
    'backup/migrations/0010_backupprofile_keep_unchanged.py': '0c4803709e208cde2c1a8d3717536347878dea7cf46c3ec22a395498f73597ec',
    'backup_data/migrations/0001_initial.py': '3c172b6d61b3b929593cf8986055f0f88fad1adedfd2d33d80162cb9a93f3313',
    'backup_data/migrations/0002_routerbackup_backup_binary_and_more.py': '3b086aba62e55ba25bc9c4bedb64fe849d10f0bc870355d6dce20315e984d99e',
    'backup_data/migrations/0003_routerbackup_config_change_detected.py': '381dcf654a141d6e609cd04c64308b8ec75ea89350b0432e1708c7f7397898c5',
    'backup_data/migrations/0004_routerbackup_backup_text_hash.py': '61b55cab6b444a0b9ece9915e6d4052847e67e97954a2a716121b0e333648a0c',
    'backup_data/migrations/0005_alter_routerbackup_backup_text_hash.py': 'a65363b0bcb2d9e16f187b0e7f547228b8e446163d92c61a94847a8963bd246e',
    'backup_data/migrations/0006_routerbackup_backup_text_filename.py': '95035c7a1f21133a5d53d73ef72b427f4b066dfa2b279a21955f3eeef0b41f69',
    'backup_data/migrations/0007_routerbackup_task_console_output.py': 'bb0eb14ba994048ed19d90da5cb33ed6dbda3f90e253f4c5bd52c6d814496b9b',
    'backup_data/migrations/0008_alter_routerbackup_task_console_output.py': 'ae967719ef8d449a89df2b85365f79f883e292b01e5442e9bf539f0772539e0f',
    'backup_data/migrations/0009_routerbackup_task_lock.py': 'dbd232e97ea793655cd7fc413289dc11dc0bce03eb032b70dd1e0677ba45718d',
    'backup_data/migrations/0010_remove_routerbackup_task_lock.py': 'f2a1d4bb33da6afbfdf9de8cb3f69aa5d7d52f191b1f9d0891195d872b99b18a',
    'backup_data/migrations/0011_routerbackup_task_lock.py': '1ef35c1d8bc734c1616b0f927d905797b33d009de169e455baa0f10d050fbbad',
    'backup_data/migrations/0012_backfill_config_change_detected.py': '61a1a51a71253fbfad1f41bca3648e948091c4d8ac27682fa8dd428c3a5d02c9',
    'backup_data/migrations/0013_routerbackup_same_as.py': 'c2f9d4cf95888bc791fb20e676f5753f9afb54941cf645eb1a7f6139669ae864',
    'backup_data/migrations/0014_named_texts.py': 'a60f0201c35f3d1fac2346ce23bc71996263ecda10cab9eadb8c99f6a8a3f1a6',
    'backup_data/migrations/0015_backup_list_indexes.py': 'd3db8b069a5de66750049eec29ac95f5fdf50506cadf253220022e63bffce2e5',
    'backup_data/migrations/0016_refused_exports_are_failures.py': '640084bd85bae3949572a2c7783eaf95c20bdd3d679336735cacd10f406661dc',
    'backup_data/migrations/0017_refused_by_exit_code.py': 'c76c59d121141bc56d2bc2dfc04420c8142929c138ef620bb71713431cce7cb9',
    'console/migrations/0001_initial.py': 'a9481ca5641a070039f7d790f0706d98a236af975da445de1d37083b3cf20e03',
    'console/migrations/0002_consolesession_console_user_open.py': '889d09b1ac07329c5f69d17734c5f5e412f1feee115fd19796dfa4f32b54aad4',
    'firmware/migrations/0001_initial.py': '566023450501610e7b040457278b20bc9a9c8725a30a066e3b0ba4014ba1e681',
    'firmware/migrations/0002_rollout_online_source.py': 'dc9f14d57afb440afefc47b0d4bf91ccb14282c998d7eb92edfd11b87f77f4bf',
    'firmware/migrations/0003_timeline_bootloader.py': '8cdebff6a4b8ab55edaf4ef4568b9794b8d818d46a8a8bb64f3ce4dd077e07ea',
    'fleet_commander/migrations/0001_initial.py': '55eed96cda04dc6e0fde8bc3cf1da80af83c7d924c362796e84c1d196e833055',
    'fleet_commander/migrations/0002_commandjob_completed.py': '8e1f6a34bf2232c6038dd4ae196e1df90c18eee692ce670394aee0cb33584c96',
    'fleet_commander/migrations/0003_commandjob_error_count_commandjob_success_count_and_more.py': '0de2b0828ada388c0202ad2e78fa9f8c76f2723b1e8eee1f1867dd20fce4f75c',
    'fleet_commander/migrations/0004_commandjob_aborted_count.py': '12a9fe000b1934d7854131cf71823d8c623f0c37d6f73110ceac3aa4c982caef',
    'fleet_commander/migrations/0005_command_verify_interval_command_verify_timeout_and_more.py': '33c5cb6373931d4667e97f73455f452d403a279331508a19f6fc943ec16753a7',
    'fleet_commander/migrations/0006_verify_update_command_result.py': 'a910fca5a74d4894ff4ae16f3b7f2663a28d24df3f41cd84fca249c5a19e6ad0',
    'fleet_commander/migrations/0007_verify_update_against_router_version.py': '464a903ab780bdffe1c890ebd5349d2254a805c302ff237b39da00496e16d8cc',
    'fleet_commander/migrations/0008_commandschedule_exclude_router_and_more.py': '627e8724bf01b4a35f618dde8ae9db164ec5df2d4015282947543b3e9ad00bef',
    'fleet_commander/migrations/0009_scheduledefaults.py': '82a0b287bb0b32901fb161b8044d8dcb692b9da6d2da63c68cc22669115bf76f',
    'fleet_commander/migrations/0010_commandjob_schedule.py': '929d1abf76a9c929fc1c44e8f5bfda6f95e00351eb1323896139d6afab616154',
    'fleet_commander/migrations/0011_commandschedule_last_missed.py': 'f3d94bb2c3e98b6f8f9dbf43c2e1a401432b2893419a5dbf3763d2619e7c3e49',
    'fleet_commander/migrations/0012_scheduledefaults_name_fits.py': 'a354c3c84a660576fdc6bcab2ff704982c838ae816342adeb56911b2669bac36',
    'fleet_commander/migrations/0013_command_system_key.py': '30fab95ae330bfb93b8c24faaec56af4fb437364950ee8988ae89e5a122edce4',
    'fleet_commander/migrations/0014_system_commands.py': 'f8584ff26a95e979613caa4a319c441d4cb379e63e08ee542c71368698f54185',
    'fleet_commander/migrations/0015_variant_device_type.py': '7317a7f0b5b1d4cfc5cda1e4ce0024def6e2b638ad100369d446bb1f7b119d56',
    'fleet_commander/migrations/0016_device_type_commands.py': 'f849800e7300cd3e95e65f788a7d029c91a569ee8c2bd54a38fdce971b71f2a9',
    'fleet_commander/migrations/0017_firmware_commands.py': 'f305b013b200142bf4b6740d433f450e3f5614b75ae53adf461ddc7da6b534d5',
    'fleet_commander/migrations/0018_routeros_text_only.py': 'd19b95e59b8c9a3571855116590712c473f0f610d0d1b1b2c9c56b7b3e098ec4',
    'fleet_commander/migrations/0019_firmware_online_update.py': 'dfee393c769741b6bfb049a988330f35cb3e6b2201a01d24260531a02424126b',
    'fleet_commander/migrations/0020_firmware_bootloader_upgrade.py': '852e0890d00a242100bed97c9011dd415b9244dacd7d4d414841eeb03b7ce520',
    'fleet_commander/migrations/0021_huawei_firmware.py': '4a4b18bc744c0bc5e48353d881f1ff6845877c940dd70c4d0a54130d3a3d029c',
    'import_tool/migrations/0001_initial.py': '132a6a583f3366f0e093a1bccdb08d7e5ef6314867c4b0b9dcdea6dbece200db',
    'import_tool/migrations/0002_rename_created_at_csvdata_created_and_more.py': '6a95e115daaa75a092ca33465c961ec8965bad4a7cc76ac694fa0b0cb16d0810',
    'import_tool/migrations/0003_importtask_router_group_name_importtask_ssh_key_name.py': '48dad1e0ba7c9f993bcc8ca28fa3f74a45c2200dffa501fda1aea93d6c5e7a15',
    'import_tool/migrations/0004_importtask_backup_profile_name.py': '84d8a22293ebd7472c6913721e24a404ce3e38a90e8c4a38e937968625f06c7f',
    'import_tool/migrations/0005_importtask_import_id_and_more.py': '36bfa4827d40c6112ba21a79c01dcbc1c44c02c4ace64bc83d6e89d9849c8cc9',
    'import_tool/migrations/0006_alter_importtask_router_type.py': 'b186c43d475279a0a633068ff4b01375024ba4cf25e56b017c01e3c57a130e5c',
    'import_tool/migrations/0007_alter_importtask_router_type.py': 'dfd9409521844fa515f0536f39e770b8ca2a1116c9b5c6839b3ce03ae831f7a8',
    'import_tool/migrations/0008_encrypt_import_passwords.py': '565c0129c085356a46f0a4d82dcec1b47a8fe52760f086814df8252e78e6eed7',
    'import_tool/migrations/0009_import_device_type.py': '076f2e2fa46054ab14ccc6be24c0ad5518a0148880d9e37de49f6ba11a602555',
    'integration_manager/migrations/0001_initial.py': 'aba0fd82febcf00a7cacbec65b34dc3d890c5ba1c736ddd86a741233e81f7c25',
    'integration_manager/migrations/0002_alter_externalintegration_wireguard_webadmin_default_user_level.py': '0f8663e28f8bf2acb36e2448b2a4ff1de675b5f5f08fd8d69e8a26ff0abd403d',
    'integration_manager/migrations/0003_secret_encrypted.py': '9a238e31f46b9e9e4b6199038cc5e5ace34cd6a251e244b075aa5a0797fcb3e5',
    'integration_manager/migrations/0004_remove_externalintegration.py': '7f18c46f1d8311482646f3932582e04d6dfa71bf78e4f102d4c498d72ff1e540',
    'message_center/migrations/0001_initial.py': '19841d2f0f3ae5ff644cec1bd0206991ec11d939fa37028d50fd7d288a467646',
    'message_center/migrations/0002_alter_messagechannel_channel_type.py': 'ea7dee1a3cc1d467e2f5c8658bf93b723555e2ded1d43118b04d9c0e86217359',
    'message_center/migrations/0003_message_error_status_code.py': '264f673d1b7feedf6439d726cba52f2ab715a002cd118e015e0f92c64dfa70ad',
    'message_center/migrations/0004_message_next_retry.py': 'a4be4090dea63cdebd5e54566a0fbefa67a886b1f7e1ff650b6b7979fc880810',
    'message_center/migrations/0005_alter_messagechannel_channel_type.py': '9bd060963433344ea4746dc49bd3b310443fe3211321329422cfc651e32d6f82',
    'message_center/migrations/0006_alter_messagechannel_channel_type.py': 'd5e164c4e75206669789c2aa9f6b388bea61f64329272e8cf503ddda55084eee',
    'message_center/migrations/0007_alter_notification_notification_type.py': '2d20162de3f585612e6aa6c57cebade930cff33b96bbea8e73d755b1de0cf629',
    'message_center/migrations/0008_secret_encrypted.py': '11962a372e85158e2062d34c7042bfa0b04ca44faf28aed0030f24b0303ab941',
    'message_center/migrations/0009_email_notifications.py': 'b99218da1f33a88e19ee3c7308ffff287e9129549141b855bfcfc8b943dfd4e3',
    'message_center/migrations/0010_ntfy_server_and_priority.py': '9721255ba95522798b877704f155b031c1661cc066c1fe916f6c8c79d59c56ee',
    'message_center/migrations/0011_callmebot_removed.py': '47f2a7dbf5288371c5412b857b7712a1d69077b172011d4d54312901f3745e46',
    'message_center/migrations/0012_message_message_queue_message_message_history_and_more.py': 'bdf6157e2774775f9644fe0e7f7d235db075e2172cbfb9f2ee10e3e50d88a7e4',
    'message_center/migrations/0013_firmware_update_event.py': '49f37c7a28ff7b15a1912de43c7968fdd69f7ddafdfc187dae5cae35da03933d',
    'message_center/migrations/0014_smtpsettings_verified_at.py': 'fb97a6954a34ba19bf244ab5abf14f8ffd03110d625bfe3d677e8db26292a647',
    'monitoring/migrations/0001_initial.py': 'fc2f64d7cc8601fd2296462360ce10b90195c5e42479aeaccc4db94fe07f770b',
    'monitoring/migrations/0002_routerdowntime_downtime_router_start_and_more.py': '6b96bfe065586a19404763e9372181db723a61e69df9f2e49353d16cadd2cef8',
    'monitoring/migrations/0003_close_outages_of_unwatched_hosts.py': '642e28b94b2a61e32206faf6a54d5a92672bd14d96a86a812a43b7a32c5f249a',
    'router_manager/migrations/0001_initial.py': 'e7ca29cc6b4bf0e80ac98fc32bef67449dcd7c4a2e1d215816c881f6a00bb3f2',
    'router_manager/migrations/0002_alter_router_password.py': 'a9c37935291d0e391e7c6c31db30cdbf3c864759872f4d1d9e2f945e99add63d',
    'router_manager/migrations/0003_routerstatus.py': '5b34e3dfd70983c25cdcacbfadf05d1958acebcf08d9471750c42644ef0b80e9',
    'router_manager/migrations/0004_router_internal_notes_alter_router_name_routergroup.py': '9156f5f0cea62bcc8aa196e179643df0fb6c194a6b0004f8df35dc88e18d02be',
    'router_manager/migrations/0005_alter_sshkey_name.py': '82ee7bc542499d339b5f83c7442f8598d05010b474c22ee1d58610b0303b6b50',
    'router_manager/migrations/0006_routergroup_default_group.py': 'e942fdfd276610bd5461caf9dfbd5b9a23b87f9c1bd7981fdd383d76d856dbde',
    'router_manager/migrations/0007_alter_routergroup_routers.py': 'd6c7155710a054fd5554fa0b6c548e08963e0763e20183a945cfadbdd69783d2',
    'router_manager/migrations/0008_routerstatus_last_backup_failed_backupschedule.py': '67d63e19dbb4f27a9ae1c544295610ae9ec42465bf3f603998be808344c9c991',
    'router_manager/migrations/0009_router_backup_profile.py': '3b9bf0ca5ec5838253d7f7b51512a2a96ba0566b0bbfd832b3f223a12c49a038',
    'router_manager/migrations/0010_alter_router_router_type.py': '914566de7c202a071d65a3d49fa84a2f27f0540a3d30afe8433db7521bd4e7c8',
    'router_manager/migrations/0011_routerstatus_backup_lock.py': '67e5590e86153487d8c0ecd1a17f2307067d7a54b7bd97ee5da6f4af67737399',
    'router_manager/migrations/0012_alter_router_router_type.py': 'a7495ae15e33d369378434b2b832511cba4fd2eb942a4d16bc5856e53706147c',
    'router_manager/migrations/0013_alter_router_address.py': '8bea5d91151ee984a17b72a398e4fc1b4e50be95488c1ead98d5f314b547c0fb',
    'router_manager/migrations/0014_alter_router_router_type.py': '8ac14bcb611570d37e5de06fa89f8f772a65315566f50a7286a5e8cdd568a0a8',
    'router_manager/migrations/0015_alter_routerstatus_status_online.py': '14627a100b4d4122e20a82c444acbf99d34f2744f0b3f199538a821973326428',
    'router_manager/migrations/0016_router_port.py': '6a3349aa56a00a45a3eaf7f7374e31d104a2f9c89af1e79ba6cf7c90118f0af3',
    'router_manager/migrations/0017_alter_router_router_type_routerinformation.py': 'ea6c53e8cb47d717b3a014d28f698a14fdc90b348e58eb368ce91e6a807d815d',
    'router_manager/migrations/0018_routerinformation_cpu.py': 'e0276ac0b4f43b026b4fd07987486864e026cf2a4ba284af90368cb08eb89f51',
    'router_manager/migrations/0019_remove_routerinformation_host_id.py': '5590d9003c5e1f254567a0ad0eaa42365f5b42d4dca7a9c7167841a97afe0dae',
    'router_manager/migrations/0020_alter_router_router_type.py': 'd363298c9252fbdb992ccb5d567bcab215e44949762d05d293536bbd5245a80a',
    'router_manager/migrations/0021_routerstatus_command_lock.py': 'd258203794dd9a99794fc2a1e655a57e7bf0d737a90b29f77cbf38b5b7419d93',
    'router_manager/migrations/0022_router_password_encrypted.py': '35619f88b611455a4d0ea0cc2308e5bd167521464609893e09d09c9880650c8a',
    'router_manager/migrations/0023_routerinformation_available_version_and_more.py': 'af4f1cc36f0476471177dcdb53d1d099584f005da781ae9eb5f21e748317052f',
    'router_manager/migrations/0024_secret_encrypted.py': '25968f5bded053e9e72f00d19b493237491787aaac1e05f4c0f47286a56e2767',
    'router_manager/migrations/0025_sshhostkey.py': '7ed81c6d7772c384d551b1ae1bd4698f0aadc87361e60d1e1f575be7736b0b44',
    'router_manager/migrations/0026_router_console_telnet_router_telnet_port.py': 'f8698a7dfa44cc32877fbc50b965343ffcb142be905a6006b062e76daa3c2720',
    'router_manager/migrations/0027_router_enabled_changed_at.py': '632d6d34f59c4d9b750b0a4d00e0e75b65f08c35e056ba5f7a18503be6170d57',
    'router_manager/migrations/0028_fill_enabled_changed_at.py': '57666e0591091c0e345bc3f83a768eb54cdc032c52de2c1d9969805c5bac5975',
    'router_manager/migrations/0029_router_keep_unchanged.py': '2d765589108cc25f7665807a8f8374ab2cdeb111894dd01fed4a8403abc93fd4',
    'router_manager/migrations/0030_device_type.py': '98cd32f4f202b5f0646cc94c4210845f02d59d896d8cfa3d5de2a2d392fa4c83',
    'router_manager/migrations/0031_device_type_presets.py': '9d1295a1ffc31aeaf00419ccac4834ed01ad6835b7ec341e9826ae4a99515f57',
    'router_manager/migrations/0032_device_type_export_words_by_version.py': '4bde9b5689bb870f1543603b54eba045667f5f368c0ae6a81dec94abe4653df8',
    'router_manager/migrations/0033_device_type_update_channels.py': 'af3286b42b86249987e59531df729fa2fab0bddd9c3e1ae2006244ff6d3eb61e',
    'router_manager/migrations/0034_routerinformation_firmware_available.py': 'd64b9d85797f25248f60241491a9c3d564442f14a7f8fe10214a874a16b53e85',
    'router_manager/migrations/0035_routerinformation_update_status.py': '8de826f95b70e4b3452168138b0c439163d54f14bd7e129760282d6e21b04f4a',
    'routerfleet_tools/migrations/0001_initial.py': '489d86f5f2fd075b4e838bcbdfc384b05cc96e1a09774868204fb493c526a24f',
    'routerfleet_tools/migrations/0002_webadminsettings_cron_last_run.py': 'd5ea9f1aacfa393f3041d72b78520f13f647defcf92a59a3f33d96e8e5315634',
    'routerfleet_tools/migrations/0003_webadminsettings_monitoring_last_run_and_more.py': '56a53e01f52b9daf0696d9a090ae63a9bb4d911ef12f5a7d3c7353eeb93a57d5',
    'routerfleet_tools/migrations/0004_https_settings.py': '99816768fe127c64ea8afeca72572c1b98e400cc6ca7b2e4b0845767bff04a92',
    'routerfleet_tools/migrations/0005_display_settings.py': 'd078ae7becaf745b2e2d094818fc881d4d3bd7f64706e6902517681bcd9f96c3',
    'routerfleet_tools/migrations/0006_update_run.py': '4eaf10784a56bbbce730b0b2f8f8f08e1c6b20045a7162c9074c3a059d5cfbb0',
    'user_manager/migrations/0001_initial.py': 'e9ad460c42c6fa79d3d14e1cad8b7f6ffd2c29808637985a567f7a9f93af5947',
    'user_manager/migrations/0002_alter_useracl_user_level.py': 'cde66a426048ff5b5994c6ea63e77df0060053b75cbf8dfb347aa50fcc164aea',
    'user_manager/migrations/0003_useracl_address_link.py': '78d80aecdcddf7a2dcf5f883766178c298ce1521f5ac2c4df2f841a8c844d821',
    'user_manager/migrations/0004_mfa.py': 'e205d0d15eb14a88a3f7215abed6e016d91494b80f2e8a7caceae4939a68b885',
    'user_manager/migrations/0005_level_label_capitalised.py': '1171873e92fa28879ad0e8e801136552ddc57a044fad78962083af4dd8886b73',
    'user_manager/migrations/0006_address_link_labels.py': '448f1e544274fd034411ac3bd8a12fc7020a67976c9ef87a6f75108fdc832bb7',
    'user_manager/migrations/0007_user_phone.py': '775b63c061a44fc040fc5c23bb9edf46fdeef79e76dcedb950335d857def2905',
    'user_manager/migrations/0008_session_hours_list_style.py': 'b1d28c0458ce72f018af089cf84336adb9628057f18ff870bccc86bb69c041e8',
}


class ReleasedMigrationsTests(SimpleTestCase):
    def test_every_migration_is_pinned(self):
        # Pinned when written: a migration that reaches a release unpinned could
        # be edited afterwards without any test noticing (30 were, by 0.24.2)
        for file in sorted(ROOT.glob('*/migrations/[0-9][0-9][0-9][0-9]_*.py')):
            path = file.relative_to(ROOT).as_posix()
            with self.subTest(migration=path):
                self.assertIn(path, RELEASED, f'{path} has no fingerprint in RELEASED: add it')

    def test_no_released_migration_was_changed_renamed_or_deleted(self):
        for path, digest in RELEASED.items():
            with self.subTest(migration=path):
                file = ROOT / path
                self.assertTrue(file.exists(), f'{path} was removed or renamed: predecessor databases would '
                                               'no longer upgrade')
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), digest,
                                 f'{path} was edited after its release: add a new migration instead')


class UpgradeGuideTests(SimpleTestCase):
    """docs/upgrading.md states how many migrations each predecessor applies:
    computed here from the real migration plan, so the guide cannot go stale."""

    def to_apply(self, state):
        graph = MigrationLoader(None, ignore_no_migrations=True).graph
        everything = set()
        for leaf in graph.leaf_nodes():
            everything |= set(graph.forwards_plan(leaf))
        applied = set()
        for app, name in state:
            if name is not None:
                applied |= set(graph.forwards_plan((app, name)))
        django_own = ('auth', 'contenttypes', 'sessions', 'admin', 'messages')
        return len([n for n in everything - applied if n[0] not in django_own])

    def test_the_counts_in_the_guide(self):
        guide = (ROOT / 'docs' / 'upgrading.md').read_text()
        # The later predecessor's row says "N migrations apply", the original's
        # "N additional migrations apply"
        extended = int(re.search(r'\| In place \| (\d+) migrations apply', guide).group(1))
        original = int(re.search(r'\| In place \| (\d+) additional migrations apply', guide).group(1))
        self.assertEqual(extended, self.to_apply(test_upgrade.LATER_PREDECESSOR_STATE))
        self.assertEqual(original, self.to_apply(test_upgrade.ORIGINAL_STATE))
        self.assertIn(f'### What the {original} migrations do when coming from the original', guide)
