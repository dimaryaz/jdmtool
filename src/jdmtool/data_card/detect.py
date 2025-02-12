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
GARMIN_UNINITIALIZED_VID_PID = (0x091E, 0x0500)
GARMIN_VID_PID = (0x091E, 0x1300)
# If you reset the Garmin Programmer's EEPROM, you end up with a Cypress EZ-USB FX2
FX2_VID_PID = (0x04B4, 0x8613)


@contextmanager
def open_programming_device() -> Generator[ProgrammingDevice, None, None]:
    with USBContext() as usbcontext:
        dev_cls: type[ProgrammingDevice] | None = None
        for usbdev in usbcontext.getDeviceIterator():
            vid_pid: tuple[int, int] = (usbdev.getVendorID(), usbdev.getProductID())
            if vid_pid == SKYBOUND_VID_PID:
                print(f"Found a Skybound device at {usbdev}")
                dev_cls = SkyboundDevice
                break

            elif vid_pid in (GARMIN_UNINITIALIZED_VID_PID, FX2_VID_PID):
                print(f"Found an un-initialized Garmin device at {usbdev}")
                print("Writing stage 1 firmware...")

                with _open_usb_device(usbdev) as handle:
                    writer = GarminFirmwareWriter(handle)
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

                print("Writing stage 2 firmware...")
                with _open_usb_device(usbdev) as handle:
                    writer = GarminFirmwareWriter(handle)
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

                try:
                    with _open_usb_device(usbdev) as handle:
                        writer = GarminFirmwareWriter(handle)
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
            dev = dev_cls(handle)
            dev.init()

            try:
                yield dev
            finally:
                dev.close()
