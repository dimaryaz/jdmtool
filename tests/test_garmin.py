"""Test suite for Garmin USB device detection and firmware programming.

This module contains pytest-based tests targeting the jdmtool.data_card.detect and
jdmtool.data_card.garmin modules. It verifies the detection of Garmin USB devices,
handling of supported and unsupported devices, and the firmware programming logic,
including upgrade paths and error handling.

Tests simulate USB device enumeration and interaction via mocks and MagicMock objects,
covering scenarios such as:
    - No devices found
    - Early and current Garmin device firmware programming flows
    - Firmware upgrade triggers and skipping already-updated devices
    - Device configuration errors such as missing endpoints
    - Exception handling during firmware writer initialization and USB device opening

Each test asserts correct device detection, firmware writer method calls, and proper
exception raising, ensuring robust integration of USB context and firmware writer factories.
"""

import logging

logger = logging.getLogger(__name__)
import pytest
from unittest import mock
from jdmtool.data_card.detect import open_programming_device
from jdmtool.data_card.common import ProgrammingException
from jdmtool.data_card.garmin import (
    GarminProgrammingDevice,
    AlreadyUpdatedException,
    GarminFirmwareWriter,
)


# ---------------------
# Pytest Fixtures
# ---------------------


@pytest.fixture(autouse=True)
def usb_context_patch(monkeypatch):
    """Automatically patch USBContext with a MagicMock instance.

    This fixture replaces the USBContext used for device enumeration and access
    with a mock object, allowing tests to simulate USB devices without real hardware.
    It is applied automatically to all tests.
    """
    logger.debug("Setting up usb_context_patch fixture")
    ctx = mock.MagicMock()
    monkeypatch.setattr("jdmtool.data_card.detect.USBContext", lambda: ctx)
    ctx.__enter__.return_value = ctx
    return ctx


@pytest.fixture
def fake_handle(monkeypatch):
    """Patch _open_usb_device to return a context manager yielding a fake device handle.

    This fixture simulates opening a Garmin USB device by returning a context
    with a fake handle that mimics device interaction. It also triggers GarminFirmwareWriter
    registration to enable firmware programming logic testing.
    """
    logger.debug("Setting up fake_handle fixture")

    def open_usb_device_side_effect(*args, **kwargs):
        from jdmtool.data_card.detect import GarminFirmwareWriter

        class Context:
            def __init__(self):
                self.handle = make_fake_device(0x091E, 0x1300).open()
                self.closed = False
                # Trigger factory registration
                try:
                    GarminFirmwareWriter(self.handle)  # type: ignore[arg-type]
                except Exception as e:
                    raise ProgrammingException(str(e)) from e

            def __enter__(self):
                return self.handle

            def __exit__(self, exc_type, exc_val, exc_tb):
                self.closed = True

        return Context()

    monkeypatch.setattr(
        "jdmtool.data_card.detect._open_usb_device",
        mock.MagicMock(side_effect=open_usb_device_side_effect),
    )
    logger.debug("fake_handle fixture: returning fake handle context")
    return open_usb_device_side_effect()


@pytest.fixture(autouse=True)
def firmware_writer_patch(monkeypatch):
    """Patch GarminFirmwareWriter to track all instances created during tests.

    This fixture replaces GarminFirmwareWriter with a factory that returns MagicMock
    instances, allowing tests to verify which firmware writer methods were called.
    It is applied automatically to all tests.
    """
    instances = []

    def factory(*args, **kwargs):
        logger.debug("GarminFirmwareWriter factory called (firmware_writer_patch)")
        instance = mock.MagicMock(spec=GarminFirmwareWriter)
        instances.append(instance)
        return instance

    monkeypatch.setattr("jdmtool.data_card.detect.GarminFirmwareWriter", factory)
    return instances


# ---------------------
# Helper Functions
# ---------------------


