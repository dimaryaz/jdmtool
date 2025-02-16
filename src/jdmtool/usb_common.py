from __future__ import annotations

from contextlib import contextmanager
import time
from typing import TYPE_CHECKING

from .common import JdmToolException

if TYPE_CHECKING:
    from usb1 import USBDevice, USBDeviceHandle


class ProgrammingException(JdmToolException):
    pass


class BasicUsbDevice():
    WRITE_ENDPOINT: int
    READ_ENDPOINT: int

    TIMEOUT = 5000

    handle: USBDeviceHandle

    def __init__(self, handle: USBDeviceHandle) -> None:
        self.handle = handle

    def bulk_read(self, length: int) -> bytes:
        return self.handle.bulkRead(self.READ_ENDPOINT, length, self.TIMEOUT)

    def bulk_write(self, data: bytes) -> None:
        self.handle.bulkWrite(self.WRITE_ENDPOINT, data, self.TIMEOUT)

    def control_read(self, bRequestType: int, bRequest: int, wValue: int, wIndex: int, wLength: int) -> bytes:
        return self.handle.controlRead(bRequestType, bRequest, wValue, wIndex, wLength, self.TIMEOUT)

    def control_write(self, bRequestType: int, bRequest: int, wValue: int, wIndex: int, data: bytes) -> None:
        self.handle.controlWrite(bRequestType, bRequest, wValue, wIndex, data, self.TIMEOUT)


@contextmanager
def open_usb_device(usbdev: USBDevice):
    from usb1 import USBError

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
