#!/usr/bin/env python3

import os
import sys


from taws_utils import SECTOR_SIZE, guess_block_footer_size, create_footer, parse_serial, write_serial


def main(argv):
    if len(argv) != 3:
        print(f"Usage: {argv[0]} physical_image.img serial_hex")
        return 1

    _, physical_image, serial_str = argv

    new_serial = int(serial_str, 16)

    taws_image_size = os.stat(physical_image).st_size
    taws_image_sectors = taws_image_size // SECTOR_SIZE

    block_size, footer_size = guess_block_footer_size(taws_image_sectors)

    with open(physical_image, 'r+b') as fd_out:
        old_header = fd_out.read(block_size)
        old_serial = parse_serial(old_header)
        print(f'Old serial: {old_serial:08x}')
        print(f'New serial: {new_serial:08x}')

        new_header = write_serial(old_header, new_serial)

        fd_out.seek(0)
        fd_out.write(new_header)
        new_header_footer = create_footer(new_header, 0, footer_size)
        fd_out.write(new_header_footer)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