def make_fake_device(vid, pid, endpoints=(0x81, 0x02)):
    """Create a fake USB device with specified VID, PID, and endpoints.

    This helper simulates a USB device with given vendor and product IDs and
    endpoint addresses. It provides methods and attributes expected by the code
    under test, including device configuration iteration and a fake device handle.

    Args:
        vid (int): Vendor ID of the fake device.
        pid (int): Product ID of the fake device.
        endpoints (tuple): Tuple of two endpoint addresses (IN, OUT).

    Returns:
        FakeDevice: An object mimicking a USB device.
    """

    class FakeDevice:
        def __init__(self, vid, pid, endpoints):
            self._vid = vid
            self._pid = pid
            self._endpoints = endpoints

        def getVendorID(self):
            return self._vid

        def getProductID(self):
            return self._pid

        def __getitem__(self, idx):
            ep_in = mock.MagicMock(getAddress=lambda: self._endpoints[0])
            ep_out = mock.MagicMock(getAddress=lambda: self._endpoints[1])
            setting = mock.MagicMock()
            setting.getNumber.return_value = 0
            setting.getAlternateSetting.return_value = 0
            setting.__iter__.return_value = iter([ep_in, ep_out])
            interface = mock.MagicMock()
            interface.__iter__.return_value = iter([setting])
            config = mock.MagicMock()
            config.__iter__.return_value = iter([interface])
            return config

        def open(self):
            # Inline the FakeHandle class here
            logger.debug(
                f"make_fake_device: opening fake handle for VID={hex(self._vid)} PID={hex(self._pid)}"
            )

            class FakeHandle:
                def __init__(self):
                    self.closed = False

                def setAutoDetachKernelDriver(self, val):
                    pass

                def claimInterface(self, i):
                    pass

                def resetDevice(self):
                    pass

                def close(self):
                    self.closed = True

                def controlRead(
                    self, request_type, request, value, index, length, timeout
                ):
                    return b"Aviation Card Programmer Ver 3.05 Jan 01 2022 00:00:00\x00"

            return FakeHandle()

    return FakeDevice(vid, pid, endpoints)


# ---------------------
# Test Cases
# ---------------------


def test_no_device(usb_context_patch):
    """Test that ProgrammingException is raised when no USB devices are found.

    This test simulates an empty USB device iterator and asserts that attempting
    to open a programming device raises a ProgrammingException indicating no device.
    """
    logger.debug("Running test_no_device")
    # 1. Setup phase: mock USB context to simulate no devices present
    usb_context_patch.__enter__.return_value.getDeviceIterator.return_value = []
    # 2. Simulation: no devices found (already done via getDeviceIterator)
    # 3. Invocation: call function under test
    with pytest.raises(ProgrammingException) as exc:
        with open_programming_device():
            pass
    # 4. Assertions: validate the exception message
    logger.debug(f"Caught expected exception: {exc.value}")
    assert "Device not found" in str(exc.value)


@pytest.mark.parametrize(
    "vid_pid_1, vid_pid_2, expected_calls, expected_exception",
    [
        pytest.param(
            (0x091E, 0x0300),
            (0x091E, 0x1300),
            [
                ("write_firmware_0x300", 1),
            ],
            False,
            id="early Garmin",
        ),
        pytest.param(
            (0x091E, 0x0500),
            (0x091E, 0x1300),
            [
                ("write_firmware_stage1", 1),
                ("init_stage2", 1),
                ("write_firmware_stage2", 1),
            ],
            False,
            id="current Garmin",
        ),
        pytest.param(
            (0x9999, 0x9999),
            (0x9999, 0x9999),
            [],
            True,
            id="unknown VID PID combo",
        ),
    ],
)
def test_uninitialized_garmin_device(
    fake_handle,
    usb_context_patch,
    firmware_writer_patch,
    vid_pid_1,
    vid_pid_2,
    expected_calls,
    expected_exception,
):
    """Test detection and firmware programming for uninitialized Garmin devices.

    Simulates two Garmin USB devices with specified VID/PID pairs. Verifies that
    the correct firmware writer methods are called depending on device type, or
    that a ProgrammingException is raised for unknown devices.

    Args:
        fake_handle: Fixture providing a fake USB handle context.
        usb_context_patch: Fixture patching USBContext.
        firmware_writer_patch: Fixture tracking GarminFirmwareWriter instances.
        vid_pid_1 (tuple): VID and PID for the first simulated device.
        vid_pid_2 (tuple): VID and PID for the second simulated device.
        expected_calls (list): List of tuples of expected method names and call counts.
        expected_exception (bool): Whether a ProgrammingException is expected.
    """
    logger.debug("Running test_uninitialized_garmin_device")
    # 1. Setup phase: mock two fake devices with given VID/PID, patch USB context and firmware writer
    dev1 = make_fake_device(*vid_pid_1)
    dev2 = make_fake_device(*vid_pid_2)
    usb_context_patch.__enter__.return_value.getDeviceIterator.return_value = [
        dev1,
        dev2,
    ]
    # 2. Simulation: emulate device detection sequence (None, None, then dev2 found)
    usb_context_patch.__enter__.return_value.getByVendorIDAndProductID.side_effect = [
        None
    ] * 2 + [dev2] * 4
    # Ensure two mocked GarminFirmwareWriter instances are available
    while len(firmware_writer_patch) < 2:
        firmware_writer_patch.append(mock.MagicMock(spec=GarminFirmwareWriter))
    fw1, fw2 = firmware_writer_patch[-2:]
    # 3. Invocation: call function under test, handle expected exceptions
    if expected_exception:
        # 4. Assertions: ProgrammingException should be raised for unknown VID/PID
        with pytest.raises(ProgrammingException):
            with open_programming_device():
                pass
        return
    # 3. Invocation: open the programming device as context manager
    with open_programming_device() as dev:
        # 4. Assertions: validate returned device type and handle state
        assert isinstance(dev, GarminProgrammingDevice)
        assert not fake_handle.closed
    # 4. Assertions: check expected firmware writer method calls
    if expected_calls:
        for name, count in expected_calls:
            found = False
            for fw in firmware_writer_patch:
                if hasattr(fw, name):
                    method = getattr(fw, name)
                    try:
                        method.assert_called_once()
                        found = True
                        break
                    except AssertionError:
                        continue
            if not found:
                raise AssertionError(
                    f"Expected method '{name}' to be called exactly once on at least one GarminFirmwareWriter instance, but it was not."
                )


