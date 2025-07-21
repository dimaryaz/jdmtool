from __future__ import annotations

from collections.abc import Callable, Generator
from pathlib import Path
from struct import unpack
from typing import BinaryIO

import usb1
from usb1 import USBDeviceHandle, USBError

from .common import IID_MAP, BasicUsbDevice, DataCardType, ProgrammingDevice, ProgrammingException


FIRMWARE_DIR = Path(__file__).parent / 'firmware'


class AlreadyUpdatedException(ProgrammingException):
    pass


class GarminFirmwareWriter(BasicUsbDevice):
    WRITE_ENDPOINT = -1
    READ_ENDPOINT = -1

    def write_firmware_0x300(self) -> None:
        import time
        print("Writing 0x300 part 1 of 2")
        with open(FIRMWARE_DIR / 'grmn0300-part1.dat', 'rb') as fd:
            self.write_firmware(fd)
        time.sleep(2)
        print("Writing 0x300 part 2 of 2")
        with open(FIRMWARE_DIR / 'grmn0300-part2.dat', 'rb') as fd:
            self.write_firmware(fd)
        time.sleep(2)

    def write_firmware_stage1(self) -> None:
        print("Writing stage 1")
        with open(FIRMWARE_DIR / 'grmn0500.dat', 'rb') as fd:
            self.write_firmware(fd)

    def init_stage2(self) -> None:
        version = self.control_read(0xC0, 0x8A, 0x0000, 0x0000, 512)
        print("Check if stage 2 required...")
        # this will not catch the "old" card programmer as its FW has different build time
        if version != b'Aviation Card Programmer Ver 3.02 Aug 10 2015 13:21:51\x00':
            print("No, we're good.")
            raise AlreadyUpdatedException()

    def write_firmware_stage2(self) -> None:
        print("Writing stage 2")
        with open(FIRMWARE_DIR / 'grmn1300.dat', 'rb') as fd:
            self.write_firmware(fd)

    def write_firmware(self, fd: BinaryIO) -> None:
        import time
        while True:
            buf = fd.read(4)
            if not buf:
                break

            addr, data_len = unpack('<HH', buf)
            data = fd.read(data_len)
            # self.control_write(0x40, 0xA0, addr, 0x0000, data)
            attempts = 0
            while True:
                try:
                    self.control_write(0x40, 0xA0, addr, 0x0000, data)
                    break
                except USBError as e:
                    # If the device disappears (no device), exit immediately
                    if e.value == -4:  # LIBUSB_ERROR_NO_DEVICE
                        return
                    # Otherwise only retry on I/O errors
                    if e.value != -1:  # LIBUSB_ERROR_IO
                        raise
                    attempts += 1
                    if attempts >= 3:
                        raise
                    time.sleep(0.1)
                    
