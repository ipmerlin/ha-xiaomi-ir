"""Isolated behavioral tests; deliberately NOT a Home Assistant test harness."""

import asyncio
import importlib.util
import sys
import types
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "xiaomi_ir"


def module(name, **attributes):
    result = types.ModuleType(name)
    result.__dict__.update(attributes)
    sys.modules[name] = result
    return result


class DeviceException(Exception):
    pass


class DeviceError(DeviceException):
    def __init__(self, code):
        self.code = code


class HAError(Exception):
    pass


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = coordinator.hass

    async def async_added_to_hass(self):
        pass

    @property
    def unique_id(self):
        return self._attr_unique_id


class RestoreEntity:
    async def async_get_last_state(self):
        return None


class Store:
    """Model persistence by key, including loading into a new entity instance."""

    values = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_save(self, value):
        self.values[self.key] = deepcopy(value)

    async def async_load(self):
        return deepcopy(self.values.get(self.key))


class Flow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    async def async_set_unique_id(self, value):
        self.unique_id = value

    def _abort_if_unique_id_configured(self):
        pass

    def _get_reconfigure_entry(self):
        return self.entry

    def _abort_if_unique_id_mismatch(self):
        if self.entry.unique_id != self.unique_id:
            raise HAError("unique_id_mismatch")

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_update_reload_and_abort(self, entry, **kwargs):
        return {"type": "update_reload", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


class DataUpdateCoordinator:
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()


# Stub only framework/dependency boundaries. Production functions are imported
# and executed intact, including actual locking, persistence, and service loops.
module("miio", ChuangmiIr=MagicMock(), DeviceException=DeviceException)
module("miio.exceptions", DeviceError=DeviceError)
module("homeassistant")
module("homeassistant.config_entries", ConfigFlow=Flow)
module(
    "homeassistant.const",
    Platform=types.SimpleNamespace(REMOTE="remote", INFRARED="infrared"),
    CONF_HOST="host",
    CONF_TOKEN="token",
    CONF_NAME="name",
)
module("homeassistant.helpers")
module(
    "homeassistant.helpers.config_validation",
    config_entry_only_config_schema=lambda d: d,
)
module(
    "homeassistant.helpers.selector",
    TextSelector=lambda x: x,
    TextSelectorConfig=dict,
    TextSelectorType=types.SimpleNamespace(PASSWORD="password"),
)
module("probatio", Schema=lambda d: d, Required=lambda k, **kwargs: k)
notifications = module(
    "homeassistant.components.persistent_notification",
    async_create=MagicMock(),
    async_dismiss=MagicMock(),
)
module("homeassistant.components", persistent_notification=notifications)
module(
    "homeassistant.components.remote",
    RemoteEntity=type("RemoteEntity", (), {}),
    RemoteEntityFeature=types.SimpleNamespace(LEARN_COMMAND=1, DELETE_COMMAND=2),
)
module(
    "homeassistant.components.infrared",
    InfraredEmitterEntity=type("InfraredEmitterEntity", (), {}),
)
module(
    "homeassistant.exceptions",
    HomeAssistantError=HAError,
    ServiceValidationError=HAError,
)
module("homeassistant.helpers.device_registry", DeviceInfo=dict)
module("homeassistant.helpers.restore_state", RestoreEntity=RestoreEntity)
module("homeassistant.helpers.storage", Store=Store)
module(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=CoordinatorEntity,
    DataUpdateCoordinator=DataUpdateCoordinator,
    UpdateFailed=HAError,
)
module("custom_components").__path__ = [str(ROOT / "custom_components")]
module("custom_components.xiaomi_ir").__path__ = [str(COMPONENT)]

from custom_components.xiaomi_ir import (  # noqa: E402
    commands,
    config_flow,
    coordinator,
    infrared,
    protocol,
    remote,
)


def hass():
    async def executor(function, *args):
        return function(*args)

    return types.SimpleNamespace(async_add_executor_job=executor)


def entry():
    info = types.SimpleNamespace(firmware_version="test", hardware_version="test")
    runtime = types.SimpleNamespace(
        hass=hass(),
        data=info,
        lock=asyncio.Lock(),
        device=MagicMock(),
        async_io=AsyncMock(),
    )
    return types.SimpleNamespace(
        unique_id="aabbccddeeff",
        title="Xiaomi IR",
        runtime_data=runtime,
        data={"host": "192.0.2.1", "token": "0" * 32},
    )


class ProtocolTests(unittest.TestCase):
    def test_token(self):
        self.assertEqual(protocol.validate_token(" A" + "B" * 31 + " "), "a" + "b" * 31)
        for token in ["x" * 32, "a" * 31, "a" * 33, ""]:
            with self.assertRaises(ValueError):
                protocol.validate_token(token)

    def test_mac(self):
        self.assertEqual(protocol.mac_id("AA:BB:CC:DD:EE:FF"), "aabbccddeeff")
        for value in [None, "", "00:00:00:00:00:00", "wrong"]:
            with self.assertRaises(ValueError):
                protocol.mac_id(value)

    def test_no_retries_and_rediscovery(self):
        with patch.object(protocol, "ChuangmiIr") as device:
            client = protocol.create_device("192.0.2.1", "0" * 32)
            self.assertEqual(client.retry_count, 0)
            self.assertFalse(device.call_args.kwargs["lazy_discover"])

    def test_read_pending_and_success(self):
        device = MagicMock()
        device.read.side_effect = [
            DeviceError(-5002),
            DeviceError(-5003),
            {"key": "7", "code": "YQ=="},
        ]
        self.assertIsNone(protocol.read_code(device, 7))
        self.assertIsNone(protocol.read_code(device, 7))
        self.assertEqual(protocol.read_code(device, 7), "raw:YQ==")

    def test_bad_read_and_unrelated_errors(self):
        device = MagicMock()
        for response in [
            None,
            [],
            {},
            {"key": "8", "code": "YQ=="},
            {"key": "7", "code": "!"},
            {"key": "7", "code": ""},
            {"error": {"code": -1}},
        ]:
            device.read.return_value = response
            with self.assertRaises(ValueError):
                protocol.read_code(device, 7)
        device.read.side_effect = DeviceError(-1)
        with self.assertRaises(DeviceError):
            protocol.read_code(device, 7)

    def test_ack(self):
        for response in ["ok", ["ok"], {"result": ["ok"]}]:
            protocol.check_ack(response)
        for response in [None, [], {}, False, {"error": "failure"}]:
            with self.assertRaises(ValueError):
                protocol.check_ack(response)


class InfraredEncodingTests(unittest.TestCase):
    def test_carrier_and_waveform_preserved_within_one_carrier_cycle(self):
        source = [9000, -4500, 560, -1690, 560, -560]
        words = [
            int(word, 16) for word in infrared.timings_to_pronto(source, 38000).split()
        ]
        self.assertEqual(words[:4], [0, 109, 3, 0])
        period = words[1] * infrared.PRONTO_TICK_US
        for expected, cycles in zip(source, words[4:], strict=True):
            self.assertLess(abs(abs(expected) - cycles * period), period)

    def test_final_pulse_supported(self):
        words = infrared.timings_to_pronto([560, -560, 560], 38000).split()
        self.assertEqual(int(words[2], 16), 2)
        self.assertEqual(len(words), 8)

    def test_bad_signs_or_empty_signal_rejected(self):
        for timings in [[], [-560, 560], [560, 560], [560, 0], [True, -560]]:
            with self.assertRaises(ValueError):
                infrared.timings_to_pronto(timings, 38000)

    def test_invalid_carrier_rejected(self):
        for frequency in [None, 0, -38000, True, 38000.0, 1]:
            with self.assertRaises(ValueError):
                infrared.timings_to_pronto([560, -560], frequency)

    def test_duration_table_overflow_rejected(self):
        timings = [value * 1000 * (1 if value % 2 else -1) for value in range(1, 19)]
        with self.assertRaisesRegex(ValueError, "sixteen"):
            infrared.timings_to_pronto(timings, 38000)

    def test_long_duration_rejected(self):
        with self.assertRaises(ValueError):
            infrared.timings_to_pronto([560, -10_000_000], 38000)


class InfraredEntityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = entry()
        self.emitter = infrared.XiaomiInfraredEmitter(self.entry)
        self.command = types.SimpleNamespace(
            modulation=38000, get_raw_timings=lambda: [560, -560]
        )
        self.io = self.entry.runtime_data.async_io
        self.io.return_value = ["ok"]

    async def test_same_device_and_separate_entity(self):
        self.assertEqual(
            self.emitter._attr_device_info["identifiers"],
            {("xiaomi_ir", self.entry.unique_id)},
        )
        self.assertEqual(self.emitter.unique_id, self.entry.unique_id + "_emitter")
        added = MagicMock()
        await infrared.async_setup_entry(
            self.entry.runtime_data.hass, self.entry, added
        )
        self.assertIsInstance(
            added.call_args.args[0][0], infrared.XiaomiInfraredEmitter
        )

    async def test_standard_command_sent_once_without_extra_repeats(self):
        with patch.object(
            infrared.ChuangmiIr, "pronto_to_raw", return_value=("YQ==", 38028)
        ) as encode:
            await self.emitter.async_send_command(self.command)
        self.assertEqual(encode.call_args.kwargs, {"repeats": 0})
        self.io.assert_awaited_once_with(
            self.entry.runtime_data.device.play_raw, "YQ==", 38028
        )

    async def test_invalid_command_never_reaches_hardware(self):
        self.command.modulation = 0
        with self.assertRaises(HAError):
            await self.emitter.async_send_command(self.command)
        self.io.assert_not_awaited()

    async def test_failed_send_not_retried(self):
        self.io.side_effect = DeviceException()
        with patch.object(
            infrared.ChuangmiIr, "pronto_to_raw", return_value=("YQ==", 38000)
        ):
            with self.assertRaisesRegex(HAError, "not retried"):
                await self.emitter.async_send_command(self.command)
        self.assertEqual(self.io.await_count, 1)

    async def test_bad_ack_is_failure(self):
        self.io.return_value = None
        with patch.object(
            infrared.ChuangmiIr, "pronto_to_raw", return_value=("YQ==", 38000)
        ):
            with self.assertRaises(HAError):
                await self.emitter.async_send_command(self.command)

    async def test_emitter_waits_for_existing_remote_operation(self):
        await self.entry.runtime_data.lock.acquire()
        with patch.object(
            infrared.ChuangmiIr, "pronto_to_raw", return_value=("YQ==", 38000)
        ):
            task = asyncio.create_task(self.emitter.async_send_command(self.command))
            await asyncio.sleep(0)
            self.io.assert_not_awaited()
            self.entry.runtime_data.lock.release()
            await task
        self.assertEqual(self.io.await_count, 1)


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.library = {"tv": {"power": "raw:YQ=="}, "amp": {"power": "raw:Yg=="}}

    def test_device_namespaces(self):
        self.assertEqual(
            commands.resolve_commands(self.library, ["power"], "amp"), ["raw:Yg=="]
        )
        with self.assertRaises(ValueError):
            commands.resolve_commands(self.library, ["power"])

    def test_delete_atomic(self):
        with self.assertRaises(ValueError):
            commands.delete_commands(self.library, ["power", "missing"], "tv")
        self.assertIn("power", self.library["tv"])
        result = commands.delete_commands(self.library, ["power"], "tv")
        self.assertNotIn("tv", result)
        self.assertIn("amp", result)

    def test_corrupt_storage(self):
        for data in [[], {"tv": []}, {"tv": {"power": 1}}, {"": {}}]:
            with self.assertRaises(ValueError):
                commands.validate_library(data)


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Store.values.clear()
        self.entry = entry()
        self.remote = remote.XiaomiIRRemote(self.entry)
        self.remote.library = {"tv": {"power": "raw:YQ==", "up": "raw:Yg=="}}
        self.io = self.entry.runtime_data.async_io
        self.io.return_value = ["ok"]

    async def test_send_repeats(self):
        await self.remote.async_send_command(
            ["power", "up"], device="tv", num_repeats=2, delay_secs=0
        )
        self.assertEqual(self.io.await_count, 4)

    async def test_missing_sequence_never_partially_sends(self):
        with self.assertRaises(HAError):
            await self.remote.async_send_command(["power", "missing"], device="tv")
        self.io.assert_not_awaited()

    async def test_send_error_never_retries(self):
        self.io.side_effect = DeviceException()
        with self.assertRaisesRegex(HAError, "not retried"):
            await self.remote.async_send_command(["power"], device="tv", num_repeats=3)
        self.assertEqual(self.io.await_count, 1)

    async def test_bad_ack_never_continues(self):
        self.io.return_value = None
        with self.assertRaises(HAError):
            await self.remote.async_send_command(["power", "up"], device="tv")
        self.assertEqual(self.io.await_count, 1)

    async def test_invalid_code_sequence_never_sends(self):
        with self.assertRaises(HAError):
            await self.remote.async_send_command(["raw:YQ==", "raw:!"])
        self.io.assert_not_awaited()

    async def test_disabled_remote(self):
        self.remote._attr_is_on = False
        with self.assertRaises(HAError):
            await self.remote.async_send_command(["power"], device="tv")
        self.io.assert_not_awaited()

    async def test_unsupported_options(self):
        for kw in [{"command_type": "rf"}, {"alternative": True}, {"timeout": 0}]:
            with self.assertRaises(HAError):
                await self.remote.async_learn_command(
                    device="tv", command="power", **kw
                )
        with self.assertRaises(HAError):
            await self.remote.async_send_command(["power"], device="tv", hold_secs=1)

    async def test_successful_learning_persists_across_instances(self):
        with patch.object(
            self.remote, "_learn_one", AsyncMock(return_value="raw:Yw==")
        ):
            await self.remote.async_learn_command(device="tv", command="power")
        other = remote.XiaomiIRRemote(self.entry)
        other.library = commands.validate_library(await other.store.async_load())
        self.assertEqual(other.library["tv"]["power"], "raw:Yw==")

    async def test_failed_learning_preserves_old_code(self):
        with patch.object(self.remote, "_learn_one", AsyncMock(side_effect=HAError())):
            with self.assertRaises(HAError):
                await self.remote.async_learn_command(device="tv", command="power")
        self.assertEqual(self.remote.library["tv"]["power"], "raw:YQ==")

    async def test_partial_learning_keeps_successful_command(self):
        with patch.object(
            self.remote, "_learn_one", AsyncMock(side_effect=["raw:Yw==", HAError()])
        ):
            with self.assertRaises(HAError):
                await self.remote.async_learn_command(
                    device="tv", command=["power", "up"]
                )
        saved = await self.remote.store.async_load()
        self.assertEqual(saved["tv"]["power"], "raw:Yw==")
        self.assertEqual(saved["tv"]["up"], "raw:Yg==")

    async def test_save_failure_does_not_mutate_library(self):
        with patch.object(
            self.remote.store, "async_save", AsyncMock(side_effect=OSError())
        ):
            with self.assertRaises(OSError):
                await self.remote.async_delete_command(device="tv", command="power")
        self.assertIn("power", self.remote.library["tv"])

    async def test_delete_persists(self):
        await self.remote.async_delete_command(device="tv", command="power")
        self.assertNotIn("power", (await self.remote.store.async_load())["tv"])

    async def test_learning_and_sending_are_serialized(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def learn(*args):
            entered.set()
            await release.wait()
            return "raw:Yw=="

        with patch.object(self.remote, "_learn_one", learn):
            learning = asyncio.create_task(
                self.remote.async_learn_command(device="tv", command="power")
            )
            await entered.wait()
            sending = asyncio.create_task(
                self.remote.async_send_command(["power"], device="tv")
            )
            await asyncio.sleep(0)
            self.io.assert_not_awaited()
            release.set()
            await asyncio.gather(learning, sending)
        self.assertEqual(self.io.call_args.args[1], "raw:Yw==")

    async def test_actual_learn_loop_and_notification_cleanup(self):
        self.io.side_effect = [None, ["ok"], None, "raw:Yw=="]
        with patch.object(remote.asyncio, "sleep", AsyncMock()):
            self.assertEqual(
                await self.remote._learn_one("tv", "power", 30), "raw:Yw=="
            )
        notifications.async_create.assert_called()
        notifications.async_dismiss.assert_called()

    async def test_occupied_slots_not_reused(self):
        self.io.side_effect = ["raw:old"] * 5
        with self.assertRaises(HAError):
            await self.remote._learn_one("tv", "power", 30)
        self.assertEqual(self.io.await_count, 5)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = config_flow.XiaomiIRConfigFlow()
        self.flow.hass = hass()
        self.data = {"name": "IR", "host": "192.0.2.1", "token": "a" * 32}
        self.device = MagicMock()
        self.device.info.return_value = types.SimpleNamespace(
            model="chuangmi.ir.v2", mac_address="aa:bb:cc:dd:ee:ff"
        )

    async def test_create_and_identity(self):
        with patch.object(config_flow, "create_device", return_value=self.device):
            result = await self.flow.async_step_user(self.data)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.flow.unique_id, "aabbccddeeff")

    async def test_invalid_token_does_not_connect(self):
        with patch.object(config_flow, "create_device") as factory:
            result = await self.flow.async_step_user({**self.data, "token": "bad"})
        factory.assert_not_called()
        self.assertEqual(result["errors"], {"token": "invalid_token"})

    async def test_wrong_model(self):
        self.device.info.return_value.model = "another.device"
        with patch.object(config_flow, "create_device", return_value=self.device):
            result = await self.flow.async_step_user(self.data)
        self.assertEqual(result["errors"]["base"], "unsupported_model")

    async def test_unavailable_device(self):
        self.device.info.side_effect = DeviceException()
        with patch.object(config_flow, "create_device", return_value=self.device):
            result = await self.flow.async_step_user(self.data)
        self.assertEqual(result["errors"]["base"], "cannot_connect")

    async def test_reconfigure_same_device(self):
        self.flow.entry = entry()
        with patch.object(config_flow, "create_device", return_value=self.device):
            result = await self.flow.async_step_reconfigure(self.data)
        self.assertEqual(result["type"], "update_reload")

    async def test_reconfigure_rejects_different_device(self):
        self.flow.entry = entry()
        self.device.info.return_value.mac_address = "11:22:33:44:55:66"
        with patch.object(config_flow, "create_device", return_value=self.device):
            with self.assertRaisesRegex(HAError, "unique_id_mismatch"):
                await self.flow.async_step_reconfigure(self.data)


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_refresh_bypasses_info_cache(self):
        item = entry()
        client = MagicMock()
        client.info.return_value = types.SimpleNamespace(
            model="chuangmi.ir.v2", mac_address="aa:bb:cc:dd:ee:ff"
        )
        with patch.object(coordinator, "create_device", return_value=client):
            instance = coordinator.XiaomiIRCoordinator(hass(), item)
        await instance._async_update_data()
        client.info.assert_called_once_with(skip_cache=True)
        client.info.side_effect = DeviceException()
        with self.assertRaises(HAError):
            await instance._async_update_data()

    async def test_cancel_waits_for_worker(self):
        item = entry()
        gate = asyncio.Event()

        async def worker():
            await gate.wait()

        job = asyncio.create_task(worker())
        fake_hass = types.SimpleNamespace(async_add_executor_job=lambda f: job)
        with patch.object(coordinator, "create_device"):
            instance = coordinator.XiaomiIRCoordinator(fake_hass, item)
        task = asyncio.create_task(instance.async_io(lambda: None))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        try:
            self.assertFalse(task.done())
            self.assertFalse(job.cancelled())
        finally:
            gate.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)

    async def test_entry_lifecycle(self):
        spec = importlib.util.spec_from_file_location(
            "custom_components.xiaomi_ir", COMPONENT / "__init__.py"
        )
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        fake_hass = hass()
        fake_hass.config_entries = types.SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        )
        instance = types.SimpleNamespace(async_config_entry_first_refresh=AsyncMock())
        item = entry()
        with patch.object(lifecycle, "XiaomiIRCoordinator", return_value=instance):
            self.assertTrue(await lifecycle.async_setup_entry(fake_hass, item))
        self.assertIs(item.runtime_data, instance)
        fake_hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
            item, ["remote", "infrared"]
        )
        self.assertTrue(await lifecycle.async_unload_entry(fake_hass, item))
        fake_hass.config_entries.async_unload_platforms.assert_awaited_once_with(
            item, ["remote", "infrared"]
        )


if __name__ == "__main__":
    unittest.main()
