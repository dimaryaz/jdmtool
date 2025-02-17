#!/usr/bin/env python3

from __future__ import annotations

import sys

from usb1 import USBContext

from jdmtool.usb_common import BasicUsbDevice, open_usb_device
from jdmtool.ngt import (
    add_checksum,
    decode_packet,
    encode_packet,
    print_message_info,
    remove_checksum,
    wrap_data_block,
    wrap_message,
    unwrap_message,
)


NGT_VID_PID = (0x2A4F, 0x0100)

MAX_PACKET_SIZE = 0x0400


class NGTDevice(BasicUsbDevice):
    READ_ENDPOINT = 0x81
    WRITE_ENDPOINT = 0x01

    def write_message(self, msg_type: int, msg_data: bytes) -> None:
        msg = wrap_message(msg_type, msg_data)
        chunk = add_checksum(msg)
        packet = encode_packet([chunk])
        self.bulk_write(packet)


# This is just for my testing.
class MockNGTDevice(NGTDevice):
    def __init__(self):
        super().__init__(None)

    def bulk_read(self, length):
        return b""

    def bulk_write(self, data):
        print("Writing:")
        chunks = decode_packet(data)
        for chunk in chunks:
            msg = remove_checksum(chunk)
            msg_type, msg_data = unwrap_message(msg)
            print_message_info(msg_type, msg_data)
        print("Done")


def run_command(dev: NGTDevice, cmd: str) -> None:
    if cmd == 'noop':
        # Do nothing - just read the data back from the device.
        pass
    elif cmd == "abcd":
        # The first message MPC writes: 0xABCD, with no content
        msg_type = 0xABCD
        msg_content = b""
        dev.write_message(msg_type, msg_content)
    elif cmd == "dcba1":
        # The second message MPC writes: 0xDCBA, with 8 bytes
        msg_type = 0xDCBA
        # They're different in flight mode and in maintenance mode.
        # The only real question is: are they specific to your device
        # (encode some kind of ID), or are they the same for everyone else?
        # If you're adventurous enough, try other bytes and see what happens.
        msg_content = b'\x7B\x3E\xE3\x26\x14\x59\x5D\x35'
        dev.write_message(msg_type, msg_content)
    elif cmd == "info":
        # Read hardware info?
        msg_type = 0x0031
        data_type = 0x7F000004
        data_content = b'\x00\x00\x00\x00\x00\x00\x00\x00'
        dev.write_message(msg_type, wrap_data_block(data_type, data_content))
    elif cmd == "maint":
        # Reboot into maintenance mode?
        msg_type = 0x0031
        data_type = 0x41000004
        data_content = b'\x02\x00\x00\x00\x00\x00\x00\x00'
        dev.write_message(msg_type, wrap_data_block(data_type, data_content))
    elif cmd == "dcba2":
        # Maintenance mode version of DCBA.
        msg_type = 0xDCBA
        msg_content = b'\x60\x57\x9F\x43\x3A\x46\x0E\x61'
        dev.write_message(msg_type, msg_content)
    elif cmd == "unknown1":
        msg_type = 0x0031
        data_type = 0x32000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "unknown2":
        msg_type = 0x0031
        data_type = 0x42000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "unknown3":
        msg_type = 0x0031
        data_type = 0x40000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "unknown4":
        msg_type = 0x0031
        data_type = 0x44000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "unknown5":
        msg_type = 0x0031
        data_type = 0x49000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "unknown6":
        msg_type = 0x0031
        data_type = 0x48000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "unknown7":
        msg_type = 0x0031
        data_type = 0x3B000002
        dev.write_message(msg_type, wrap_data_block(data_type, b''))
    elif cmd == "before_database":
        msg_type = 0x0031
        data_type = 0x4F000004
        data_content = b'\x01\x00\x00\x00\x00\x00\x00\x00'
        dev.write_message(msg_type, wrap_data_block(data_type, data_content))
    elif cmd == "database":
        # Send the database!
        with open("68.bin", "rb") as fd:
            idx = 1
            while True:
                chunk = fd.read(2000)
                if not chunk:
                    break

                if len(chunk) == 2000:
                    # regular block
                    data_type = 0x370001F8
                else:
                    # either means the block is <2000 bytes because it's last,
                    # or it's always used for the last block even if it's 2000 bytes.
                    data_type = 0x37000094

                data_content = idx.to_bytes(4, 'little') + chunk

                msg_type = 0x0031
                dev.write_message(msg_type, wrap_data_block(data_type, data_content))

                idx += 1
    elif cmd == "after_database":
        msg_type = 0x0031
        data_type = 0x38000004
        data_content = b'\x00\x00\x00\x00\x00\x00\x00\x00'
        dev.write_message(msg_type, wrap_data_block(data_type, data_content))
    elif cmd == "flight":
        # Reboot into flight mode?
        msg_type = 0x0031
        data_type = 0x41000004
        data_content = b'\x01\x00\x00\x00\x00\x00\x00\x00'
        dev.write_message(msg_type, wrap_data_block(data_type, data_content))
    else:
        print("Unknown command")
        return

    # Read 5 packets
    for idx in range(5):
        print(f"Reading packet {idx}...")
        packet = dev.bulk_read(MAX_PACKET_SIZE)
        chunks = decode_packet(packet)
        print(f"Received {len(chunks)} messages.")
        for chunk in chunks:
            msg = remove_checksum(chunk)
            msg_type, msg_data = unwrap_message(msg)
            print_message_info(msg_type, msg_data)
        print("")


def main(args) -> int:
    if len(args) != 2:
        print(f"Usage: {args[0]} command")
        return 1

    with USBContext() as usbcontext:
        usbdev = usbcontext.getByVendorIDAndProductID(NGT_VID_PID[0], NGT_VID_PID[1])
        if usbdev is None:
            print("Could not find an NGT9000 device!")
            return 2

        with open_usb_device(usbdev, reset=False) as dev:
            if args[1] == "reset":
                dev.resetDevice()
            else:
                ngt_dev = NGTDevice(dev)
                run_command(ngt_dev, args[1])

    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
