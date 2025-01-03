from typing import TYPE_CHECKING, List, Tuple

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
    BLOCKS_PER_SECTOR = 0x10
    SECTOR_SIZE = BLOCK_SIZE * BLOCKS_PER_SECTOR  # 64KB

    MEMORY_OFFSETS = [0x00E0, 0x0160, 0x01A0, 0x01C0]

    FIRMWARE_NAME = {
        "20071203": "G2 Black",
        "20140530": "G2 Orange",
    }

    def __init__(self, handle: 'USBDeviceHandle') -> None:
        self.handle = handle
        self.card_name = "undefined"
        self.chips = 0
        self.sectors_per_chip = 0x0

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

        chip_iids: List[int] = []

        for offset in self.MEMORY_OFFSETS:
            self.select_physical_page(offset)
            self.before_read()
            chip_iid = self.get_iid()

            if chip_iid == 0x90009000 or chip_iid == 0xff00ff00:  # depends on G2 firmware
                break

            chip_iids.append(chip_iid)

        if not chip_iids:
            raise SkyboundException("Unsupported data card - possibly Terrain/Obstacles")

        hex_iids = [f"{chip_iid:08x}" for chip_iid in chip_iids]

        self.chips = len(chip_iids)
        iid = chip_iids[0]

        if self.chips == 1 or len(set(chip_iids)) > 1:
            # None of the known cards have a single chip or mixed chip types.
            raise SkyboundException(f"Unknown data card with chip IIDs: {hex_iids}. Please file a bug!")

        if iid == 0x8900a200:
            # 032: 2 MB Intel Series 2 (1 MB x 2)
            # 033: 3 MB Intel Series 2 (1 MB x 3)
            # 034: 4 MB Intel Series 2 (1 MB x 4)
            self.sectors_per_chip = 0x10
            self.card_name = f"{self.chips}MB"
        elif iid == 0x0100ad00:
            # 421: 4 MB AMD Series C/D (2 MB x 2)
            # 431: 6 MB AMD Series C/D (2 MB x 3)
            # 441: 8 MB AMD Series C/D (2 MB x 4)
            self.sectors_per_chip = 0x20
            self.card_name = f"{self.chips*2}MB"
        elif iid == 0x01004100:
            # 451: 16MB AMD Series C/D (4 MB x 4)
            self.sectors_per_chip = 0x40
            self.card_name = "16MB WAAS (silver)"
        elif iid == 0x89007E00:
            # 451: 16MB AMD Series C/D (4 MB x 4)
            self.sectors_per_chip = 0x40
            self.card_name = "16MB WAAS (orange)"
        else:
            # Unknown IID
            raise SkyboundException(f"Unknown data card with chip IIDs: {hex_iids}. Please file a bug!")

    def get_firmware_version_name(self) -> Tuple[str, str]:
        self.write(b"\x60")
        version = self.read(0x0040).decode()
        name = self.FIRMWARE_NAME.get(version, "unknown")
        return version, name

    def get_1m_chip_version(self) -> int:
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

        if self.sectors_per_chip == 0x10:  # 1MB chip
            self.write(b"\x2A\x03")
            expected_byte = 0x80
        else:
            self.write(b"\x2A\x04")
            expected_byte = data[-1]

        self.write(data)
        buf = self.read(0x0040)

        if buf[0] != expected_byte or buf[1:] != b"\x00\x00\x00":
            raise SkyboundException(f"Unexpected response: {buf}")

    def select_physical_page(self, page_id: int) -> None:
        if not (0x0000 <= page_id <= 0xFFFF):
            raise ValueError("Invalid page ID")
        self.write(b"\x30\x00\x00" + page_id.to_bytes(2, 'little'))

    def translate_page(self, page_id: int) -> int:
        offset_id = page_id // self.sectors_per_chip
        if self.sectors_per_chip > 0x20:  # 4MB chip
            offset_for_16mb = 0x200 * (page_id // 0x20 % 2)
            return self.MEMORY_OFFSETS[offset_id] + page_id % 0x20 + offset_for_16mb
        else:
            return self.MEMORY_OFFSETS[offset_id] + page_id % self.sectors_per_chip

    def select_page(self, page_id: int) -> None:
        self.select_physical_page(self.translate_page(page_id))

    def erase_page(self) -> None:
        if self.sectors_per_chip == 0x10:  # 1MB chip
            key = b"\x03"
            self.write(b"\x16")
            self.write(b"\x52" + key)
        else:
            key = b"\x04"
            self.write(b"\x52" + key)
        buf = self.read(0x0040)
        if buf != key:
            raise SkyboundException(f"Unexpected response: {buf}")

    def before_read(self) -> None:
        # It's not clear that this does anything, but JDM seems to send it
        # before reading anything, so do the same thing.
        self.write(b"\x40")

    def before_write(self) -> None:
        # Same as above.
        self.write(b"\x42")

    def get_total_pages(self) -> int:
        return self.chips * self.sectors_per_chip

    def get_total_size(self) -> int:
        return self.get_total_pages() * self.SECTOR_SIZE  # chips * sectors_per_chip * SECTOR_SIZE
