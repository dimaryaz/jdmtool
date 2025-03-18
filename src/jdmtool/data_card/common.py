from __future__ import annotations

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING

from ..common import JdmToolException


if TYPE_CHECKING:
    from usb1 import USBDeviceHandle


# (manufactorer_id, chip_id) -> (sectors_per_chip, description)
IID_MAP = {
    # 032: 2 MB Intel Series 2 (1 MB x 2)
    # 033: 3 MB Intel Series 2 (1 MB x 3)
    # 034: 4 MB Intel Series 2 (1 MB x 4)
    (0x89, 0xa2): (0x10, "non-WAAS (white)"),

    # 421: 4 MB AMD Series C/D (2 MB x 2)
    # 431: 6 MB AMD Series C/D (2 MB x 3)
    # 441: 8 MB AMD Series C/D (2 MB x 4)
    (0x01, 0xad): (0x20, "non-WAAS (green)"),

    # 451: 16MB AMD Series C/D (4 MB x 4)
    (0x01, 0x41): (0x40, "WAAS (silver)"),
    (0x89, 0x7e): (0x40, "WAAS (orange)"),
}


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

    def control_read(self, request_type: int, request: int, value: int, index: int, length: int) -> bytes:
        return self.handle.controlRead(request_type, request, value, index, length, self.TIMEOUT)

    def control_write(self, request_type: int, request: int, value: int, index: int, data: bytes) -> None:
        self.handle.controlWrite(request_type, request, value, index, data, self.TIMEOUT)


class ProgrammingDevice(BasicUsbDevice):
    BLOCK_SIZE = 0x1000
    BLOCKS_PER_SECTOR = 0x10
    SECTOR_SIZE = BLOCK_SIZE * BLOCKS_PER_SECTOR  # 64KB

    chips = 0
    sectors_per_chip = 0x0
    card_info = ""

    def init(self) -> None:
        pass

    def close(self) -> None:
        pass

    def has_card(self) -> bool:
        raise NotImplementedError()

    def get_card_name(self) -> str:
        return f"{self.chips * self.sectors_per_chip // 0x10}MB {self.card_info}"

    def get_card_description(self) -> str:
        return f"{self.chips} chips of {self.sectors_per_chip//0x10}MB"

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
        return self.get_total_sectors() * self.SECTOR_SIZE

    def read_blocks(self, start_sector: int, num_sectors: int) -> Generator[bytes, bool, None]:
        raise NotImplementedError()

    def erase_sectors(self, start_sector: int, num_sectors: int) -> Generator[None, None, None]:
        raise NotImplementedError()

    def write_blocks(
        self, start_sector: int, num_sectors: int,
        read_func: Callable[[], bytes]
    ) -> Generator[None, None, None]:
        raise NotImplementedError()

    def check_card(self):
        if not self.has_card():
            raise ProgrammingException("Data card has disappeared!")
