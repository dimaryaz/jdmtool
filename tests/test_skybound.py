from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pytest

from jdmtool.data_card.common import ProgrammingException
from jdmtool.data_card.skybound import SkyboundDevice


class WriteFormat(Enum):
    FORMAT_1 = 1  # Used by 1MB chips (or possibly by Intel chips)
    FORMAT_2 = 2  # Used by 2MB and 4MB chips (or possibly by AMD chips)


@dataclass
class ChipConfig:
    iid: int
    sectors: int
    write_format: WriteFormat


CHIP_INTEL_1MB      = ChipConfig(0x8900a200, 0x10, WriteFormat.FORMAT_1)
CHIP_AMD_2MB        = ChipConfig(0x0100ad00, 0x20, WriteFormat.FORMAT_2)
CHIP_AMD_4MB_SILVER = ChipConfig(0x01004100, 0x40, WriteFormat.FORMAT_2)
CHIP_AMD_4MB_ORANGE = ChipConfig(0x89007e00, 0x40, WriteFormat.FORMAT_2)


SUPPORTED_CARDS = [
    (CHIP_INTEL_1MB, 2, "2MB non-WAAS (white)"),
    (CHIP_INTEL_1MB, 3, "3MB non-WAAS (white)"),
    (CHIP_INTEL_1MB, 4, "4MB non-WAAS (white)"),
    (CHIP_AMD_2MB, 2, "4MB non-WAAS (green)"),
    (CHIP_AMD_2MB, 3, "6MB non-WAAS (green)"),
    (CHIP_AMD_2MB, 4, "8MB non-WAAS (green)"),
    (CHIP_AMD_4MB_SILVER, 4, "16MB WAAS (silver)"),
    (CHIP_AMD_4MB_ORANGE, 4, "16MB WAAS (orange)"),
]

FAKE_CHIP = ChipConfig(0x12345678, 0x20, WriteFormat.FORMAT_1)


class State(Enum):
    NONE = 0
    READING = 1
    WRITING = 2
    ERASING = 3


class UsbHandleMock:
    EMPTY_BLOCK = b'\xFF' * 0x1000

    pending_response: Optional[bytes]

    def __init__(self, n_chips: int, chip: ChipConfig, g2_orange: bool):
        self.pending_response = None
        self.offsets = [0x00E0, 0x0160, 0x01A0, 0x01C0][:n_chips]
        self.chip = chip
        self.g2_orange = g2_orange
        self.led = False
        self.state = State.NONE
        self.current_sector = -1
        self.current_block = -1
        self.writing = False
        self.blocks = [self.EMPTY_BLOCK] * (n_chips * chip.sectors * 0x10)

    def bulkRead(self, endpoint: int, length: int, timeout=0) -> bytes:
        assert endpoint == 0x81
        assert self.pending_response is not None
        assert len(self.pending_response) <= length
        try:
            return self.pending_response
        finally:
            self.pending_response = None

    def bulkWrite(self, endpoint: int, data: bytes, timeout=0) -> None:
        assert endpoint == 0x02
        assert self.pending_response is None

        if self.writing:
            assert len(data) == 0x1000, f"Invalid block size: {len(data)}"
            block_idx = self.current_sector * 0x10 + self.current_block
            assert self.blocks[block_idx] == self.EMPTY_BLOCK, "Block has not been erased!"
            self.blocks[block_idx] = data
            self.current_block += 1

            if self.chip.write_format is WriteFormat.FORMAT_1:
                self.pending_response = b"\x80\x00\x00\x00"
            else:
                self.pending_response = data[-1:] + b"\x00\x00\x00"

            self.writing = False
            return

        if data == b'\x18':
            self.pending_response = self._has_card()
        elif data.startswith(b'\x30\x00\x00'):
            assert len(data) == 5
            physical_sector = int.from_bytes(data[3:], 'little')

            self.current_sector = -1
            self.current_block = -1

            sectors = min(self.chip.sectors, 0x20)

            for chip_idx, offset in enumerate(self.offsets):
                if offset <= physical_sector < offset + sectors:
                    self.current_sector = chip_idx * self.chip.sectors + (physical_sector - offset)
                    self.current_block = 0
                    break
                if self.chip.sectors > 0x20:
                    if offset + 0x200 <= physical_sector < offset + 0x200 + sectors:
                        self.current_sector = chip_idx * self.chip.sectors + (physical_sector - offset - 0x200 + 0x20)
                        self.current_block = 0
                        break
        elif data == b'\x16':
            assert self.chip.write_format == WriteFormat.FORMAT_1
            self.state = State.ERASING
        elif data == b'\x40':
            self.state = State.READING
        elif data == b'\x42':
            self.state = State.WRITING
        elif data == b'\x50\x04':
            if self.current_sector >= 0:
                iid = self.chip.iid
            else:
                iid = 0xff00ff00 if self.g2_orange else 0x90009000
            self.pending_response = iid.to_bytes(4, 'little')
        elif data == b'\x28':
            assert self.current_sector >= 0
            assert 0 <= self.current_block < 0x10
            assert self.state is State.READING
            block_idx = self.current_sector * 0x10 + self.current_block
            self.current_block += 1
            self.pending_response = self.blocks[block_idx]
        elif data == b'\x52\x03':
            assert self.chip.write_format is WriteFormat.FORMAT_1
            assert self.state is State.ERASING
            self.state = State.NONE
            for idx in range(0x10):
                self.blocks[self.current_sector * 0x10 + idx] = self.EMPTY_BLOCK
            self.pending_response = b'\x03'
        elif data == b'\x52\x04':
            assert self.chip.write_format is WriteFormat.FORMAT_2
            assert self.state is State.WRITING
            for idx in range(0x10):
                self.blocks[self.current_sector * 0x10 + idx] = self.EMPTY_BLOCK
            self.pending_response = b'\x04'
        elif data == b'\x2A\x03':
            assert self.chip.write_format is WriteFormat.FORMAT_1
            assert self.current_sector >= 0
            assert 0 <= self.current_block < 0x10
            assert self.state is State.WRITING
            assert not self.writing
            self.writing = True
        elif data == b'\x2A\x04':
            assert self.chip.write_format is WriteFormat.FORMAT_2
            assert self.current_sector >= 0
            assert 0 <= self.current_block < 0x10
            assert self.state is State.WRITING
            assert not self.writing
            self.writing = True
        else:
            assert False, data

    def _has_card(self) -> bytes:
        return b'\x00'


