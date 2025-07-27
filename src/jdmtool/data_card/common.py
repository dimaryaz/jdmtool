from __future__ import annotations

from collections.abc import Callable, Generator
from enum import Enum
from typing import TYPE_CHECKING

from ..common import JdmToolException


if TYPE_CHECKING:
    from usb1 import USBDeviceHandle


class DataCardType(Enum):
    def __init__(self, sector_size: int, read_size, min_write_size: int, max_write_size: int):
        self.sector_size = sector_size
        self.read_size = read_size
        self.min_write_size = min_write_size
        self.max_write_size = max_write_size

    NONE    = (0x0,     0x0,    0x0,    0x0)
    NAVDATA = (0x10000, 0x1000, 0x1000, 0x1000)
    TAWS    = (0x10800, 0xf800, 0x0840, 0xffc0)


# (manufactorer_id, chip_id) -> (card_type, sectors_per_chip, description)
IID_MAP = {
    # 032: 2 MB Intel Series 2 (1 MB x 2)
    # 033: 3 MB Intel Series 2 (1 MB x 3)
    # 034: 4 MB Intel Series 2 (1 MB x 4)
    (0x89, 0xa2): (DataCardType.NAVDATA, 0x10, "non-WAAS (white)"),
    #      2 MB Intel          (1 MB x 2)
    #      3 MB Intel          (1 MB x 3)
    #      4 MB Intel          (1 MB x 4)
    (0x89, 0xa6): (DataCardType.NAVDATA, 0x10, "non-WAAS (white)"),

    # 421: 4 MB AMD Series C/D (2 MB x 2)
    # 431: 6 MB AMD Series C/D (2 MB x 3)
    # 441: 8 MB AMD Series C/D (2 MB x 4)
    (0x01, 0xad): (DataCardType.NAVDATA, 0x20, "non-WAAS (green)"),
    #      4 MB Intel Series   (2 MB x 2)
    #      6 MB Intel Series   (2 MB x 3)
    #      8 MB Intel Series   (2 MB x 4)
    (0x89, 0xaa): (DataCardType.NAVDATA, 0x20, "non-WAAS (green)"),

    # 451: 16MB AMD Series C/D (4 MB x 4)
    (0x01, 0x41): (DataCardType.NAVDATA, 0x40, "WAAS (silver)"),
    (0x89, 0x7e): (DataCardType.NAVDATA, 0x40, "WAAS (orange)"),

    # 128MB
    (0xec, 0x79): (DataCardType.TAWS, 0x800, "Terrain/Obstacles"),

    # 256MB
    (0xec, 0xda): (DataCardType.TAWS, 0x1000, "Terrain/Obstacles"),
}


class ProgrammingException(JdmToolException):
    pass


class BasicUsbDevice():
    handle: USBDeviceHandle
    read_endpoint: int
    write_endpoint: int

    TIMEOUT = 5000

    def __init__(self, handle: USBDeviceHandle, read_endpoint: int, write_endpoint: int) -> None:
        self.handle = handle
        self.read_endpoint = read_endpoint
        self.write_endpoint = write_endpoint

    def bulk_read(self, length: int) -> bytes:
        return self.handle.bulkRead(self.read_endpoint, length, self.TIMEOUT)

    def bulk_write(self, data: bytes) -> None:
        self.handle.bulkWrite(self.write_endpoint, data, self.TIMEOUT)

    def control_read(self, request_type: int, request: int, value: int, index: int, length: int) -> bytes:
        return self.handle.controlRead(request_type, request, value, index, length, self.TIMEOUT)

    def control_write(self, request_type: int, request: int, value: int, index: int, data: bytes) -> None:
        self.handle.controlWrite(request_type, request, value, index, data, self.TIMEOUT)


class ProgrammingDevice(BasicUsbDevice):
    card_type = DataCardType.NONE
    chips = 0
    sectors_per_chip = 0x0
    card_info = ""

    def __init__(self, handle: "USBDeviceHandle", read_endpoint: int, write_endpoint: int) -> None:
        super().__init__(handle, read_endpoint, write_endpoint)

    def init(self) -> None:
        pass

    def close(self) -> None:
        pass

    def has_card(self) -> bool:
        raise NotImplementedError()

    def get_card_name(self) -> str:
        return f"{self.chips * self.sectors_per_chip // 0x10}MB {self.card_info}"

    def get_card_description(self) -> str:
        return f"{self.chips} chip(s) of {self.sectors_per_chip//0x10}MB"

    def get_firmware_version(self) -> str:
        raise NotImplementedError()

    def get_firmware_description(self) -> str:
        raise NotImplementedError()

    def get_chip_iids(self) -> list[int]:
        raise NotImplementedError()

    def init_data_card(self) -> None:
        raise NotImplementedError()

    def get_total_sectors(self) -> int:
        return self.chips * self.sectors_per_chip

    def get_total_size(self) -> int:
        return self.get_total_sectors() * self.card_type.sector_size

    def pad_for_write(self, block: bytes) -> bytes:
        rem = len(block) % self.card_type.min_write_size
        if rem != 0:
            block += b'\xFF' * (self.card_type.min_write_size - rem)
        return block

    def read_blocks(self, start_sector: int, length: int) -> Generator[bytes, None, None]:
        raise NotImplementedError()

    def erase_sectors(self, start_sector: int, num_sectors: int) -> Generator[None, None, None]:
        raise NotImplementedError()

    def write_blocks(
        self, start_sector: int, length: int,
        read_func: Callable[[int], bytes]
    ) -> Generator[bytes, None, None]:
        raise NotImplementedError()

    def check_card(self):
        if not self.has_card():
            raise ProgrammingException("Data card has disappeared!")

    def check_supports_write(self) -> None:
        pass
