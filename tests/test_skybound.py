from typing import Optional

import pytest

from jdmtool.skybound import SkyboundDevice, SkyboundException

class UsbHandleMock:
    pending_response: Optional[bytes]
    led: bool
    page: int

    def __init__(self, iid: int, n_chips: int):
        self.pending_response = None
        self.iid = iid
        self.n_chips = n_chips
        self.led = False
        self.page = -1

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

        if data == b'\x18':
            self.pending_response = self.has_card()
        elif data.startswith(b'\x30\x00\x00'):
            assert len(data) == 5
            self.page = int.from_bytes(data[3:], 'little')
        elif data == b'\x40':
            pass
        elif data == b'\x50\x04':
            assert self.page in [0x00E0, 0x0160, 0x01A0, 0x01C0]
            self.pending_response = self.get_iid()
        else:
            assert False

    def has_card(self) -> bytes:
        return b'\x00'

    def get_iid(self) -> bytes:
        chip_idx = SkyboundDevice.MEMORY_OFFSETS.index(self.page) // 2
        if chip_idx < self.n_chips:
            iid = self.iid
        else:
            iid = 0x90009000
        return iid.to_bytes(4, 'little')


class UsbHandleMockNoCard(UsbHandleMock):
    def __init__(self):
        super().__init__(0, 0)

    def has_card(self) -> bytes:
        return b'\x01'


def test_no_card():
    mock = UsbHandleMockNoCard()

    device = SkyboundDevice(mock)
    with pytest.raises(SkyboundException, match="Card is missing"):
        device.init_data_card()


def test_2mb():
    mock = UsbHandleMock(0x8900a200, 1)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.memory_layout == device.MEMORY_LAYOUT_2MB
    assert device.card_name == '2MB'


def test_4mb():
    mock = UsbHandleMock(0x0100ad00, 2)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.memory_layout == device.MEMORY_LAYOUT_4MB
    assert device.card_name == '4MB'


def test_6mb():
    mock = UsbHandleMock(0x0100ad00, 3)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.memory_layout == device.MEMORY_LAYOUT_6MB
    assert device.card_name == '6MB'


def test_8mb():
    mock = UsbHandleMock(0x0100ad00, 4)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.memory_layout == device.MEMORY_LAYOUT_8MB
    assert device.card_name == '8MB'


def test_16mb():
    mock = UsbHandleMock(0x01004100, 4)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.memory_layout == device.MEMORY_LAYOUT_16MB
    assert device.card_name == '16MB WAAS (silver)'


    mock = UsbHandleMock(0x89007E00, 4)

    device = SkyboundDevice(mock)
    device.init_data_card()

    assert device.memory_layout == device.MEMORY_LAYOUT_16MB
    assert device.card_name == '16MB WAAS (orange)'


def test_errors():
    mock = UsbHandleMock(0x90009000, 4)

    device = SkyboundDevice(mock)
    with pytest.raises(SkyboundException, match="Unsupported"):
        device.init_data_card()

    mock = UsbHandleMock(0x0100ad00, 1)

    device = SkyboundDevice(mock)
    with pytest.raises(SkyboundException, match="Unexpected"):
        device.init_data_card()

    mock = UsbHandleMock(0x12345678, 4)

    device = SkyboundDevice(mock)
    with pytest.raises(SkyboundException, match="Unknown"):
        device.init_data_card()
