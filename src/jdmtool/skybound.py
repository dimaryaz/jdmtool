from typing import TYPE_CHECKING, Any, Iterable
from dataclasses import dataclass

if TYPE_CHECKING:
    from usb1 import USBDeviceHandle


class SkyboundException(Exception):
    pass

@dataclass
class MemoryCard:
    type: str
    iid: int
    memory_layout: list[int]

    def __repr__(self) -> str:
        return f"iid: {self.iid}, type: {self.type}"

CARDS = {
    card.iid: card for card in [
        MemoryCard(
            type="16mb IFRW (WAAS) Orange", 
            iid=0x89007e00,
            memory_layout=[0, 1, 2, 3, 4, 5, 6, 7],
        ),
        MemoryCard(
            type="16Mb IFRW (WAAS) Silver",
            iid=0x01004100,
            memory_layout=[0, 1, 2, 3, 4, 5, 6, 7],
        ),
        MemoryCard(
            type="4Mb IFR (non-WAAS)",
            iid= 0x0100ad00,
            memory_layout=[0, 2],
        ),
    ]
}

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

    def __init__(self, handle: 'USBDeviceHandle') -> None:
        self.handle = handle
        self.memory_layout = self.MEMORY_LAYOUT_UNKNOWN

    def set_memory_layout(self, memory_layout: Iterable[int]) -> None:
        self.memory_layout = list(memory_layout)

    def write(self, data: bytes) -> None:
        self.handle.bulkWrite(self.WRITE_ENDPOINT, data, self.TIMEOUT)

    def read(self, length: int) -> bytes:
        return self.handle.bulkRead(self.READ_ENDPOINT, length, self.TIMEOUT)

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
            raise SkyboundException(f"Unexpected response: {buf}")

    def get_version(self) -> str:
        self.write(b"\x60")
        return self.read(0x0040).decode()

    def get_unknown(self) -> int: # TODO: what is this?
        self.write(b"\x50\x03")
        buf = self.read(0x0040)
        return int.from_bytes(buf, 'little')

    def get_iid(self) -> int:
        self.write(b"\x50\x04")
        buf = self.read(0x0040)
        return int.from_bytes(buf, 'little')
    
    def get_card(self) -> MemoryCard | None:
        return CARDS.get(self.get_iid())

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
            raise SkyboundException(f"Unexpected response: {buf}")

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
            raise SkyboundException(f"Unexpected response: {buf}")

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
