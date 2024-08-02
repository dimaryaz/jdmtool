from contextlib import contextmanager
from typing import BinaryIO, Callable, Generator, Iterable

from .util import ProgrammingException


try:
    import usb1
except ImportError:
    raise ProgrammingException("Please install USB support by running `pip3 install jdmtool[usb]") from None


class SkyboundDevice():
    VID = 0x0E39
    PID = 0x1250

    WRITE_ENDPOINT = 0x02
    READ_ENDPOINT = 0x81

    TIMEOUT = 3000

    BLOCK_SIZE = 0x1000
    BLOCKS_PER_PAGE = 0x10
    PAGE_SIZE = BLOCK_SIZE * BLOCKS_PER_PAGE

    MEMORY_OFFSETS = [0x00E0, 0x02E0, 0x0160, 0x0360, 0x01A0, 0x03A0, 0x01C0, 0x03C0]
    PAGES_PER_OFFSET = 0x20

    MEMORY_LAYOUT_UNKNOWN = [0]
    MEMORY_LAYOUT_4MB = [0, 2]
    MEMORY_LAYOUT_16MB = [0, 1, 2, 3, 4, 5, 6, 7]

    def __init__(self, handle: usb1.USBDeviceHandle) -> None:
        self.handle = handle
        self.memory_layout = self.MEMORY_LAYOUT_UNKNOWN

    def set_memory_layout(self, memory_layout: Iterable[int]) -> None:
        self.memory_layout = list(memory_layout)

    def write(self, data: bytes) -> None:
        self.handle.bulkWrite(self.WRITE_ENDPOINT, data, self.TIMEOUT)

    def read(self, length: int) -> bytes:
        return self.handle.bulkRead(self.READ_ENDPOINT, length, self.TIMEOUT)

    def control_read(self, bRequestType: int, bRequest: int, wValue: int, wIndex: int, wLength: int) -> bytes:
        return self.handle.controlRead(bRequestType, bRequest, wValue, wIndex, wLength, self.TIMEOUT)

    def init(self) -> None:
        buf = self.control_read(0x80, 0x06, 0x0100, 0x0000, 18)
        if buf != b"\x12\x01\x10\x01\xFF\x83\xFF\x40\x39\x0E\x50\x12\x00\x00\x00\x00\x00\x01":
            raise ProgrammingException("Unexpected response")
        buf = self.control_read(0x80, 0x06, 0x0200, 0x0000, 9)
        if buf != b"\x09\x02\x20\x00\x01\x01\x00\x80\x0F":
            raise ProgrammingException("Unexpected response")
        buf = self.control_read(0x80, 0x06, 0x0200, 0x0000, 32)
        if buf != (
            b"\x09\x02\x20\x00\x01\x01\x00\x80\x0F\x09\x04\x00\x00\x02\x00\x00"
            b"\x00\x00\x07\x05\x81\x02\x40\x00\x05\x07\x05\x02\x02\x40\x00\x05"
        ):
            raise ProgrammingException("Unexpected response")

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

    def get_total_size(self) -> int:
        return self.get_total_pages() * self.PAGE_SIZE

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



@contextmanager
def open_usb_device(need_data_card: bool = True) -> Generator[SkyboundDevice, None, None]:
    with usb1.USBContext() as usbcontext:
        try:
            usbdev = usbcontext.getByVendorIDAndProductID(SkyboundDevice.VID, SkyboundDevice.PID)
            if usbdev is None:
                raise ProgrammingException("Device not found")

            print(f"Found device: {usbdev}")
            handle = usbdev.open()
        except usb1.USBError as ex:
            raise ProgrammingException(f"Could not open device: {ex}") from ex

        handle.setAutoDetachKernelDriver(True)
        with handle.claimInterface(0):
            handle.resetDevice()
            dev = SkyboundDevice(handle)
            dev.init()

            if need_data_card:
                dev.init_data_card()

            try:
                yield dev
            finally:
                dev.set_led(False)
