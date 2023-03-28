#!/usr/bin/env python3

import argparse 
import os
import usb1
import sys


from .device import GarminProgrammerDevice, GarminProgrammerException


DB_MAGIC = (
    b'\xeb<\x90GARMIN10\x00\x02\x08\x01\x00\x01\x00\x02\x00\x80\xf0\x10\x00?\x00\xff\x00?\x00\x00\x00'
    b'\x00\x00\x00\x00\x00\x00)\x02\x11\x00\x00GARMIN AT  FAT16   \x00\x00'
)


def cmd_detect(dev: GarminProgrammerDevice) -> None:
    version = dev.get_version()
    print(f"Firmware version: {version}")
    if dev.has_card():
        print("Card inserted:")
        iid = dev.get_iid()
        print(f"  IID: 0x{iid:x}")
        unknown = dev.get_unknown()
        print(f"  Unknown identifier: 0x{unknown:x}")
    else:
        print("No card")

def cmd_read_metadata(dev: GarminProgrammerDevice) -> None:
    dev.write(b'\x40')  # TODO: Is this needed?
    dev.select_page(GarminProgrammerDevice.METADATA_PAGE)
    blocks = []
    for i in range(16):
        dev.set_led(i % 2 == 0)
        dev.check_card()
        blocks.append(dev.read_block())
    value = b''.join(blocks).rstrip(b"\xFF").decode()
    print(f"Database metadata: {value}")

def cmd_write_metadata(dev: GarminProgrammerDevice, metadata: str) -> None:
    dev.write(b'\x42')  # TODO: Is this needed?
    page = metadata.encode().ljust(0x10000, b'\xFF')

    dev.select_page(GarminProgrammerDevice.METADATA_PAGE)

    # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
    # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
    dev.erase_page()

    for i in range(16):
        dev.set_led(i % 2 == 0)

        block = page[i*0x1000:(i+1)*0x1000]

        dev.check_card()
        dev.write_block(block)

    print("Done")

def cmd_read_database(dev: GarminProgrammerDevice, path: str) -> None:
    with open(path, 'w+b') as fd:
        print("Reading the database...")

        dev.write(b'\x40')  # TODO: Is this needed?
        for i in range(len(GarminProgrammerDevice.DATA_PAGES) * 16):
            dev.set_led(i % 2 == 0)

            dev.check_card()

            if i % 256 == 0:
                dev.select_page(GarminProgrammerDevice.DATA_PAGES[i // 16])

            block = dev.read_block()
            fd.write(block)

        # Garmin card has no concept of size of the data,
        # so we need to remove the trailing "\xFF"s.
        print("Truncating the file...")
        fd.seek(0, os.SEEK_END)
        pos = fd.tell()
        while pos > 0:
            pos -= 1024
            fd.seek(pos)
            block = fd.read(1024)
            if block != b'\xFF' * 1024:
                break
        fd.truncate()

    print("Done")

def cmd_write_database(dev: GarminProgrammerDevice, path: str) -> None:
    with open(path, 'rb') as fd:
        size = os.fstat(fd.fileno()).st_size

        max_size = len(GarminProgrammerDevice.DATA_PAGES) * 16 * 0x1000
        if size > max_size:
            raise GarminProgrammerException(f"Database file is too big! The maximum size is {max_size}.")

        magic = fd.read(64)
        if magic != DB_MAGIC:
            raise GarminProgrammerException(f"Does not look like a Garmin database file.")

        fd.seek(0)

        dev.write(b'\x42')  # TODO: Is this needed?

        # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
        # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
        print("Erasing the database...")
        for i, page_id in enumerate(GarminProgrammerDevice.DATA_PAGES):
            dev.set_led(i % 2 == 0)
            dev.check_card()
            dev.select_page(page_id)
            dev.erase_page()

        print("Writing the database...")
        for i in range(len(GarminProgrammerDevice.DATA_PAGES) * 16):
            chunk = fd.read(0x1000)
            chunk = chunk.ljust(0x1000, b'\xFF')

            dev.set_led(i % 2 == 0)

            dev.check_card()

            if i % 256 == 0:
                dev.select_page(GarminProgrammerDevice.DATA_PAGES[i // 16])

            dev.write_block(chunk)

    print("Done")

def main():
    parser = argparse.ArgumentParser(description="Program a Garmin data card")

    subparsers = parser.add_subparsers(metavar="<command>")
    subparsers.required = True

    detect_p = subparsers.add_parser(
        "detect",
        help="Detect a card programmer device",
    )
    detect_p.set_defaults(func=cmd_detect)

    read_metadata_p = subparsers.add_parser(
        "read-metadata",
        help="Read the database metadata",
    )
    read_metadata_p.set_defaults(func=cmd_read_metadata)

    write_metadata_p = subparsers.add_parser(
        "write-metadata",
        help="Write the database metadata",
    )
    write_metadata_p.add_argument(
        "metadata",
        help="Metadata string, e.g. {2303~12345678}",
    )
    write_metadata_p.set_defaults(func=cmd_write_metadata)

    read_database_p = subparsers.add_parser(
        "read-database",
        help="Read the database from the card and write to the file",
    )
    read_database_p.add_argument(
        "path",
        help="File to write the database to",
    )
    read_database_p.set_defaults(func=cmd_read_database)

    write_database_p = subparsers.add_parser(
        "write-database",
        help="Write the database to the card",
    )
    write_database_p.add_argument(
        "path",
        help="Database file, e.g. dgrw72_2302_742ae60e.bin",
    )
    write_database_p.set_defaults(func=cmd_write_database)

    args = parser.parse_args()

    kwargs = vars(args)
    func = kwargs.pop('func')

    with usb1.USBContext() as usbcontext:
        try:
            usbdev = usbcontext.getByVendorIDAndProductID(GarminProgrammerDevice.VID, GarminProgrammerDevice.PID)
            if usbdev is None:
                print("Device not found")
                return 1

            print(f"Found device: {usbdev}")
            handle = usbdev.open()
        except usb1.USBError as ex:
            print(f"Could not open: {ex}")
            return 1

        with handle.claimInterface(0):
            handle.resetDevice()
            try:
                dev = GarminProgrammerDevice(handle)
                dev.init()
                func(dev, **kwargs)
            except GarminProgrammerException as ex:
                print(ex)
                return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
