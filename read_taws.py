#!/usr/bin/env python3

import os
import sys


from taws_utils import SECTOR_SIZE, guess_block_footer_size, parse_bad_sectors, translate_sector


def main(argv):
    if len(argv) != 3:
        print(f"Usage: {argv[0]} taws_physical_input.img logical_output.img")
        return 1

    _, physical_input, logical_output = argv

    taws_image_size = os.stat(physical_input).st_size
    taws_image_sectors = taws_image_size // SECTOR_SIZE

    block_size, footer_size = guess_block_footer_size(taws_image_sectors)

    assert SECTOR_SIZE % (block_size + footer_size) == 0
    blocks_per_sector = SECTOR_SIZE // (block_size + footer_size)

    with open(physical_input, 'rb') as fd_in, open(logical_output, 'wb') as fd_out:
        fd_in.seek(block_size + footer_size)
        xblk = fd_in.read(block_size)
        bad_sectors = parse_bad_sectors(xblk, block_size)

        print("Bad sectors:", bad_sectors)

        good_sector_count = taws_image_sectors - len(bad_sectors)

        for logical_sector in range(good_sector_count):
            physical_sector = translate_sector(bad_sectors, logical_sector)
            assert physical_sector <= taws_image_sectors, (physical_sector, taws_image_sectors)
            fd_in.seek(physical_sector * SECTOR_SIZE)

            for sector_block_idx in range(blocks_per_sector):
                current_idx = physical_sector * blocks_per_sector + sector_block_idx

                data = fd_in.read(block_size)
                footer = fd_in.read(footer_size)
                fd_out.write(data)

                idx = int.from_bytes(footer[0:4], 'little')
                if idx == 0xffffffff:
                    continue

                if idx & 0x00ffffff != current_idx:
                    raise ValueError(f"Unexpected idx: {idx:08x} (expected {current_idx:08x})")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
