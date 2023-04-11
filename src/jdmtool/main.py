#!/usr/bin/env python3

import argparse 
from functools import wraps
from getpass import getpass
import os
import tqdm
import usb1
import sys


from .device import GarminProgrammerDevice, GarminProgrammerException
from .downloader import Downloader, DownloaderException


DB_MAGIC = (
    b'\xeb<\x90GARMIN10\x00\x02\x08\x01\x00\x01\x00\x02\x00\x80\xf0\x10\x00?\x00\xff\x00?\x00\x00\x00'
    b'\x00\x00\x00\x00\x00\x00)\x02\x11\x00\x00GARMIN AT  FAT16   \x00\x00'
)

MAX_SIZE = len(GarminProgrammerDevice.DATA_PAGES) * 16 * 0x1000


DETAILED_INFO_MAP = [
    ("Aircraft Manufacturer", "./oracle_aircraft_manufacturer"),
    ("Aircraft Model", "./oracle_aircraft_model"),
    ("Aircraft Tail Number", "./oracle_aircraft_tail_number"),
    (None, None),
    ("Avionics", "./avionics"),
    ("Coverage", "./coverage_desc"),
    ("Service Type", "./service_type"),
    ("Service Code", "./service_code"),
    ("Service ID", "./unique_service_id"),
    ("Service Renewal Date", "./service_renewal_date"),
    (None, None),
    ("Version", "./display_version"),
    ("Version Start Date", "./version_start_date"),
    ("Version End Date", "./version_end_date"),
    (None, None),
    ("Next Version", "./next_display_version"),
    ("Next Version Available Date", "./next_version_avail_date"),
    ("Next Version Start Date", "./next_version_start_date"),
    (None, None),
    ("File Name", "./filename"),
    ("File Size", "./file_size"),
    ("File CRC32", "./file_crc"),
    ("Serial Number", "./serial_number"),
    ("System ID", "./avionics_id"),
]