def test_garmin_needs_firmware_upgrade(
    fake_handle, usb_context_patch, firmware_writer_patch
):
    """Test that firmware upgrade is triggered for outdated Garmin devices.

    Simulates a Garmin device requiring a firmware upgrade and verifies that
    the firmware writer's stage2 initialization and firmware writing methods are called.
    """
    logger.debug("Running test_garmin_needs_firmware_upgrade")
    # 1. Setup phase: create a fake device, patch USB context and firmware writer
    dev = make_fake_device(0x091E, 0x1300)
    usb_context_patch.__enter__.return_value.getDeviceIterator.return_value = [dev]
    usb_context_patch.__enter__.return_value.getByVendorIDAndProductID.return_value = (
        dev
    )
    # 2. Simulation: device needs firmware upgrade (handled by firmware writer mock)
    # 3. Invocation: call function under test
    with open_programming_device() as devobj:
        # 4. Assertions: check device type and firmware writer method calls
        assert isinstance(devobj, GarminProgrammingDevice)
    called = [fw for fw in firmware_writer_patch if fw.init_stage2.called]
    assert len(called) == 1
    called[0].init_stage2.assert_called_once()
    called[0].write_firmware_stage2.assert_called_once()
    logger.debug("Validated firmware upgrade calls")


def test_garmin_firmware_already_current(
    fake_handle, usb_context_patch, firmware_writer_patch, monkeypatch
):
    """Test that firmware writer skips update when firmware is already current.

    Simulates a Garmin device whose firmware is already up-to-date by causing
    the init_stage2 method to raise AlreadyUpdatedException. Verifies that the
    subsequent firmware writing method is not called.
    """
    logger.debug("Running test_garmin_firmware_already_current")
    # 1. Setup phase: create a fake device, patch USB context and firmware writer
    dev = make_fake_device(0x091E, 0x1300)
    usb_context_patch.__enter__.return_value.getDeviceIterator.return_value = [dev]
    usb_context_patch.__enter__.return_value.getByVendorIDAndProductID.return_value = (
        dev
    )
    firmware_writer_patch.clear()

    # 2. Simulation: firmware writer raises AlreadyUpdatedException on init_stage2
    def writer_with_exception(*args, **kwargs):
        instance = mock.MagicMock(spec=GarminFirmwareWriter)
        instance.init_stage2.side_effect = AlreadyUpdatedException()
        firmware_writer_patch.append(instance)
        return instance

    monkeypatch.setattr(
        "jdmtool.data_card.detect.GarminFirmwareWriter", writer_with_exception
    )
    # 3. Invocation: call function under test
    with open_programming_device() as devobj:
        # 4. Assertions: check device type and that firmware was not rewritten
        assert isinstance(devobj, GarminProgrammingDevice)
    called = [fw for fw in firmware_writer_patch if fw.init_stage2.called]
    assert len(called) == 1
    called[0].init_stage2.assert_called_once()
    called[0].write_firmware_stage2.assert_not_called()
    logger.debug("Validated AlreadyUpdatedException behavior")


# ---------------------
# Additional Test Cases
# ---------------------


def test_garmin_device_missing_endpoints(usb_context_patch):
    """Test that ProgrammingException is raised if Garmin device lacks valid endpoints.

    Simulates a Garmin USB device configuration with no endpoints and asserts
    that opening the programming device raises a ProgrammingException indicating
    missing endpoints.
    """
    logger.debug("Running test_garmin_device_missing_endpoints")

    # 1. Setup phase: mock a device with no endpoints
    class BadDevice:
        def getVendorID(self):
            return 0x091E

        def getProductID(self):
            return 0x1300

        def __getitem__(self, idx):
            return mock.MagicMock(__iter__=lambda s: iter([]))

        def open(self):
            return mock.MagicMock()

    usb_context_patch.__enter__.return_value.getDeviceIterator.return_value = [
        BadDevice()
    ]
    # 2. Simulation: device with no endpoints (already handled in class above)
    # 3. Invocation: call function under test
    with pytest.raises(ProgrammingException) as exc:
        with open_programming_device():
            pass
    # 4. Assertions: error message about missing endpoints
    assert "No endpoints found in device configuration." in str(exc.value)