class GarminProgrammingDevice(ProgrammingDevice):

    NO_CARD_IDS = { # card reader / firmware versions
        0x00697641, # "newer" 010-10579-20 
        0x00090304  # "older" 011-01277-00
        }

    # Standard values: WRITE_ENDPOINT = 0x02, READ_ENDPOINT = 0x86 ("newer"), 0x82 ("older") reader
    def __init__(self, handle: USBDeviceHandle, read_endpoint: int = 0x86, write_endpoint: int = 0x02) -> None:
         # Initialize base device
        super().__init__(handle)
        # Override endpoints for this Garmin device
        self.READ_ENDPOINT = read_endpoint
        self.WRITE_ENDPOINT = write_endpoint
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
        if card_id in self.NO_CARD_IDS:
            raise ProgrammingException("Card is missing!")

        self.chips = (card_id & 0x00ff0000) >> 16
        manufacturer_id = card_id & 0xff
        chip_id = (card_id & 0x0000ff00) >> 8

        info = IID_MAP.get((manufacturer_id, chip_id))
        if info is None:
            raise ProgrammingException(f"Unknown data card ID: 0x{card_id:08x}. Please file a bug!")

        (self.card_type, self.sectors_per_chip, self.card_info) = info

        self.end_read()
        self.end_write()

    def has_card(self) -> bool:
        return self.get_card_id() not in self.NO_CARD_IDS

    def get_firmware_version(self) -> str:
        return self.firmware

    def get_firmware_description(self) -> str:
        return self.firmware

    def begin_erase(self, start_sector: int, sector_count: int) -> None:
        self.check_card()

        # Doesn't seem to make a difference, but this is what Garmin's software does.
        if self.card_type == DataCardType.TAWS:
            unknown1 = 3
            unknown2 = 2
        else:
            unknown1 = 0
            unknown2 = 1

        unknown1_bytes = unknown1.to_bytes(2, 'big')
        start_sector_byte = start_sector.to_bytes(2, 'big')
        sector_count_byte = sector_count.to_bytes(2, 'big')
        unknown2_bytes = unknown2.to_bytes(2, 'big')

        buf = (
            unknown1_bytes + start_sector_byte + b"\x00\x00\x00\x00" +
            sector_count_byte + unknown2_bytes + b"\x00\x00"
        )
        self.control_write(0x40, 0x85, 0x0000, 0x0000, buf)

    def begin_write(self, start_sector: int) -> None:
        self.check_card()

        # Doesn't seem to make a difference, but this is what Garmin's software does.
        if self.card_type == DataCardType.TAWS:
            unknown1 = 5
            unknown2 = 8
        else:
            unknown1 = 4
            unknown2 = 0

        unknown1_bytes = unknown1.to_bytes(2, 'big')
        start_sector_byte = start_sector.to_bytes(2, 'big')
        # Not clear if this is actually an offset, or what is supported.
        sector_offset_bytes = (0).to_bytes(2, "big")
        unknown2_bytes = unknown2.to_bytes(2, 'big')

        buf = unknown1_bytes + start_sector_byte + sector_offset_bytes + b"\x00\x00" + unknown2_bytes
        self.control_write(0x40, 0x86, 0x0000, 0x0000, buf)

    def end_write(self) -> None:
        self.control_write(0x40, 0x87, 0x0000, 0x0000, b"")

    def begin_read(self, start_sector: int) -> None:
        self.check_card()

        # Doesn't seem to make a difference, but this is what Garmin's software does.
        unknown = 0 if self.card_type == DataCardType.TAWS else 4
        unknown_bytes = unknown.to_bytes(2, 'big')

        start_sector_bytes = start_sector.to_bytes(2, 'big')

        # We can technically read from the middle of a sector.
        # For NavData cards, it's the offset in bytes, 0x0 through 0xffff.
        # But TAWS cards have a 0x10800 sector size, which doesn't fit in two bytes -
        # so instead, it's scaled down, i.e., (offset * 0x10000 // 0x10800).
        # We're not going to bother with this craziness, and will only support 0.
        sector_offset_bytes = (0).to_bytes(2, "big")

        buf = unknown_bytes + start_sector_bytes + sector_offset_bytes + b"\x00\x00\x00\x00"
        self.control_write(0x40, 0x81, 0x0000, 0x0000, buf)

    def end_read(self) -> None:
        self.control_write(0x40, 0x83, 0x0000, 0x0000, b"")

    def read_blocks(self, start_sector: int, length: int) -> Generator[bytes, None, None]:
        block_size = self.card_type.read_size

        self.begin_read(start_sector)
        try:
            while length > 0:
                block = self.bulk_read(block_size)
                yield block[:min(block_size, length)]
                length -= block_size
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
        self, start_sector: int, length: int,
        read_func: Callable[[int], bytes]
    ) -> Generator[bytes, None, None]:
        block_size = self.card_type.max_write_size

        self.begin_write(start_sector)
        try:
            while length > 0:
                read_size = min(block_size, length)
                block = read_func(read_size)
                if len(block) != read_size:
                    raise IOError(f"Expected {read_size} bytes, but got {len(block)}")
                self.bulk_write(self.pad_for_write(block))
                yield block
                length -= block_size
        finally:
            self.end_write()
