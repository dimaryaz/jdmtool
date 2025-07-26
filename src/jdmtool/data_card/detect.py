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


@contextmanager
def _open_usb_device(usbdev: USBDevice):
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
                if retry == 3:
                    raise ProgrammingException(f"Could not open device: {ex}") from ex
                time.sleep(.5)

        yield handle

    finally:
        if handle is not None:
            handle.close()


SKYBOUND_VID_PID = (0x0E39, 0x1250)
GARMIN_UNINITIALIZED_VID_PID = {
    (0x091E, 0x0500), # "current" 010-10579-20 
    (0x091E, 0x0300), # "early" 011-01277-00
    (0x04B4, 0x8613), # Cypress EZ-USB FX2 (if EEPROM is reset)
}
GARMIN_VID_PID = (0x091E, 0x1300)

GARMIN_EARLY_READ_ENDPOINT = 0x82
GARMIN_CURRENT_READ_ENDPOINT = 0x86
GARMIN_WRITE_ENDPOINT = 0x02

@contextmanager
def open_programming_device() -> Generator[ProgrammingDevice, None, None]:
    with USBContext() as usbcontext:
        dev_cls: type[ProgrammingDevice] | None = None
        read_ep: int | None = None
        write_ep: int | None = None
        for usbdev in usbcontext.getDeviceIterator():
            vid = usbdev.getVendorID()
            pid = usbdev.getProductID()
            vid_pid: tuple[int, int] = (vid, pid)

            if vid_pid == SKYBOUND_VID_PID:
                print(f"Found a Skybound device at {usbdev}")
                dev_cls = SkyboundDevice
                break

            elif vid_pid in GARMIN_UNINITIALIZED_VID_PID:
                print(f"Found an un-initialized Garmin device at {usbdev}")

                with _open_usb_device(usbdev) as handle:
                    if pid == 0x0300: # early model
                        writer = GarminFirmwareWriter(handle, GARMIN_EARLY_READ_ENDPOINT, GARMIN_WRITE_ENDPOINT)
                        print("Configuring an early GARMIN programmer (0x0300)")
                        writer.write_firmware_0x300()

                    else: # current model
                        writer = GarminFirmwareWriter(handle, GARMIN_CURRENT_READ_ENDPOINT, GARMIN_WRITE_ENDPOINT)
                        print("Configuring GARMIN programmer")
                        # write stage 1
                        writer.write_firmware_stage1()
                        print("Re-scanning devices...")
                        for _ in range(5):
                            time.sleep(0.5)
                            new_usbdev = usbcontext.getByVendorIDAndProductID(GARMIN_VID_PID[0], GARMIN_VID_PID[1])
                            if new_usbdev is not None:
                                print(f"Found at {new_usbdev}")
                                usbdev = new_usbdev
                                break
                        else:
                            raise ProgrammingException("Could not find the new device!")

                        # check version and write stage 2 if required
                        with _open_usb_device(usbdev) as handle:
                            writer.init_stage2()
                            writer.write_firmware_stage2()

                print("Re-scanning devices...")
                for _ in range(5):
                    time.sleep(0.5)
                    new_usbdev = usbcontext.getByVendorIDAndProductID(GARMIN_VID_PID[0], GARMIN_VID_PID[1])
                    if new_usbdev is not None:
                        print(f"Found at {new_usbdev}")
                        usbdev = new_usbdev
                        break
                else:
                    raise ProgrammingException("Could not find the new device!")

                dev_cls = GarminProgrammingDevice
                break

            elif vid_pid == GARMIN_VID_PID:
                print(f"Found a Garmin device at {usbdev}")

                try: # updating the firmware for current device only
                    with _open_usb_device(usbdev) as handle:
                        writer = GarminFirmwareWriter(handle, GARMIN_CURRENT_READ_ENDPOINT, GARMIN_WRITE_ENDPOINT)
                        writer.init_stage2()
                        print("Writing stage 2 firmware...")
                        writer.write_firmware_stage2()
                except AlreadyUpdatedException:
                    pass
                else:
                    print("Re-scanning devices...")
                    for _ in range(5):
                        time.sleep(0.5)
                        new_usbdev = usbcontext.getByVendorIDAndProductID(GARMIN_VID_PID[0], GARMIN_VID_PID[1])
                        if new_usbdev is not None:
                            print(f"Found at {new_usbdev}")
                            usbdev = new_usbdev
                            break

                dev_cls = GarminProgrammingDevice
                break
        else:
            raise ProgrammingException("Device not found")

        with _open_usb_device(usbdev) as handle:
            # Get and list endpoints
            # `0x8X` = IN endpoints, `0x0X` = OUT endpoints
            # Bit 7 (MSB)of the endpoint address defines direction
            # Bits 0–3 define the endpoint number (max 15).
            config = usbdev[0]
            endpoints = []
            for interface in config:
                for setting in interface:
                    num = setting.getNumber()
                    alt = setting.getAlternateSetting()
                    for endpoint in setting:
                        addr = endpoint.getAddress()
                        # print(f"Interface {num}, Alt {alt}, Endpoint address: 0x{addr:02X}")
                        endpoints.append((num, alt, addr))
            # Determine read/write endpoints (IN endpoints start with 0x8_, OUT with 0x0_)
            in_eps = [addr for (_, _, addr) in endpoints if (addr & 0xF0) == 0x80]
            out_eps = [addr for (_, _, addr) in endpoints if (addr & 0xF0) == 0x00]
            # Select first IN and OUT endpoints
            read_ep = int(in_eps[0])
            write_ep = int(out_eps[0])
            dev = dev_cls(handle, read_endpoint=read_ep, write_endpoint=write_ep)
            dev.init()

            try:
                yield dev
            finally:
                dev.close()