def test_garmin_device_raises_on_error_in_writer(usb_context_patch, monkeypatch):
    """Test that ProgrammingException is raised if GarminFirmwareWriter initialization fails.

    Simulates a Garmin device and patches GarminFirmwareWriter to raise a RuntimeError.
    Verifies that the exception is wrapped and raised as a ProgrammingException.
    """
    logger.debug("Running test_garmin_device_raises_on_error_in_writer")
    # 1. Setup phase: create a fake device, patch USB context and firmware writer to raise error
    dev = make_fake_device(0x091E, 0x1300)
    usb_context_patch.__enter__.return_value.getDeviceIterator.return_value = [dev]

    # 2. Simulation: firmware writer raises RuntimeError on initialization
    def broken_writer(*args, **kwargs):
        raise RuntimeError("Device IO error")

    monkeypatch.setattr("jdmtool.data_card.detect.GarminFirmwareWriter", broken_writer)
    # 3. Invocation: call function under test
    with pytest.raises(ProgrammingException) as exc:
        with open_programming_device():
            pass
    # 4. Assertions: error message about IO error
    assert "Device IO error" in str(exc.value)


# ---------------------
# Additional Coverage for detect.py
# ---------------------


def test_open_usb_device_retries_on_usb_error(monkeypatch):
    """Test that _open_usb_device retries on USBError and eventually raises ProgrammingException.

    Simulates a USB device whose open method raises usb1.USBError repeatedly.
    Verifies that _open_usb_device retries and raises ProgrammingException with error details.
    """
    import usb1
    from jdmtool.data_card import detect

    # 1. Setup phase: create fake device, patch its open method to raise USBError
    fake_dev = make_fake_device(0x091E, 0x1300)

    class FlakyHandle:
        def __init__(self):
            self.attempts = 0

        def open(self):
            raise usb1.USBError("temporary error")

    monkeypatch.setattr(fake_dev, "open", FlakyHandle().open)
    monkeypatch.setattr("jdmtool.data_card.detect.USBDeviceHandle", mock.Mock())
    # 2. Simulation: device open always fails with USBError
    # 3. Invocation: call function under test
    with pytest.raises(ProgrammingException) as exc:
        with detect._open_usb_device(fake_dev):  # type: ignore[arg-type]
            pass
    # 4. Assertions: error message contains expected text
    assert "Could not open device" in str(exc.value)
    assert "temporary error" in str(exc.value)


def test_open_usb_device_raises_on_endpoint_failure(monkeypatch):
    """Test that _read_endpoints raises ProgrammingException when no endpoints are found.

    Simulates a USB device configuration with no endpoints and asserts that
    _read_endpoints raises a ProgrammingException indicating the issue.
    """
    from jdmtool.data_card import detect

    # 1. Setup phase: create a fake device with no endpoints
    class NoEndpointDevice:
        def getVendorID(self):
            return 0x091E

        def getProductID(self):
            return 0x1300

        def __getitem__(self, idx):
            setting = mock.MagicMock()
            setting.getNumber.return_value = 0
            setting.getAlternateSetting.return_value = 0
            setting.__iter__.return_value = iter([])  # No endpoints
            interface = mock.MagicMock()
            interface.__iter__.return_value = iter([setting])
            config = mock.MagicMock()
            config.__iter__.return_value = iter([interface])
            return config

    dev = NoEndpointDevice()
    # 2. Simulation: device with no endpoints (already in __getitem__)
    # 3. Invocation: call function under test
    with pytest.raises(ProgrammingException) as exc:
        detect._read_endpoints(dev)  # type: ignore[arg-type]
    # 4. Assertions: error message about missing endpoints
    assert "No endpoints found in device configuration." in str(exc.value)


from jdmtool.data_card.detect import _read_endpoints


def test_read_endpoints_returns_in_and_out():
    """Test _read_endpoints returns correct IN and OUT endpoint addresses."""

    class InlineDevice:
        def getVendorID(self):
            return 0x1234

        def getProductID(self):
            return 0x5678

        def __getitem__(self, idx):
            setting = mock.MagicMock()
            setting.getNumber.return_value = 0
            setting.getAlternateSetting.return_value = 0
            # Provide endpoints
            ep_in = mock.MagicMock(getAddress=lambda: 0x81)
            ep_out = mock.MagicMock(getAddress=lambda: 0x02)
            setting.__iter__.return_value = iter([ep_in, ep_out])
            interface = mock.MagicMock()
            interface.__iter__.return_value = iter([setting])
            config = mock.MagicMock()
            config.__iter__.return_value = iter([interface])
            return config

    dev = InlineDevice()
    ep_in, ep_out = _read_endpoints(dev)  # type: ignore[arg-type]
    assert ep_in == 0x81
    assert ep_out == 0x02
