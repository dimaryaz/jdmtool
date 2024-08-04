import time

from contextlib import contextmanager
from pathlib import Path
from struct import unpack
from typing import BinaryIO, Callable, Generator, Iterable, Optional, Type, Tuple

from .util import ProgrammingException


try:
    import usb1
except ImportError:
    raise ProgrammingException("Please install USB support by running `pip3 install jdmtool[usb]") from None


FIRMWARE_DIR = Path(__file__).parent / 'firmware'


class BasicUsbDevice():
    WRITE_ENDPOINT: int
    READ_ENDPOINT: int

    TIMEOUT = 3000

    handle: usb1.USBDeviceHandle

    def __init__(self, handle: usb1.USBDeviceHandle) -> None:
        self.handle = handle

    def write(self, data: bytes) -> None:
        self.handle.bulkWrite(self.WRITE_ENDPOINT, data, self.TIMEOUT)

    def read(self, length: int) -> bytes:
        return self.handle.bulkRead(self.READ_ENDPOINT, length, self.TIMEOUT)

    def control_read(self, bRequestType: int, bRequest: int, wValue: int, wIndex: int, wLength: int) -> bytes:
        return self.handle.controlRead(bRequestType, bRequest, wValue, wIndex, wLength, self.TIMEOUT)

    def control_write(self, bRequestType: int, bRequest: int, wValue: int, wIndex: int, data: bytes):
        self.handle.controlWrite(bRequestType, bRequest, wValue, wIndex, data, self.TIMEOUT)

    def validate_read(self, expected: bytes, actual: bytes) -> None:
        if expected != actual:
            raise ProgrammingException("Unexpected response")

    def control_get_device(self, length: int) -> bytes:
        return self.control_read(0x80, 0x06, 0x0100, 0x0000, length)

    def control_get_configuration(self, length: int) -> bytes:
        return self.control_read(0x80, 0x06, 0x0200, 0x0000, length)

    def control_get_string(self, index: int, language_id: int, length: int) -> bytes:
        return self.control_read(0x80, 0x06, 0x0300 + index, language_id, length)

    def control_get_status(self, value: int, index: int, length: int) -> bytes:
        return self.control_read(0x80, 0x00, value, index, length)

    def control_set_configuration(self, value: int, index: int) -> None:
        self.control_write(0x00, 0x09, value, index, b"")

    def control_set_interface(self, alt: int, interface: int) -> None:
        self.control_write(0x01, 0x0B, alt, interface, b"")


class ProgrammingDevice(BasicUsbDevice):
    BLOCK_SIZE = 0x1000
    BLOCKS_PER_PAGE = 0x10
    PAGE_SIZE = BLOCK_SIZE * BLOCKS_PER_PAGE

    def init(self) -> None:
        pass

    def close(self) -> None:
        pass

    def has_card(self) -> bool:
        raise NotImplementedError()

    def init_data_card(self) -> None:
        raise NotImplementedError()

    def get_total_pages(self) -> int:
        raise NotImplementedError()

    def get_total_size(self) -> int:
        return self.get_total_pages() * self.PAGE_SIZE

    def get_version(self) -> str:
        raise NotImplementedError()

    def get_iid(self) -> int:
        raise NotImplementedError()

    def get_unknown(self) -> int:
        raise NotImplementedError()

    def read_database(self, pages: int, fd: BinaryIO, progress_cb: Callable[[int], None]) -> None:
        raise NotImplementedError()

    def erase_database(self, pages: int, progress_cb: Callable[[int], None]) -> None:
        raise NotImplementedError()

    def write_database(self, pages: int, fd: BinaryIO, progress_cb: Callable[[int], None]) -> None:
        raise NotImplementedError()

    def verify_database(self, pages: int, fd: BinaryIO, progress_cb: Callable[[int], None]) -> None:
        raise NotImplementedError()


