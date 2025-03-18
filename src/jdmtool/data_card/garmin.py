from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from struct import unpack
from typing import BinaryIO

from .common import IID_MAP, BasicUsbDevice, ProgrammingDevice, ProgrammingException


FIRMWARE_DIR = Path(__file__).parent / 'firmware'


class AlreadyUpdatedException(ProgrammingException):
    pass


class GarminFirmwareWriter(BasicUsbDevice):
    WRITE_ENDPOINT = -1
    READ_ENDPOINT = -1

    def write_firmware_stage1(self) -> None:
        with open(FIRMWARE_DIR / 'grmn0500.dat', 'rb') as fd:
            self.write_firmware(fd)

    def init_stage2(self) -> None:
        version = self.control_read(0xC0, 0x8A, 0x0000, 0x0000, 512)
        if version != b'Aviation Card Programmer Ver 3.02 Aug 10 2015 13:21:51\x00':
            raise AlreadyUpdatedException()

    def write_firmware_stage2(self) -> None:
        with open(FIRMWARE_DIR / 'grmn1300.dat', 'rb') as fd:
            self.write_firmware(fd)

    def write_firmware(self, fd: BinaryIO) -> None:
        while True:
            buf = fd.read(4)
            if not buf:
                break

            addr, data_len = unpack('<HH', buf)
            data = fd.read(data_len)
            self.control_write(0x40, 0xA0, addr, 0x0000, data)


class GarminProgrammingDevice(ProgrammingDevice):
    WRITE_ENDPOINT = 0x02
    READ_ENDPOINT = 0x86

    NO_CARD = 0x00697641

    def __init__(self, handle):
        super().__init__(handle)
        self.firmware = ""

    def init(self) -> None:
        buf = self.control_read(0xC0, 0x8A, 0x0000, 0x0000, 512)
        self.firmware = buf.rstrip(b'\x00').decode()

    def get_card_id(self) -> int:
        return int.from_bytes(self.control_read(0xC0, 0x82, 0x0000, 0x0000, 4), 'little')

    def get_chip_iids(self) -> list[int]:
        return [self.get_card_id()]

    def init_data_card(self) -> None:
        card_id = self.get_card_id()
        if card_id == self.NO_CARD:
            raise ProgrammingException("Card is missing!")
        if card_id == 0x0101daec or card_id == 0x010179ec:
            raise ProgrammingException("TAWS/Terrain is not (yet) supported")

        self.chips = (card_id & 0x00ff0000) >> 16
        manufacturer_id = card_id & 0xff
        chip_id = (card_id & 0x0000ff00) >> 8

        info = IID_MAP.get((manufacturer_id, chip_id))
        if info is None:
            raise ProgrammingException(f"Unknown data card ID: 0x{card_id:08x}. Please file a bug!")

        (self.sectors_per_chip, self.card_info) = info

        self.end_read()
        self.end_write()

    def has_card(self) -> bool:
        return self.get_card_id() != self.NO_CARD

    def get_firmware_version(self) -> str:
        return self.firmware

    def get_firmware_description(self) -> str:
        return self.firmware

    def begin_erase(self, start_sector: int, sector_count: int) -> None:
        self.check_card()

        start_sector_byte = start_sector.to_bytes(2, 'big')
        sector_count_byte = sector_count.to_bytes(2, 'big')
        buf = b"\x00\x00" + start_sector_byte + b"\x00\x00\x00\x00" + sector_count_byte + b"\x00\x01\x00\x00"
        self.control_write(0x40, 0x85, 0x0000, 0x0000, buf)

    def begin_write(self, start_sector: int) -> None:
        self.check_card()

        start_sector_byte = start_sector.to_bytes(2, 'big')
        buf = b"\x00\x04" + start_sector_byte + b"\x00\x00\x00\x00\x00\x00"
        self.control_write(0x40, 0x86, 0x0000, 0x0000, buf)

    def end_write(self) -> None:
        self.control_write(0x40, 0x87, 0x0000, 0x0000, b"")

    def begin_read(self, start_sector: int) -> None:
        self.check_card()

        start_sector_byte = start_sector.to_bytes(2, 'big')
        buf = b"\x00\x04" + start_sector_byte + b"\x00\x00\x00\x00\x00\x00"
        self.control_write(0x40, 0x81, 0x0000, 0x0000, buf)

    def end_read(self) -> None:
        self.control_write(0x40, 0x83, 0x0000, 0x0000, b"")

    def read_blocks(self, start_sector: int, num_sectors: int) -> Generator[None, bytes, None]:
        self.begin_read(start_sector)
        try:
            for _ in range(num_sectors * self.BLOCKS_PER_SECTOR):
                yield self.bulk_read(self.BLOCK_SIZE)
        finally:
            self.end_read()

    def erase_sectors(self, start_sector: int, num_sectors: int) -> Generator[None, None, None]:
        self.begin_erase(start_sector, num_sectors)
        try:
            for idx in range(num_sectors):
                buf = self.bulk_read(0x000C)
                if buf[:-2] != b"\x42\x6C\x4B\x65\x00\x00\x00\x00\x00\x00":
                    raise ProgrammingException(f"Unexpected response: {buf}")
                if int.from_bytes(buf[-2:], 'big') != idx:
                    raise ProgrammingException(f"Unexpected response: {buf}")
                yield
        finally:
            self.end_write()

    def write_blocks(
        self, start_sector: int, num_sectors: int,
        read_func: Callable[[], bytes]
    ) -> Generator[None, None, None]:
        self.begin_write(start_sector)
        try:
            for _ in range(num_sectors * self.BLOCKS_PER_SECTOR):
                block = read_func()
                self.bulk_write(block)
                yield
        finally:
            self.end_write()
