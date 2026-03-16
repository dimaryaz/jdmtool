#!/usr/bin/env python3

import os
import sys


from taws_utils import SECTOR_SIZE, guess_block_footer_size, parse_bad_sectors, translate_sector, create_footer


def main(argv):
    if len(argv) != 4:
        print(f"Usage: {argv[0]} taws_image.bin sector database.bin")
        return 1

    _, taws_image, sector_str, database = argv
    starting_logical_sector = int(sector_str)

    taws_image_size = os.stat(taws_image).st_size
    taws_image_sectors = taws_image_size // SECTOR_SIZE

    block_size, footer_size = guess_block_footer_size(taws_image_sectors)

    assert SECTOR_SIZE % (block_size + footer_size) == 0
    blocks_per_sector = SECTOR_SIZE // (block_size + footer_size)

    with open(database, 'rb') as fd_in, open(taws_image, 'r+b') as fd_out:
        fd_out.seek(block_size + footer_size)
        xblk = fd_out.read(block_size)
        bad_sectors = parse_bad_sectors(xblk, block_size)

        print("Bad sectors:", bad_sectors)

        file_size = os.fstat(fd_in.fileno()).st_size
        block_count = -(-file_size // (block_size))

        current_idx = -1

        for input_block_idx in range(block_count):
            if input_block_idx % blocks_per_sector == 0:
                logical_sector = starting_logical_sector + input_block_idx // blocks_per_sector
                physical_sector = translate_sector(bad_sectors, logical_sector)
                if physical_sector > taws_image_sectors:
                    raise ValueError("Ran out of space!")

                current_idx = physical_sector * blocks_per_sector
                fd_out.seek(physical_sector * SECTOR_SIZE)

            data = fd_in.read(block_size).ljust(block_size, b'\xff')
            fd_out.write(data)

            footer = create_footer(data, current_idx, footer_size)
            fd_out.write(footer)

            current_idx += 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
