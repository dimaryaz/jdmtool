import usb1


class GarminProgrammerException(Exception):
    pass


class GarminProgrammerDevice():
    VID = 0x0E39
    PID = 0x1250

    WRITE_ENDPOINT = 0x02
    READ_ENDPOINT = 0x81

    TIMEOUT = 3000

    METADATA_PAGE = 0x03DF

    DATA_PAGES = (
        list(range(0x00E0, 0x0100)) +
        list(range(0x02E0, 0x0300)) +
        list(range(0x0160, 0x0180)) +
        list(range(0x0360, 0x0380)) +
        list(range(0x01A0, 0x01C0)) +
        list(range(0x03A0, 0x03C0)) +
        list(range(0x01C0, 0x01E0)) +
        list(range(0x03C0, 0x03E0))
    )

    def __init__(self, handle: usb1.USBDeviceHandle) -> None:
        self.handle = handle

    def write(self, data):
        self.handle.bulkWrite(self.WRITE_ENDPOINT, data, self.TIMEOUT)

    def read(self, length):
        return self.handle.bulkRead(self.READ_ENDPOINT, length, self.TIMEOUT)

    def control_read(self, bRequestType, bRequest, wValue, wIndex, wLength):
        return self.handle.controlRead(bRequestType, bRequest, wValue, wIndex, wLength, self.TIMEOUT)

    def init(self):
        buf = self.control_read(0x80, 0x06, 0x0100, 0x0000, 18)
        if buf != b"\x12\x01\x10\x01\xFF\x83\xFF\x40\x39\x0E\x50\x12\x00\x00\x00\x00\x00\x01":
            raise GarminProgrammerException("Unexpected response")
        buf = self.control_read(0x80, 0x06, 0x0200, 0x0000, 9)
        if buf != b"\x09\x02\x20\x00\x01\x01\x00\x80\x0F":
            raise GarminProgrammerException("Unexpected response")
        buf = self.control_read(0x80, 0x06, 0x0200, 0x0000, 32)
        if buf != (
            b"\x09\x02\x20\x00\x01\x01\x00\x80\x0F\x09\x04\x00\x00\x02\x00\x00"
            b"\x00\x00\x07\x05\x81\x02\x40\x00\x05\x07\x05\x02\x02\x40\x00\x05"
        ):
            raise GarminProgrammerException("Unexpected response")

    def set_led(self, on):
        if on:
            self.write(b'\x12')
        else:
            self.write(b'\x13')

    def has_card(self):
        self.write(b"\x18")
        buf = self.read(0x0040)
        if buf == b"\x00":
            return True
        elif buf == b"\x01":
            return False
        else:
            raise GarminProgrammerException(f"Unexpected response: {buf}")

    def get_version(self):
        self.write(b"\x60")
        return self.read(0x0040).decode()

    def get_unknown(self): # TODO: what is this?
        self.write(b"\x50\x03")
        buf = self.read(0x0040)
        return int.from_bytes(buf, 'little')

    def get_iid(self):
        self.write(b"\x50\x04")
        buf = self.read(0x0040)
        return int.from_bytes(buf, 'little')

    def read_block(self):
        self.write(b"\x28")
        return self.read(0x1000)

    def write_block(self, data: bytes):
        if len(data) != 0x1000:
            raise ValueError("Data must be 4096 bytes")

        self.write(b"\x2A\x04")
        self.write(data)

        buf = self.read(0x0040)
        if buf[0] != data[-1] or buf[1:] != b"\x00\x00\x00":
            raise GarminProgrammerException(f"Unexpected response: {buf}")

    def select_page(self, page_id: int):
        if not (0x0000 <= page_id <= 0xFFFF):
            raise ValueError("Invalid page ID")
        self.write(b"\x30\x00\x00" + page_id.to_bytes(2, 'little'))

    def erase_page(self):
        self.write(b"\x52\x04")
        buf = self.read(0x0040)
        if buf != b"\x04":
            raise GarminProgrammerException(f"Unexpected response: {buf}")

    def before_read(self):
        # It's not clear that this does anything, but JDM seems to send it
        # before reading anything, so do the same thing.
        self.write(b"\x40")

    def before_write(self):
        # Same as above.
        self.write(b"\x42")
