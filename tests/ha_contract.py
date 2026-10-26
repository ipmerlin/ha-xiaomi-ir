"""Contract checks with real Home Assistant/miIO APIs, without network traffic.

Run in a separate process from test_integration.py, which stubs its imports.
"""

import asyncio
import base64
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.components.infrared import InfraredEmitterEntity  # noqa: E402
from homeassistant.const import Platform  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from infrared_protocols.commands.nec import NECCommand  # noqa: E402
from miio.chuangmi_ir import ChuangmiIrSignal  # noqa: E402
from miio.exceptions import DeviceError  # noqa: E402

from custom_components.xiaomi_ir import PLATFORMS  # noqa: E402
from custom_components.xiaomi_ir.infrared import XiaomiInfraredEmitter  # noqa: E402
from custom_components.xiaomi_ir.protocol import create_device, read_code  # noqa: E402
from custom_components.xiaomi_ir.remote import XiaomiIRRemote  # noqa: E402


class HomeAssistantContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = create_device("192.0.2.1", "0" * 32)
        # Mock only transport, keeping the actual miIO encoder and client API.
        self.client.send = Mock(return_value=["ok"])

        async def invoke(function, *args):
            return function(*args)

        info = types.SimpleNamespace(firmware_version="test", hardware_version="test")
        runtime = types.SimpleNamespace(
            hass=None,
            config_entry=None,
            data=info,
            last_update_success=True,
            lock=asyncio.Lock(),
            device=self.client,
            async_io=invoke,
        )
        self.entry = types.SimpleNamespace(
            title="Xiaomi IR",
            unique_id="aabbccddeeff",
            runtime_data=runtime,
        )
        self.emitter = XiaomiInfraredEmitter(self.entry)
        self.emitter.async_write_ha_state = Mock()

    async def test_actual_miio_learning_accepts_zero_and_persists(self):
        entity = XiaomiIRRemote(self.entry)
        entity.hass = Mock()
        entity.library = {}
        for acknowledgement in (0, [0], {"result": 0}, ["ok"]):
            with self.subTest(acknowledgement=acknowledgement):
                self.client.send.reset_mock()
                self.client.send.side_effect = [
                    {"error": {"code": 0, "message": "no code for this key"}},
                    acknowledgement,
                    {"key": "7", "code": "Yw=="},
                ]
                with (
                    patch(
                        "custom_components.xiaomi_ir.remote.secrets.randbelow",
                        return_value=6,
                    ),
                    patch(
                        "custom_components.xiaomi_ir.remote.asyncio.sleep",
                        new_callable=AsyncMock,
                    ),
                    patch(
                        "custom_components.xiaomi_ir.remote.persistent_notification.async_create"
                    ),
                    patch(
                        "custom_components.xiaomi_ir.remote.persistent_notification.async_dismiss"
                    ),
                    patch.object(
                        entity.store, "async_save", new_callable=AsyncMock
                    ) as save,
                ):
                    await entity.async_learn_command(
                        device="harman_kardon", command=["power_on"]
                    )
                save.assert_awaited_once_with(
                    {"harman_kardon": {"power_on": "raw:Yw=="}}
                )
                self.assertEqual(
                    entity.library["harman_kardon"]["power_on"], "raw:Yw=="
                )
                methods = [call.args[0] for call in self.client.send.call_args_list]
                self.assertEqual(
                    methods, ["miIO.ir_read", "miIO.ir_learn", "miIO.ir_read"]
                )

    async def test_real_device_error_legacy_empty_slot(self):
        self.client.send.side_effect = DeviceError(
            {"code": 0, "message": "no code for this key"}
        )
        self.assertIsNone(read_code(self.client, 7))
        self.client.send.side_effect = DeviceError(
            {"code": 0, "message": "unknown failure"}
        )
        with self.assertRaises(DeviceError):
            read_code(self.client, 7)

    async def test_platform_and_actual_base_class(self):
        self.assertIn(Platform.INFRARED, PLATFORMS)
        self.assertIn(Platform.REMOTE, PLATFORMS)
        self.assertIsInstance(self.emitter, InfraredEmitterEntity)
        self.assertTrue(self.emitter.available)
        self.entry.runtime_data.last_update_success = False
        self.assertFalse(self.emitter.available)

    async def test_real_nec_waveform_encoded_into_xiaomi_packet(self):
        command = NECCommand(address=0x04, command=0x08)
        expected = command.get_raw_timings()
        await self.emitter.async_send_command(command)
        self.client.send.assert_called_once()
        method, payload = self.client.send.call_args.args
        self.assertEqual(method, "miIO.ir_play")
        self.assertLess(
            abs(payload["freq"] - command.modulation), command.modulation * 0.01
        )
        signal = ChuangmiIrSignal.parse(base64.b64decode(payload["code"]))
        decoded = []
        for pair in signal.edge_pairs:
            decoded.extend(
                [signal.times_index[pair.pulse], -signal.times_index[pair.gap]]
            )
        self.assertIn(len(decoded) - len(expected), (0, 1))
        for actual, wanted in zip(decoded, expected):
            self.assertLessEqual(
                abs(actual - wanted), 1_000_000 / command.modulation + 1
            )

    async def test_timestamp_only_advances_after_successful_send(self):
        command = NECCommand(address=0x04, command=0x08)
        await self.emitter.async_send_command_internal(command)
        timestamp = self.emitter.state
        self.assertIsNotNone(timestamp)
        self.client.send.return_value = None
        with self.assertRaises(HomeAssistantError):
            await self.emitter.async_send_command_internal(command)
        self.assertEqual(self.emitter.state, timestamp)

    async def test_legacy_zero_status_updates_timestamp_without_retransmission(self):
        self.client.send.return_value = 0
        command = NECCommand(address=0x04, command=0x08)
        await self.emitter.async_send_command_internal(command)
        self.client.send.assert_called_once()
        self.assertIsNotNone(self.emitter.state)


if __name__ == "__main__":
    unittest.main(verbosity=2)