class UsbHandleMockNoCard(UsbHandleMock):
    def __init__(self, g2_orange: bool):
        super().__init__(0, ChipConfig(0, 0, WriteFormat.FORMAT_1), g2_orange)

    def _has_card(self) -> bytes:
        return b'\x01'


@pytest.mark.parametrize("g2_orange", [False, True])
def test_init_no_card(g2_orange):
    mock = UsbHandleMockNoCard(g2_orange)

    device = SkyboundDevice(mock)
    with pytest.raises(ProgrammingException, match="Card is missing"):
        device.init_data_card()


@pytest.mark.parametrize("g2_orange", [False, True])
@pytest.mark.parametrize(["chip", "n_chips", "name"], SUPPORTED_CARDS)
def test_init_card(g2_orange, chip, n_chips, name):
    mock = UsbHandleMock(n_chips, chip, g2_orange)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.sectors_per_chip == chip.sectors
    assert device.get_card_name() == name


@pytest.mark.parametrize("g2_orange", [False, True])
def test_init_errors(g2_orange):
    # No chips
    mock = UsbHandleMock(0, FAKE_CHIP, g2_orange)

    device = SkyboundDevice(mock)
    with pytest.raises(ProgrammingException, match="Unsupported"):
        device.init_data_card()

    # One chip (not supported, even if it's a real chip)
    mock = UsbHandleMock(1, CHIP_AMD_2MB, g2_orange)

    device = SkyboundDevice(mock)
    with pytest.raises(ProgrammingException, match="Unknown"):
        device.init_data_card()

    # Four chips, but unknown ID
    mock = UsbHandleMock(4, FAKE_CHIP, g2_orange)

    device = SkyboundDevice(mock)
    with pytest.raises(ProgrammingException, match="Unknown"):
        device.init_data_card()


@pytest.mark.parametrize(["chip", "n_chips", "name"], SUPPORTED_CARDS)
def test_simple_read(chip, n_chips, name):
    mock = UsbHandleMock(n_chips, chip, True)

    device = SkyboundDevice(mock)
    device.init_data_card()

    block1 = b"a" * 0x1000
    block2 = b"b" * 0x1000

    mock.blocks[0x01] = block1
    mock.blocks[0x40] = block2

    device.before_read()
    device.select_sector(0)
    assert device.read_block() == mock.EMPTY_BLOCK
    assert device.read_block() == block1

    device.select_sector(4)
    assert device.read_block() == block2
    assert device.read_block() == mock.EMPTY_BLOCK


@pytest.mark.parametrize(["chip", "n_chips", "name"], SUPPORTED_CARDS)
def test_simple_write(chip, n_chips, name):
    mock = UsbHandleMock(n_chips, chip, True)

    device = SkyboundDevice(mock)
    device.init_data_card()

    block1 = b"a" * 0x1000
    block2 = b"b" * 0x1000

    device.before_write()
    device.select_sector(3)
    device.write_block(block1)
    device.write_block(block2)

    assert mock.blocks[0x30] == block1
    assert mock.blocks[0x31] == block2


@pytest.mark.parametrize(["chip", "n_chips", "name"], SUPPORTED_CARDS)
def test_read_write_erase(chip, n_chips, name):
    mock = UsbHandleMock(n_chips, chip, True)

    device = SkyboundDevice(mock)
    device.init_data_card()

    block1 = b"a" * 0x1000
    block2 = b"b" * 0x1000

    device.before_write()
    device.select_sector(3)
    device.write_block(block1)
    device.write_block(block1)

    device.select_sector(3)
    device.erase_sector()

    device.before_write()
    device.select_sector(3)
    device.write_block(block2)

    device.before_read()
    device.select_sector(3)
    assert device.read_block() == block2
    assert device.read_block() == mock.EMPTY_BLOCK


def test_write_16mb():
    mock = UsbHandleMock(4, CHIP_AMD_4MB_SILVER, False)

    device = SkyboundDevice(mock)
    device.init_data_card()

    device.before_write()
    blocks = []
    for i in range(0x1000):
        if i % 16 == 0:
            device.select_sector(i // 16)

        # Fake but slightly different data for each block
        block = bytes([i % 19] * 0x1000)
        device.write_block(block)

        blocks.append(block)

    assert mock.blocks == blocks
