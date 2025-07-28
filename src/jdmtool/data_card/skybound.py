from __future__ import annotations

from collections.abc import Callable, Generator

from .common import IID_MAP, ProgrammingDevice, ProgrammingException


class SkyboundDevice(ProgrammingDevice):

    MEMORY_OFFSETS = [0x00E0, 0x0160, 0x01A0, 0x01C0]

    FIRMWARE_NAME = {
        "20071203": "G2 Black",
        "20140530": "G2 Orange",
    }

    is_orange_card = False

    def init(self) -> None:
        self.set_led(True)

    def close(self) -> None:
        self.set_led(False)

    def set_led(self, on: bool) -> None:
        if on:
            self.bulk_write(b'\x12')
        else:
            self.bulk_write(b'\x13')

    def has_card(self) -> bool:
        self.bulk_write(b"\x18")
        buf = self.bulk_read(0x0040)
        if buf == b"\x00":
            return True
        elif buf == b"\x01":
            return False
        else:
            raise ProgrammingException(f"Unexpected response: {buf}")

    def get_chip_iids(self) -> list[int]:
        chip_iids: list[int] = []

        for offset in self.MEMORY_OFFSETS:
            self.select_physical_sector(offset)
            self.before_read()
            chip_iid = self.get_iid()

            if chip_iid == 0x90009000 or chip_iid == 0xff00ff00:  # depends on G2 firmware
                break

            chip_iids.append(chip_iid)

        return chip_iids

    def init_data_card(self) -> None:
        if not self.has_card():
            raise ProgrammingException("Card is missing!")

        chip_iids = self.get_chip_iids()

        if not chip_iids:
            raise ProgrammingException("Unsupported data card - possibly Terrain/Obstacles")

        hex_iids = [f"{chip_iid:08x}" for chip_iid in chip_iids]

        self.chips = len(chip_iids)

        if self.chips == 1 or len(set(chip_iids)) > 1:
            # None of the known cards have a single chip or mixed chip types.
            raise ProgrammingException(f"Unknown data card with chip IIDs: {hex_iids}. Please file a bug!")

        iid = chip_iids[0]
        manufacturer_id = iid >> 24
        chip_id = (iid >> 8) & 0xFF

        info = IID_MAP.get((manufacturer_id, chip_id))
        if info is None:
            raise ProgrammingException(f"Unknown data card with chip IIDs: {hex_iids}. Please file a bug!")

        self.is_orange_card = (manufacturer_id, chip_id) == (0x89, 0x7e)

        (self.card_type, self.sectors_per_chip, self.card_info) = info

    def get_firmware_version(self) -> str:
        self.bulk_write(b"\x60")
        return self.bulk_read(0x0040).decode()

    def get_firmware_description(self) -> str:
        version = self.get_firmware_version()
        name = self.FIRMWARE_NAME.get(version, "unknown")
        return f"{version} ({name})"

    def get_1m_chip_version(self) -> int:
        self.bulk_write(b"\x50\x03")
        buf = self.bulk_read(0x0040)
        return int.from_bytes(buf, 'little')

    def get_iid(self) -> int:
        self.bulk_write(b"\x50\x04")
        buf = self.bulk_read(0x0040)
        return int.from_bytes(buf, 'little')

    def read_block(self) -> bytes:
        self.bulk_write(b"\x28")
        return self.bulk_read(0x1000)

    def write_block(self, data: bytes) -> None:
        if len(data) != 0x1000:
            raise ValueError("Data must be 4096 bytes")

        if self.sectors_per_chip == 0x10:  # 1MB chip
            self.bulk_write(b"\x2A\x03")
            expected_byte = 0x80
        else:
            self.bulk_write(b"\x2A\x04")
            expected_byte = data[-1]

        self.bulk_write(data)
        buf = self.bulk_read(0x0040)

        if buf[0] != expected_byte or buf[1:] != b"\x00\x00\x00":
            raise ProgrammingException(f"Unexpected response: {buf}")

    def select_physical_sector(self, sector_id: int) -> None:
        if not (0x0000 <= sector_id <= 0xFFFF):
            raise ValueError("Invalid sector ID")
        self.bulk_write(b"\x30\x00\x00" + sector_id.to_bytes(2, 'little'))

    def translate_sector(self, sector_id: int) -> int:
        offset_id = sector_id // self.sectors_per_chip
        if self.sectors_per_chip > 0x20:  # 4MB chip
            offset_for_16mb = 0x200 * (sector_id // 0x20 % 2)
            return self.MEMORY_OFFSETS[offset_id] + sector_id % 0x20 + offset_for_16mb
        else:
            return self.MEMORY_OFFSETS[offset_id] + sector_id % self.sectors_per_chip

    def select_sector(self, sector_id: int) -> None:
        self.select_physical_sector(self.translate_sector(sector_id))

    def erase_sector(self) -> None:
        if self.sectors_per_chip == 0x10:  # 1MB chip
            key = b"\x03"
            self.bulk_write(b"\x16")
            self.bulk_write(b"\x52" + key)
        else:
            key = b"\x04"
            self.bulk_write(b"\x52" + key)
        buf = self.bulk_read(0x0040)
        if buf != key:
            raise ProgrammingException(f"Unexpected response: {buf}")

    def before_read(self) -> None:
        # It's not clear that this does anything, but JDM seems to send it
        # before reading anything, so do the same thing.
        self.bulk_write(b"\x40")

    def before_write(self) -> None:
        # Same as above.
        self.bulk_write(b"\x42")

    def _loop_helper(self, i: int) -> None:
        self.set_led(i % 2 == 0)
        self.check_card()

    def read_blocks(self, start_sector: int, length: int) -> Generator[bytes, None, None]:
        block_size = self.card_type.read_size
        blocks_per_sector = self.card_type.sector_size // block_size

        self.before_read()
        block_idx = 0

        while length > 0:
            if block_idx % blocks_per_sector == 0:
                self.select_sector(start_sector + block_idx // blocks_per_sector)

            self._loop_helper(block_idx)
            block_idx += 1

            block = self.read_block()
            assert len(block) == block_size
            yield block[:min(block_size, length)]
            length -= block_size

    def erase_sectors(self, start_sector: int, num_sectors: int) -> Generator[None, None, None]:
        self.before_write()
        for sector in range(start_sector, start_sector + num_sectors):
            self._loop_helper(sector)
            self.select_sector(sector)
            self.erase_sector()
            yield

    def write_blocks(
        self, start_sector: int, length: int,
        read_func: Callable[[int], bytes]
    ) -> Generator[bytes, None, None]:
        block_size = self.card_type.max_write_size
        blocks_per_sector = self.card_type.sector_size // block_size

        self.before_write()
        block_idx = 0

        while length > 0:
            if block_idx % blocks_per_sector == 0:
                self.select_sector(start_sector + block_idx // blocks_per_sector)

            self._loop_helper(block_idx)
            block_idx += 1

            read_size = min(block_size, length)
            block = read_func(read_size)
            if len(block) != read_size:
                raise IOError(f"Expected {read_size} bytes, but got {len(block)}")
            self.write_block(self.pad_for_write(block))
            yield block
            length -= block_size

    def check_supports_write(self) -> None:
        if self.is_orange_card and self.get_firmware_version() != "20140530":
            raise ProgrammingException("Writing to orange-label cards requires an orange-label adapter!")