def with_usb(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        with usb1.USBContext() as usbcontext:
            try:
                usbdev = usbcontext.getByVendorIDAndProductID(GarminProgrammerDevice.VID, GarminProgrammerDevice.PID)
                if usbdev is None:
                    raise GarminProgrammerException("Device not found")

                print(f"Found device: {usbdev}")
                handle = usbdev.open()
            except usb1.USBError as ex:
                raise GarminProgrammerException(f"Could not open: {ex}")

            with handle.claimInterface(0):
                handle.resetDevice()
                dev = GarminProgrammerDevice(handle)
                dev.init()
                f(dev, *args, **kwargs)

    return wrapper


def _loop_helper(dev, i):
    dev.set_led(i % 2 == 0)
    if not dev.has_card():
        dev.set_led(False)
        raise GarminProgrammerException("Card not found!")


def cmd_login() -> None:
    downloader = Downloader()

    username = input("Username: ")
    password = getpass("Password: ")

    downloader.login(username, password)
    print("Logged in successfully")

def cmd_refresh() -> None:
    downloader = Downloader()
    downloader.refresh()
    print("Success")

def cmd_list() -> None:
    downloader = Downloader()
    services = downloader.get_services()

    downloads_dir = downloader.get_downloads_dir()

    row_format = "{:>2}  {:<70}  {:<8}  {:<10}  {:<10}  {:<10}"

    print(row_format.format("ID", "Name", "Version", "Start Date", "End Date", "Downloaded"))
    for idx, service in enumerate(services):
        name: str = service.findtext('./short_desc')
        version: str = service.findtext('./display_version')
        start_date: str = service.findtext('./version_start_date').split()[0]
        end_date: str = service.findtext('./version_end_date').split()[0]
        filename: str = service.findtext('./filename')

        downloaded = (downloads_dir / filename).exists()

        print(row_format.format(idx, name, version, start_date, end_date, 'Y' if downloaded else ''))

def cmd_info(id) -> None:
    downloader = Downloader()

    services = downloader.get_services()
    if id < 0 or id >= len(services):
        raise DownloaderException("Invalid download ID")

    service = services[id]

    for desc, path in DETAILED_INFO_MAP:
        if desc is None:
            print()
        else:
            value = service.findtext(path) or ''
            print(f'{desc+":":<30}{value}')

def cmd_download(id) -> None:
    downloader = Downloader()

    services = downloader.get_services()
    if id < 0 or id >= len(services):
        raise DownloaderException("Invalid download ID")

    service = services[id]

    size = int(service.findtext('./file_size'))

    with tqdm.tqdm(desc="Downloading", total=size, unit='B', unit_scale=True) as t:
        path = downloader.download(service, t.update)

    print(f"Downloaded to {path}")

@with_usb
def cmd_transfer(dev, id) -> None:
    downloader = Downloader()

    services = downloader.get_services()
    if id < 0 or id >= len(services):
        raise DownloaderException("Invalid download ID")

    service = services[id]

    filename: str = service.findtext('./filename')
    version = service.findtext('./version')
    unique_service_id = service.findtext('./unique_service_id')

    path = downloader.get_downloads_dir() / filename
    if not path.exists():
        raise DownloaderException("Need to download it first")

    new_metadata = f'{{{version}~{unique_service_id}}}'  # E.g. {2303~12345678}

    prompt = input(f"Transfer {path} to the data card? (y/n) ")
    if prompt.lower() != 'y':
        raise DownloaderException("Cancelled")

    _clear_metadata(dev)
    _write_database(dev, path)

    print(f"Writing new metadata: {new_metadata}")
    _write_metadata(dev, new_metadata)

    print("Done")

@with_usb
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

@with_usb
def cmd_read_metadata(dev: GarminProgrammerDevice) -> None:
    dev.before_read()
    dev.select_page(GarminProgrammerDevice.METADATA_PAGE)
    blocks = []
    for i in range(16):
        _loop_helper(dev, i)
        blocks.append(dev.read_block())
    value = b''.join(blocks).rstrip(b"\xFF").decode()
    print(f"Database metadata: {value}")

def _clear_metadata(dev: GarminProgrammerDevice) -> None:
    dev.before_write()
    dev.select_page(GarminProgrammerDevice.METADATA_PAGE)
    dev.erase_page()

def _write_metadata(dev: GarminProgrammerDevice, metadata: str) -> None:
    dev.before_write()
    page = metadata.encode().ljust(0x10000, b'\xFF')

    dev.select_page(GarminProgrammerDevice.METADATA_PAGE)

    # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
    # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
    dev.erase_page()

    for i in range(16):
        _loop_helper(dev, i)

        block = page[i*0x1000:(i+1)*0x1000]

        dev.write_block(block)

@with_usb
def cmd_write_metadata(dev: GarminProgrammerDevice, metadata: str) -> None:
    _write_metadata(dev, metadata)
    print("Done")

@with_usb
def cmd_read_database(dev: GarminProgrammerDevice, path: str) -> None:
    with open(path, 'w+b') as fd:
        with tqdm.tqdm(desc="Reading the database", total=MAX_SIZE, unit='B', unit_scale=True) as t:
            dev.before_read()
            for i in range(len(GarminProgrammerDevice.DATA_PAGES) * 16):
                _loop_helper(dev, i)

                if i % 256 == 0:
                    dev.select_page(GarminProgrammerDevice.DATA_PAGES[i // 16])

                block = dev.read_block()

                if block == b'\xFF' * 0x1000:
                    break

                fd.write(block)
                t.update(len(block))

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

def _write_database(dev: GarminProgrammerDevice, path: str) -> None:
    with open(path, 'rb') as fd:
        size = os.fstat(fd.fileno()).st_size

        if size > MAX_SIZE:
            raise GarminProgrammerException(f"Database file is too big! The maximum size is {MAX_SIZE}.")

        pages_required = min(size // 16 // 0x1000 + 3, len(GarminProgrammerDevice.DATA_PAGES))
        page_ids = GarminProgrammerDevice.DATA_PAGES[:pages_required]
        total_size = pages_required * 16 * 0x1000

        magic = fd.read(64)
        if magic != DB_MAGIC:
            raise GarminProgrammerException(f"Does not look like a Garmin database file.")

        fd.seek(0)

        dev.before_write()

        # Data card can only write by changing 1s to 0s (effectively doing a bit-wise AND with
        # the existing contents), so all data needs to be "erased" first to reset everything to 1s.
        with tqdm.tqdm(desc="Erasing the database", total=total_size, unit='B', unit_scale=True) as t:
            for i, page_id in enumerate(page_ids):
                _loop_helper(dev, i)
                dev.select_page(page_id)
                dev.erase_page()
                t.update(16 * 0x1000)

        with tqdm.tqdm(desc="Writing the database", total=total_size, unit='B', unit_scale=True) as t:
            for i in range(pages_required * 16):
                block = fd.read(0x1000).ljust(0x1000, b'\xFF')

                _loop_helper(dev, i)

                if i % 256 == 0:
                    dev.select_page(page_ids[i // 16])

                dev.write_block(block)
                t.update(len(block))

        fd.seek(0)

        with tqdm.tqdm(desc="Verifying the database", total=total_size, unit='B', unit_scale=True) as t:
            dev.before_read()
            for i in range(pages_required * 16):
                file_block = fd.read(0x1000).ljust(0x1000, b'\xFF')

                _loop_helper(dev, i)

                if i % 256 == 0:
                    dev.select_page(page_ids[i // 16])

                card_block = dev.read_block()

                if card_block != file_block:
                    raise GarminProgrammerException(f"Verification failed! Block {i} is incorrect.")

                t.update(len(file_block))

@with_usb
def cmd_write_database(dev: GarminProgrammerDevice, path: str) -> None:
    prompt = input(f"Transfer {path} to the data card? (y/n) ")
    if prompt.lower() != 'y':
        raise DownloaderException("Cancelled")

    try:
        _write_database(dev, path)
    except IOError as ex:
        raise GarminProgrammerException(f"Could not read the database file: {ex}")

    print("Done")

def main():
    parser = argparse.ArgumentParser(description="Program a Garmin data card")

    subparsers = parser.add_subparsers(metavar="<command>")
    subparsers.required = True

    login_p = subparsers.add_parser(
        "login",
        help="Log into Jeppesen",
    )
    login_p.set_defaults(func=cmd_login)

    refresh_p = subparsers.add_parser(
        "refresh",
        help="Refresh the list available downloads",
    )
    refresh_p.set_defaults(func=cmd_refresh)

    list_p = subparsers.add_parser(
        "list",
        help="Show the (cached) list of available downloads",
    )
    list_p.set_defaults(func=cmd_list)

    info_p = subparsers.add_parser(
        "info",
        help="Show detailed info about the download",
    )
    info_p.add_argument(
        "id",
        help="ID of the download",
        type=int,
    )
    info_p.set_defaults(func=cmd_info)

    download_p = subparsers.add_parser(
        "download",
        help="Download the data",
    )
    download_p.add_argument(
        "id",
        help="ID of the download",
        type=int,
    )
    download_p.set_defaults(func=cmd_download)

    transfer_p = subparsers.add_parser(
        "transfer",
        help="Transfer the downloaded database to the data card",
    )
    transfer_p.add_argument(
        "id",
        help="ID of the download",
        type=int,
    )
    transfer_p.set_defaults(func=cmd_transfer)

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

    try:
        func(**kwargs)
    except DownloaderException as ex:
        print(ex)
        return 1
    except GarminProgrammerException as ex:
        print(ex)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
