from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import time

from .common import ProgrammingDevice, ProgrammingException
from .garmin import AlreadyUpdatedException, GarminFirmwareWriter, GarminProgrammingDevice
from .skybound import SkyboundDevice

try:
    from usb1 import USBContext, USBDevice, USBDeviceHandle, USBError
except ImportError:
    raise ProgrammingException("Please install USB support by running `pip3 install jdmtool[usb]") from None

SKYBOUND_VID_PID = (0x0E39, 0x1250)
GARMIN_UNINITIALIZED_VID_PID = {
    (0x091E, 0x0500), # "current" 010-10579-20 
    (0x091E, 0x0300), # "early" 011-01277-00
    (0x04B4, 0x8613), # Cypress EZ-USB FX2 (if EEPROM is reset)
}
GARMIN_VID_PID = (0x091E, 0x1300)

@contextmanager
def _open_usb_device(usbdev: USBDevice) -> Generator[USBDeviceHandle, None, None]:
    # Open a USB device handle as a context manager. Retries up to 3 times on USBError.
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
                time.sleep(.5)

        yield handle

    finally:
        if handle is not None:
            handle.close() # pyright: ignore[reportCallIssue]


def _read_endpoints(usbdev: USBDevice) -> tuple[int, int]:
    # Scans the device's first configuration to list all endpoint addresses, then selects
    # the first IN (0x8X) and first OUT (0x0X) endpoints. 
    # Bit 7 (MSB) of the endpoint address defines direction, Bits 0–3 define the endpoint number (max 15).

    config = usbdev[0]
    endpoints = []
    for interface in config:
        for setting in interface:
            num = setting.getNumber()
            alt = setting.getAlternateSetting()
            for endpoint in setting:
                addr = endpoint.getAddress()
                endpoints.append((num, alt, addr))
    in_eps = [addr for (_, _, addr) in endpoints if (addr & 0xF0) == 0x80]
    out_eps = [addr for (_, _, addr) in endpoints if (addr & 0xF0) == 0x00]
    read_ep = int(in_eps[0])
    write_ep = int(out_eps[0])
    return read_ep, write_ep


def _rescan(usbcontext: USBContext, vid_pid: tuple[int, int]) -> USBDevice:
    # Rescan a USB device matching vid_pid. Try up to 5 times to find the device.
    for _ in range(20):
        time.sleep(0.2) # wait for interface
        usbdev = usbcontext.getByVendorIDAndProductID(vid_pid[0], vid_pid[1])
        if usbdev is not None:
            return usbdev
    raise ProgrammingException("Could not find the new device!")


@contextmanager
def open_programming_device() -> Generator[ProgrammingDevice, None, None]:
    # Searches for supported devices (Skybound or Garmin), performs necessary firmware flashing
    # for Garmin models, and opens the final programming device handle with detected endpoints.

    with USBContext() as usbcontext:
        dev_cls: type[ProgrammingDevice] | None = None
        read_ep: int | None = None
        write_ep: int | None = None

        for usbdev in usbcontext.getDeviceIterator():
            vid_pid = (usbdev.getVendorID(), usbdev.getProductID())

            if vid_pid == SKYBOUND_VID_PID:
                print(f"Found a Skybound device at {usbdev}")
                dev_cls = SkyboundDevice
                break

            elif vid_pid in GARMIN_UNINITIALIZED_VID_PID:
                print(f"Found an un-initialized Garmin device at {usbdev}")

                with _open_usb_device(usbdev) as handle:
                    if vid_pid[1] == 0x0300: # early model
                        writer = GarminFirmwareWriter(handle)
                        print("Configuring early Garmin programmer (0x0300)")
                        writer.write_firmware_0x300()
                        usbdev = _rescan(usbcontext, GARMIN_VID_PID)

                    else: # current model
                        writer = GarminFirmwareWriter(handle)
                        print("Configuring Garmin programmer")
                        # write stage 1
                        writer.write_firmware_stage1()
                        # get new handle (and endpoints we won't use)
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
                print(f"Found a Garmin device at {usbdev}")

                # First, update stage-2 firmware if possible
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
