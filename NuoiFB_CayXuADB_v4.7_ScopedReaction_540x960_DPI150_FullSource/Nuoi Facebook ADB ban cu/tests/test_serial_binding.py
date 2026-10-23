from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock
import threading

from automation.device_session import DeviceBootstrapService
from automation.feed_runner import FeedRunner
from core.command_runner import CommandResult
from core.config import AppSettings, RuntimeConfig
from core.ldconsole import LDConsoleManager


class FakeCommands:
    def __init__(self, devices: str, *, host_verified: bool = True, state: str = "device"):
        self.devices = devices
        self.host_verified = host_verified
        self.state = state
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        command = [str(arg) for arg in args]
        self.calls.append(command)
        if command[-2:] == ["devices", "-l"]:
            return CommandResult(command, 0, self.devices, "")
        if command[0] == "powershell.exe":
            return CommandResult(command, 0 if self.host_verified else 2,
                                 "VERIFIED\n" if self.host_verified else "", "")
        if command[-1] == "get-state":
            return CommandResult(command, 0, self.state + "\n", "")
        raise AssertionError(f"Unexpected command: {command}")


def manager_with_fake(fake: FakeCommands) -> LDConsoleManager:
    manager = LDConsoleManager("C:/unused", runner=fake)
    manager.is_instance_running_by_ldconsole = lambda _index: True
    return manager


