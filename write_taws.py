#!/usr/bin/env python3

import os
import sys


from taws_utils import OFFSET_SERIAL, SECTOR_SIZE, guess_block_footer_size, parse_bad_sectors, translate_sector, create_footer, parse_serial


def main(argv):
    if len(argv) != 3:
        print(f"Usage: {argv[0]} taws_physical_output.img logical_input.img")
        return 1

    _, physical_output, logical_input = argv

    taws_image_size = os.stat(physical_output).st_size
    taws_image_sectors = taws_image_size // SECTOR_SIZE

    block_size, footer_size = guess_block_footer_size(taws_image_sectors)

    assert SECTOR_SIZE % (block_size + footer_size) == 0
    blocks_per_sector = SECTOR_SIZE // (block_size + footer_size)

    with open(logical_input, 'rb') as fd_in, open(physical_output, 'r+b') as fd_out:
        old_header = fd_out.read(block_size)
        old_serial = parse_serial(old_header)
        print(f'Old serial: {old_serial:08x}')

        new_header = fd_in.read(block_size)
        new_serial = parse_serial(new_header)
        print(f'New serial: {new_serial:08x}')

        fd_out.seek(0)
        fd_out.write(new_header)
        new_header_footer = create_footer(new_header, 0, footer_size)
        fd_out.write(new_header_footer)

        print(fd_out.tell())
        print(block_size + footer_size)
        fd_out.seek(block_size + footer_size)
        print(fd_out.tell())
        xblk = fd_out.read(block_size)
        bad_sectors = parse_bad_sectors(xblk, block_size)

        print("Bad sectors:", bad_sectors)

        good_sector_count = taws_image_sectors - len(bad_sectors)

        fd_in.seek(block_size * blocks_per_sector)  # Skip the first *logical* sector

        for logical_sector in range(1, good_sector_count):
            physical_sector = translate_sector(bad_sectors, logical_sector)
            assert physical_sector <= taws_image_sectors, (physical_sector, taws_image_sectors)
            fd_out.seek(physical_sector * SECTOR_SIZE)

            for sector_block_idx in range(blocks_per_sector):
                current_idx = physical_sector * blocks_per_sector + sector_block_idx

                data = fd_in.read(block_size).ljust(block_size, b'\xff')
                fd_out.write(data)

                footer = create_footer(data, current_idx, footer_size)
                fd_out.write(footer)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
