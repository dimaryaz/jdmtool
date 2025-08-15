"""Device detection and initialization logic for Garmin and Skybound programming tools.

Includes USB device discovery, firmware staging, and endpoint extraction.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import time
import logging

from .common import ProgrammingDevice, ProgrammingException
from .garmin import (
    AlreadyUpdatedException,
    GarminFirmwareWriter,
    GarminProgrammingDevice,
)
from .skybound import SkyboundDevice

logger = logging.getLogger(__name__)

try:
    from usb1 import USBContext, USBDevice, USBDeviceHandle, USBError
except ImportError:
    raise ProgrammingException(
        "Please install USB support by running `pip3 install jdmtool[usb]"
    ) from None

SKYBOUND_VID_PID = (0x0E39, 0x1250)
GARMIN_VID_PID = (0x091E, 0x1300)

GARMIN_UNINITIALIZED_VID_PID = {
    (0x091E, 0x0500),  # "current" 010-10579-20
    (0x091E, 0x0300),  # "early" 011-01277-00
    (0x04B4, 0x8613),  # Cypress EZ-USB FX2 (if EEPROM is reset)
}


@contextmanager
def _open_usb_device(usbdev: USBDevice) -> Generator[USBDeviceHandle, None, None]:
    """Open a USB device handle as a context manager with automatic retry.

    Tries to open the device up to three times in case of USBError, claiming interface 0
    and resetting the device before yielding the handle.

    Args:
        usbdev (USBDevice): The USB device to open.

    Yields:
        USBDeviceHandle: The open handle to the USB device.

    Raises:
        ProgrammingException: If the device cannot be opened after 3 retries.
    @public"""

    handle: USBDeviceHandle | None = None

    try:
        retry = 0
        while True:
            try:
                handle = usbdev.open()

                try:
                    handle.setAutoDetachKernelDriver(True)
                except USBError:
                    # Safe to ignore if it's not supported.
                    pass

                handle.claimInterface(0)
                handle.resetDevice()

                break
            except USBError as ex:
                retry += 1
                if retry >= 3:
                    raise ProgrammingException(f"Could not open device: {ex}") from ex
                time.sleep(0.5)

        yield handle

    finally:
        if handle is not None:
            handle.close()  # pyright: ignore[reportCallIssue]


def _read_endpoints(usbdev: USBDevice) -> tuple[int, int]:
    """Extract the first IN and OUT endpoint addresses from a USB device.

    Parses the first configuration and its interface settings to collect endpoint addresses.
    The first endpoint with IN direction (0x8X) and the first with OUT direction (0x0X) are returned.

    Args:
        usbdev (USBDevice): The USB device to inspect.

    Returns:
        tuple[int, int]: A tuple containing the IN and OUT endpoint addresses.

    Raises:
        ProgrammingException: If no IN or OUT endpoints are found.
    @public"""

    config = usbdev[0]
    endpoints = []
    for interface in config:
        for setting in interface:
            try:
                num = setting.getNumber()
                alt = setting.getAlternateSetting()
                for endpoint in setting:
                    addr = endpoint.getAddress()
                    endpoints.append((num, alt, addr))
            except Exception:
                raise ProgrammingException("Failed to read endpoints")
    if not endpoints:
        raise ProgrammingException("No endpoints found in device configuration.")
    in_eps = [addr for (_, _, addr) in endpoints if (addr & 0xF0) == 0x80]
    out_eps = [addr for (_, _, addr) in endpoints if (addr & 0xF0) == 0x00]
    if not in_eps or not out_eps:
        raise ProgrammingException("No suitable device endpoints found.")
    read_ep = int(in_eps[0])
    write_ep = int(out_eps[0])
    return read_ep, write_ep


def _rescan(usbcontext: USBContext, vid_pid: tuple[int, int]) -> USBDevice:
    """Locate a USB device by vendor/product ID.

    Polls the USB context up to 20 times with short delays to find the device.

    Args:
        usbcontext (USBContext): The USB context used for scanning.
        vid_pid (tuple[int, int]): Vendor ID and Product ID tuple to match.

    Returns:
        USBDevice: The found USB device.

    Raises:
        ProgrammingException: If the device cannot be found after polling.
    @public"""

    for _ in range(20):
        time.sleep(0.2)  # wait for interface
        usbdev = usbcontext.getByVendorIDAndProductID(vid_pid[0], vid_pid[1])
        if usbdev is not None:
            return usbdev
    raise ProgrammingException("Could not find the new device!")


@contextmanager
def open_programming_device() -> Generator[ProgrammingDevice, None, None]:
    """
    Discover, initialize, and yield a programming device context.

    Searches for supported devices (Skybound or Garmin). For uninitialized Garmin devices,
    performs necessary firmware staging and upgrades before yielding an operational device.

    Yields:
        ProgrammingDevice: A ready-to-use programming device instance.

    Raises:
        ProgrammingException: If no supported device is found or initialization fails.
    """

    with USBContext() as usbcontext:
        dev_cls: type[ProgrammingDevice] | None = None
        read_ep: int | None = None
        write_ep: int | None = None

        for usbdev in usbcontext.getDeviceIterator():
            vid_pid = (usbdev.getVendorID(), usbdev.getProductID())

            if vid_pid == SKYBOUND_VID_PID:
                logger.info("Found a Skybound device at %s", usbdev)
                dev_cls = SkyboundDevice
                break

            elif vid_pid in GARMIN_UNINITIALIZED_VID_PID:
                logger.info("Found an un-initialized Garmin device at %s", usbdev)

                with _open_usb_device(usbdev) as handle:
                    if vid_pid[1] == 0x0300:  # early model
                        writer = GarminFirmwareWriter(handle)
                        logger.info("Configuring early Garmin programmer (0x0300)")
                        writer.write_firmware_0x300()
                        usbdev = _rescan(usbcontext, GARMIN_VID_PID)

                    else:  # current model
                        writer = GarminFirmwareWriter(handle)
                        logger.info("Configuring Garmin programmer")
                        # write stage 1
                        writer.write_firmware_stage1()
                        # get new handle
                        usbdev = _rescan(usbcontext, GARMIN_VID_PID)
                        # check version and write stage 2 if required
                        try:
                            with _open_usb_device(usbdev) as handle:
                                writer = GarminFirmwareWriter(handle)
                                writer.init_stage2()
                                writer.write_firmware_stage2()
                        except AlreadyUpdatedException:
                            pass
                        else:
                            usbdev = _rescan(usbcontext, GARMIN_VID_PID)

                dev_cls = GarminProgrammingDevice
                break

            elif vid_pid == GARMIN_VID_PID:
                logger.info("Found a Garmin device at %s", usbdev)

                with _open_usb_device(usbdev) as handle:
                    try:  # to update stage-2 firmware if possible
                        writer = GarminFirmwareWriter(handle)
                        writer.init_stage2()
                        writer.write_firmware_stage2()
                    except AlreadyUpdatedException:
                        pass
                    except Exception as e:
                        raise ProgrammingException(str(e)) from e
                    else:
                        usbdev = _rescan(usbcontext, GARMIN_VID_PID)

                dev_cls = GarminProgrammingDevice
                break
        else:
            raise ProgrammingException("Device not found")

        # reader identified, firmware written, now get endpoints and set the programming device
        read_ep, write_ep = _read_endpoints(usbdev)
        with _open_usb_device(usbdev) as handle:
            dev = dev_cls(handle, read_endpoint=read_ep, write_endpoint=write_ep)
            dev.init()

            try:
                yield dev
            finally:
                dev.close()