class SkyboundDevice(ProgrammingDevice):
    WRITE_ENDPOINT = 0x02
    READ_ENDPOINT = 0x81

    MEMORY_OFFSETS = [0x00E0, 0x02E0, 0x0160, 0x0360, 0x01A0, 0x03A0, 0x01C0, 0x03C0]
    PAGES_PER_OFFSET = 0x20

    MEMORY_LAYOUT_UNKNOWN = [0]
    MEMORY_LAYOUT_4MB = [0, 2]
    MEMORY_LAYOUT_16MB = [0, 1, 2, 3, 4, 5, 6, 7]

    def __init__(self, handle: usb1.USBDeviceHandle) -> None:
        super().__init__(handle)
        self.memory_layout = self.MEMORY_LAYOUT_UNKNOWN

    def set_memory_layout(self, memory_layout: Iterable[int]) -> None:
        self.memory_layout = list(memory_layout)

    def init(self) -> None:
        super().init()

        buf = self.control_get_device(18)
        self.validate_read(
            b"\x12\x01\x10\x01\xFF\x83\xFF\x40\x39\x0E\x50\x12\x00\x00\x00\x00\x00\x01", buf
        )

        buf = self.control_get_configuration(9)
        self.validate_read(b"\x09\x02\x20\x00\x01\x01\x00\x80\x0F", buf)

        buf = self.control_get_configuration(32)
        self.validate_read(
            b"\x09\x02\x20\x00\x01\x01\x00\x80\x0F\x09\x04\x00\x00\x02\x00\x00"
            b"\x00\x00\x07\x05\x81\x02\x40\x00\x05\x07\x05\x02\x02\x40\x00\x05", buf)

    def close(self) -> None:
        super().close()
        self.set_led(False)

    def set_led(self, on: bool) -> None:
        if on:
            self.write(b'\x12')
        else:
            self.write(b'\x13')

    def has_card(self) -> bool:
        self.write(b"\x18")
        buf = self.read(0x0040)
        if buf == b"\x00":
            return True
        elif buf == b"\x01":
            return False
        else:
            raise ProgrammingException(f"Unexpected response: {buf}")

    def get_version(self) -> str:
        self.write(b"\x60")
        return self.read(0x0040).decode()

    def get_unknown(self) -> int: # TODO: what is this?
        self.select_page(0)
        self.before_read()
        self.write(b"\x50\x03")
        buf = self.read(0x0040)
        return int.from_bytes(buf, 'little')

    def get_iid(self) -> int:
        self.select_page(0)
        self.before_read()
        self.write(b"\x50\x04")
        buf = self.read(0x0040)
        return int.from_bytes(buf, 'little')

    def read_block(self) -> bytes:
        self.write(b"\x28")
        return self.read(0x1000)

    def write_block(self, data: bytes) -> None:
        if len(data) != 0x1000:
            raise ValueError("Data must be 4096 bytes")

        self.write(b"\x2A\x04")
        self.write(data)

        buf = self.read(0x0040)
        if buf[0] != data[-1] or buf[1:] != b"\x00\x00\x00":
            raise ProgrammingException(f"Unexpected response: {buf}")

    def select_physical_page(self, page_id: int) -> None:
        if not (0x0000 <= page_id <= 0xFFFF):
            raise ValueError("Invalid page ID")
        self.write(b"\x30\x00\x00" + page_id.to_bytes(2, 'little'))

    def translate_page(self, page_id: int) -> int:
        offset_id = self.memory_layout[page_id // self.PAGES_PER_OFFSET]
        return self.MEMORY_OFFSETS[offset_id] + page_id % self.PAGES_PER_OFFSET

    def select_page(self, page_id: int) -> None:
        self.select_physical_page(self.translate_page(page_id))

    def erase_page(self) -> None:
        self.write(b"\x52\x04")
        buf = self.read(0x0040)
        if buf != b"\x04":
            raise ProgrammingException(f"Unexpected response: {buf}")

    def before_read(self) -> None:
        # It's not clear that this does anything, but JDM seems to send it
        # before reading anything, so do the same thing.
        self.write(b"\x40")

    def before_write(self) -> None:
        # Same as above.
        self.write(b"\x42")

    def get_total_pages(self) -> int:
        return len(self.memory_layout) * self.PAGES_PER_OFFSET

    def init_data_card(self) -> None:
        if not self.has_card():
            raise ProgrammingException("Card is missing!")

        self.select_page(0)
        self.before_read()

        # TODO: Figure out the actual meaning of the iid and the "unknown" value.
        iid = self.get_iid()
        if iid == 0x01004100:
            # 16MB WAAS card
            print("Detected data card: 16MB WAAS")
            self.set_memory_layout(SkyboundDevice.MEMORY_LAYOUT_16MB)
        elif iid == 0x0100ad00:
            # 4MB non-WAAS card
            print("Detected data card: 4MB non-WAAS")
            self.set_memory_layout(SkyboundDevice.MEMORY_LAYOUT_4MB)
        else:
            raise ProgrammingException(
                f"Unknown data card IID: 0x{iid:08x} (possibly 8MB non-WAAS?). Please file a bug!"
            )

    def _loop_helper(self, i: int) -> None:
        self.set_led(i % 2 == 0)
        if not self.has_card():
            raise ProgrammingException("Data card has disappeared!")

    def read_database(self, pages: int, fd: BinaryIO, progress_cb: Callable[[int], None]) -> None:
        self.before_read()
        for i in range(pages * SkyboundDevice.BLOCKS_PER_PAGE):
            self._loop_helper(i)

            if i % SkyboundDevice.BLOCKS_PER_PAGE == 0:
                self.select_page(i // SkyboundDevice.BLOCKS_PER_PAGE)

            block = self.read_block()

            if block == b'\xFF' * SkyboundDevice.BLOCK_SIZE:
                break

            fd.write(block)
            progress_cb(len(block))


    def erase_database(self, pages: int, progress_cb: Callable[[int], None]) -> None:
        self.before_write()
        for i in range(pages):
            self._loop_helper(i)
            self.select_page(i)
            self.erase_page()
            progress_cb(SkyboundDevice.PAGE_SIZE)


    def write_database(self, pages: int, fd: BinaryIO, progress_cb: Callable[[int], None]) -> None:
        self.before_write()
        for i in range(pages * SkyboundDevice.BLOCKS_PER_PAGE):
            block = fd.read(SkyboundDevice.BLOCK_SIZE).ljust(SkyboundDevice.BLOCK_SIZE, b'\xFF')

            self._loop_helper(i)

            if i % SkyboundDevice.BLOCKS_PER_PAGE == 0:
                self.select_page(i // SkyboundDevice.BLOCKS_PER_PAGE)

            self.write_block(block)
            progress_cb(len(block))


    def verify_database(self, pages: int, fd: BinaryIO, progress_cb: Callable[[int], None]) -> None:
        self.before_read()
        for i in range(pages * SkyboundDevice.BLOCKS_PER_PAGE):
            file_block = fd.read(SkyboundDevice.BLOCK_SIZE).ljust(SkyboundDevice.BLOCK_SIZE, b'\xFF')

            self._loop_helper(i)

            if i % SkyboundDevice.BLOCKS_PER_PAGE == 0:
                self.select_page(i // SkyboundDevice.BLOCKS_PER_PAGE)

            card_block = self.read_block()

            if card_block != file_block:
                raise ProgrammingException(f"Verification failed! Block {i} is incorrect.")

            progress_cb(len(file_block))


class GarminFirmwareWriter(BasicUsbDevice):
    WRITE_ENDPOINT = -1
    READ_ENDPOINT = -1

    def init_stage1(self) -> None:
        buf = self.control_get_device(18)
        self.validate_read(
                b"\x12\x01\x00\x02\xFF\xFF\xFF\x40\x1E\x09\x00\x05\x00\x00\x00\x00\x00\x01", buf)

        buf = self.control_get_configuration(9)
        self.validate_read(b"\x09\x02\xAB\x00\x01\x01\x00\x80\x32", buf)

        buf = self.control_get_configuration(171)
        self.validate_read(
                b"\x09\x02\xAB\x00\x01\x01\x00\x80\x32\x09\x04\x00\x00\x00\xFF\xFF"
                b"\xFF\x00\x09\x04\x00\x01\x06\xFF\xFF\xFF\x00\x07\x05\x01\x02\x00"
                b"\x02\x00\x07\x05\x81\x02\x00\x02\x00\x07\x05\x02\x02\x00\x02\x00"
                b"\x07\x05\x04\x02\x00\x02\x00\x07\x05\x86\x02\x00\x02\x00\x07\x05"
                b"\x88\x02\x00\x02\x00\x09\x04\x00\x02\x06\xFF\xFF\xFF\x00\x07\x05"
                b"\x01\x03\x40\x00\x01\x07\x05\x81\x03\x40\x00\x01\x07\x05\x02\x03"
                b"\x00\x02\x01\x07\x05\x04\x02\x00\x02\x00\x07\x05\x86\x03\x00\x02"
                b"\x01\x07\x05\x88\x02\x00\x02\x00\x09\x04\x00\x03\x06\xFF\xFF\xFF"
                b"\x00\x07\x05\x01\x03\x40\x00\x01\x07\x05\x81\x03\x40\x00\x01\x07"
                b"\x05\x02\x01\x00\x02\x01\x07\x05\x04\x02\x00\x02\x00\x07\x05\x86"
                b"\x01\x00\x02\x01\x07\x05\x88\x02\x00\x02\x00", buf)

        self.control_set_configuration(0x0001, 0x0000)
        self.control_set_interface(0x0000, 0x0000)

    def write_firmware_stage1(self) -> None:
        with open(FIRMWARE_DIR / 'grmn0500.dat', 'rb') as fd:
            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x01")
            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x01")

            fd.seek(0x29a0)
            self.write_firmware(fd)

            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x00")
            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x01")

            fd.seek(0x0000)
            self.write_firmware(fd)

            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x01")
            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x00")

    def init_stage2(self) -> None:
        buf = self.control_get_device(18)
        self.validate_read(
                b"\x12\x01\x00\x02\xFF\xFF\xFF\x40\x1E\x09\x00\x13\x01\x00\x01\x02\x00\x01", buf)

        buf = self.control_get_configuration(9)
        self.validate_read(b"\x09\x02\x20\x00\x01\x01\x00\x80\x32", buf)

        buf = self.control_get_configuration(32)
        self.validate_read(
                b"\x09\x02\x20\x00\x01\x01\x00\x80\x32\x09\x04\x00\x00\x02\xFF\xFF"
                b"\xFF\x00\x07\x05\x86\x02\x40\x00\x00\x07\x05\x02\x02\x40\x00\x00", buf)

        self.control_set_configuration(0x0001, 0x0000)

        buf = self.control_get_device(64)
        self.validate_read(b"\x12\x01\x00\x02\xFF\xFF\xFF\x40\x1E\x09\x00\x13\x01\x00\x01\x02\x00\x01", buf)

        buf = self.control_get_configuration(255)
        self.validate_read(
                b"\x09\x02\x20\x00\x01\x01\x00\x80\x32\x09\x04\x00\x00\x02\xFF\xFF"
                b"\xFF\x00\x07\x05\x86\x02\x40\x00\x00\x07\x05\x02\x02\x40\x00\x00", buf)

        buf = self.control_get_status(0x0000, 0x0000, 2)
        self.validate_read(b"\x02\x00", buf)

        buf = self.control_get_string(0, 0x0000, 255)
        self.validate_read(b"\x04\x03\x09\x04", buf)

        buf = self.control_get_string(1, 0x0409, 255)
        self.validate_read(b"\x1A\x03" + "GARMIN Corp.".encode("utf-16le"), buf)

        buf = self.control_get_string(2, 0x0409, 255)
        self.validate_read(b"\x32\x03" + "USB Data Card Programmer".encode("utf-16le"), buf)

        buf = self.control_read(0xC0, 0x8A, 0x0000, 0x0000, 512)
        self.validate_read(b'Aviation Card Programmer Ver 3.02 Aug 10 2015 13:21:51\x00', buf)

        buf = self.control_read(0xC0, 0xD2, 0x0000, 0x0000, 512)
        self.validate_read(b"\xC0\x1E\x09\x00\x05\x00\x00\x00\xCF\x39\x87\x49\xFF\xFF\xFF\xFF", buf)

    def write_firmware_stage2(self) -> None:
        with open(FIRMWARE_DIR / 'grmn1300.dat', 'rb') as fd:
            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x01")

            self.write_firmware(fd)

            self.control_write(0x40, 0xA0, 0xE600, 0x0000, b"\x00")

    def write_firmware(self, fd: BinaryIO) -> None:
        while True:
            data_len, addr, buf = unpack('<HHx16sx', fd.read(0x16))
            if not data_len:
                break

            self.control_write(0x40, 0xA0, addr, 0x0000, buf[:data_len])


@contextmanager
def _open_usb_device(usbdev: usb1.USBDevice):
    try:
        handle = usbdev.open()
    except usb1.USBError as ex:
        raise ProgrammingException(f"Could not open device: {ex}") from ex

    handle.setAutoDetachKernelDriver(True)
    with handle.claimInterface(0):
        handle.resetDevice()
        yield handle

    handle.close()


SKYBOUND_VID_PID = (0x0E39, 0x1250)
GARMIN_UNINITIALIZED_VID_PID = (0x091E, 0x0500)
GARMIN_VID_PID = (0x091E, 0x1300)


@contextmanager
def open_programming_device(need_data_card: bool = True) -> Generator[SkyboundDevice, None, None]:
    with usb1.USBContext() as usbcontext:
        dev_cls: Optional[Type[ProgrammingDevice]] = None
        for usbdev in usbcontext.getDeviceIterator():
            vid_pid: Tuple[int, int] = (usbdev.getVendorID(), usbdev.getProductID())
            if vid_pid == SKYBOUND_VID_PID:
                print(f"Found a Skybound device at {usbdev}")
                dev_cls = SkyboundDevice
                break

            elif vid_pid == GARMIN_UNINITIALIZED_VID_PID:
                print(f"Found an un-initialized Garmin device at {usbdev}")
                print("Writing stage 1 firmware...")

                with _open_usb_device(usbdev) as handle:
                    GarminFirmwareWriter(handle).init_stage1()
                    GarminFirmwareWriter(handle).write_firmware_stage1()

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
                    GarminFirmwareWriter(handle).init_stage2()
                    GarminFirmwareWriter(handle).write_firmware_stage2()

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

                raise ProgrammingException("TODO: Implement GarminProgrammingDevice")
                break

            elif vid_pid == GARMIN_VID_PID:
                print(f"Found a Garmin device at {usbdev}")

                # TODO: Check if this is needed?
                print("Writing stage 2 firmware...")
                with _open_usb_device(usbdev) as handle:
                    GarminFirmwareWriter(handle).init_stage2()
                    GarminFirmwareWriter(handle).write_firmware_stage2()

                print("Re-scanning devices...")
                for _ in range(5):
                    time.sleep(0.5)
                    new_usbdev = usbcontext.getByVendorIDAndProductID(GARMIN_VID_PID[0], GARMIN_VID_PID[1])
                    if new_usbdev is not None:
                        print(f"Found at {new_usbdev}")
                        usbdev = new_usbdev
                        break

                raise ProgrammingException("TODO: Implement GarminProgrammingDevice")
                break
        else:
            raise ProgrammingException("Device not found")

        with _open_usb_device(usbdev) as handle:
            dev = dev_cls(handle)
            dev.init()

            if need_data_card:
                dev.init_data_card()

            try:
                yield dev
            finally:
                dev.close()