class SerialBindingTests(TestCase):
    def test_emulator_alias_requires_host_proof(self):
        fake = FakeCommands("List of devices attached\nemulator-5588 device product:test\n")
        manager = manager_with_fake(fake)
        self.assertEqual(manager.verified_adb_serial_for_index(17), "emulator-5588")
        proof = next(call for call in fake.calls if call[0] == "powershell.exe")
        self.assertIn("leidian17", proof[-1])
        self.assertIn("5589", proof[-1])
        self.assertEqual(fake.calls[-1][-3:], ["-s", "emulator-5588", "get-state"])

    def test_existing_localhost_alias_remains_supported(self):
        fake = FakeCommands("localhost:5589 device\n")
        self.assertEqual(manager_with_fake(fake).verified_adb_serial_for_index(17), "localhost:5589")

    def test_missing_ambiguous_and_offline_transports_fail_closed(self):
        for devices in (
            "emulator-5590 device\n",
            "emulator-5588 device\nlocalhost:5589 device\n",
            "emulator-5588 offline\n",
        ):
            with self.subTest(devices=devices):
                fake = FakeCommands(devices)
                with self.assertRaisesRegex(RuntimeError, "DEVICE_MAPPING_UNVERIFIED"):
                    manager_with_fake(fake).verified_adb_serial_for_index(17)
                self.assertFalse(any(call[0] == "powershell.exe" for call in fake.calls))

    def test_wrong_index_does_not_use_another_transport(self):
        fake = FakeCommands("emulator-5588 device\n")
        with self.assertRaisesRegex(RuntimeError, "DEVICE_MAPPING_UNVERIFIED"):
            manager_with_fake(fake).verified_adb_serial_for_index(18)

    def test_host_mismatch_and_connection_loss_do_not_fallback(self):
        for host_verified, state in ((False, "device"), (True, "offline")):
            with self.subTest(host_verified=host_verified, state=state):
                fake = FakeCommands("emulator-5588 device\n", host_verified=host_verified, state=state)
                with self.assertRaisesRegex(RuntimeError, "DEVICE_MAPPING_UNVERIFIED"):
                    manager_with_fake(fake).verified_adb_serial_for_index(17)
                self.assertFalse(any("connect" in call or "launch" in call or "quit" in call
                                     for call in fake.calls))

    def test_unconfirmed_instance_never_queries_or_starts_device(self):
        fake = FakeCommands("emulator-5588 device\n")
        manager = manager_with_fake(fake)
        manager.is_instance_running_by_ldconsole = lambda _index: False
        with self.assertRaisesRegex(RuntimeError, "DEVICE_MAPPING_UNVERIFIED"):
            manager.verified_adb_serial_for_index(17)
        self.assertEqual(fake.calls, [])

    def test_feed_runner_blocks_before_bootstrap_side_effects(self):
        config = RuntimeConfig()
        settings = AppSettings(ld_base="C:/unused", runtime_config=config)
        runner = FeedRunner(17, settings, config, threading.Event(), threading.Event())
        runner.ld.verified_adb_serial_for_index = Mock(side_effect=RuntimeError("DEVICE_MAPPING_UNVERIFIED"))
        runner.bootstrap_service.bootstrap = Mock()
        self.assertFalse(runner.bootstrap())
        runner.bootstrap_service.bootstrap.assert_not_called()
        self.assertEqual(runner.stats.status, "ERROR")

    def test_feed_runner_uses_bound_serial_without_changing_shared_default(self):
        config = RuntimeConfig()
        settings = AppSettings(ld_base="C:/unused", runtime_config=config)
        stop, pause = threading.Event(), threading.Event()
        shared = DeviceBootstrapService(17, settings, config, stop, pause)
        self.assertFalse(shared.require_existing)
        self.assertEqual(shared.device.serial, "localhost:5589")
        runner = FeedRunner(17, settings, config, stop, pause)
        runner.ld.verified_adb_serial_for_index = Mock(return_value="emulator-5588")
        runner.bootstrap_service.bootstrap = Mock(side_effect=lambda: SimpleNamespace(
            ld=runner.ld, device=runner.bootstrap_service.device, screen_size=None,
        ))
        self.assertTrue(runner.bootstrap())
        self.assertEqual(runner.device.serial, "emulator-5588")

    def test_bound_feed_session_never_launches_restarts_opens_or_quits(self):
        config = RuntimeConfig(auto_quit_ld_after_run=True)
        settings = AppSettings(ld_base="C:/unused", runtime_config=config)
        stop, pause = threading.Event(), threading.Event()
        runner = FeedRunner(17, settings, config, stop, pause)
        service = runner.bootstrap_service
        self.assertTrue(service.require_existing)
        service.ld.is_instance_running_by_ldconsole = Mock(return_value=False)
        service.ld.launch = Mock()
        service.ld.quit = Mock()
        service.ld.run_app = Mock()
        service.device.is_package_foreground = Mock(return_value=False)
        service.device.open_app_with_monkey = Mock()
        self.assertFalse(service.ensure_ldplayer_started())
        self.assertIsNone(service.restart_after_adb_not_ready())
        self.assertFalse(service.open_facebook())
        runner.stats.status = "DONE"
        runner._quit_ldplayer_after_done()
        service.ld.launch.assert_not_called()
        service.ld.quit.assert_not_called()
        service.ld.run_app.assert_not_called()
        service.device.open_app_with_monkey.assert_not_called()

    def test_connection_loss_after_binding_cannot_restart(self):
        config = RuntimeConfig(ld_settle_sec=0)
        settings = AppSettings(ld_base="C:/unused", runtime_config=config)
        service = DeviceBootstrapService(
            17, settings, config, threading.Event(), threading.Event(), require_existing=True,
        )
        service.ld.validate_paths = Mock(return_value=(True, []))
        service.ld.is_instance_running_by_ldconsole = Mock(return_value=True)
        service.ld.launch = Mock()
        service.ld.quit = Mock()
        service.device.get_state = Mock(return_value="offline")
        service.device.connect = Mock()
        service.device.disconnect = Mock()
        service.device.devices_list = Mock(return_value="emulator-5588 offline")
        self.assertIsNone(service.bootstrap())
        service.ld.launch.assert_not_called()
        service.ld.quit.assert_not_called()
        service.device.connect.assert_not_called()
        service.device.disconnect.assert_not_called()

    def test_bound_bootstrap_uses_read_only_probes(self):
        config = RuntimeConfig(ld_settle_sec=0, launch_app=False)
        settings = AppSettings(ld_base="C:/unused", runtime_config=config)
        service = DeviceBootstrapService(
            17, settings, config, threading.Event(), threading.Event(), require_existing=True,
        )
        service.ld.validate_paths = Mock(return_value=(True, []))
        service.ld.is_instance_running_by_ldconsole = Mock(return_value=True)
        service.ld.launch = Mock()
        service.ld.quit = Mock()
        service.ld.run_app = Mock()
        service.device.get_state = Mock(return_value="device")
        service.device.shell = Mock(return_value=SimpleNamespace(ok=True, stdout="1\n"))
        service.device.is_package_installed = Mock(return_value=True)
        service.device.screen_size = Mock(return_value=None)
        service.device.is_package_foreground = Mock(return_value=True)
        service.device.connect = Mock()
        service.device.disconnect = Mock()
        service.device.open_app_with_monkey = Mock()
        self.assertIsNotNone(service.bootstrap())
        service.ld.launch.assert_not_called()
        service.ld.quit.assert_not_called()
        service.ld.run_app.assert_not_called()
        service.device.connect.assert_not_called()
        service.device.disconnect.assert_not_called()
        service.device.open_app_with_monkey.assert_not_called()
