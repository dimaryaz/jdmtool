from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from usb1 import USBDeviceHandle


class SkyboundException(Exception):
    pass


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

    MEMORY_LAYOUT_2MB = [0]
    MEMORY_LAYOUT_4MB = [0, 2]
    MEMORY_LAYOUT_6MB = [0, 2, 4]
    MEMORY_LAYOUT_8MB = [0, 2, 4, 6]
    MEMORY_LAYOUT_16MB = [0, 1, 2, 3, 4, 5, 6, 7]

    def __init__(self, handle: 'USBDeviceHandle') -> None:
        self.handle = handle
        self.memory_layout = []
        self.card_name = "undefined"

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

    def init_data_card(self) -> None:
        if not self.has_card():
            raise SkyboundException("Card is missing!")

        self.select_physical_page(self.MEMORY_OFFSETS[0])
        self.before_read()

        iid = self.get_iid()
        if iid == 0x8900a200:
            # 032: 2 MB Intel Series 2 (1 MB x 2)
            self.memory_layout = SkyboundDevice.MEMORY_LAYOUT_2MB
            self.card_name = "2MB"
        elif iid == 0x0100ad00:
            # 4MB, 6MB, or 8MB, depending on whether it has 2, 3, or 4 chips.
            chip_config: List[int] = []
            for chip_idx in [1, 2, 3]:
                self.select_physical_page(self.MEMORY_OFFSETS[chip_idx * 2])
                self.before_read()
                chip_iid = self.get_iid()
                if chip_iid == iid:
                    chip_config.append(1)
                elif chip_iid == 0x90009000:
                    chip_config.append(0)
                else:
                    raise SkyboundException(f"Unexpected IID 0x{chip_iid:08x} for chip {chip_idx}")

            if chip_config == [1, 0, 0]:
                # 421: 4 MB AMD Series C/D (2 MB x 2)
                self.memory_layout = SkyboundDevice.MEMORY_LAYOUT_4MB
                self.card_name = "4MB"
            elif chip_config == [1, 1, 0]:
                # 431: 6 MB AMD Series C/D (2 MB x 3)
                self.memory_layout = SkyboundDevice.MEMORY_LAYOUT_6MB
                self.card_name = "6MB"
            elif chip_config == [1, 1, 1]:
                # 441: 8 MB AMD Series C/D (2 MB x 4)
                self.memory_layout = SkyboundDevice.MEMORY_LAYOUT_8MB
                self.card_name = "8MB"
            else:
                raise SkyboundException(f"Unexpected chip configuration: {chip_config}")
        elif iid == 0x01004100:
            # 451: 16MB AMD Series C/D (4 MB x 4)
            self.memory_layout = SkyboundDevice.MEMORY_LAYOUT_16MB
            self.card_name = "16MB WAAS (silver)"
        elif iid == 0x89007E00:
            # 451: 16MB AMD Series C/D (4 MB x 4)
            self.memory_layout = SkyboundDevice.MEMORY_LAYOUT_16MB
            self.card_name = "16MB WAAS (orange)"
        elif iid == 0x90009000:
            raise SkyboundException("Unsupported data card - possibly Terrain/Obstacles")
        else:
            raise SkyboundException(f"Unknown data card IID: 0x{iid:08x}. Please file a bug!")

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
