#!/usr/bin/env python3

import os
import sys

from taws_utils import SECTOR_SIZE, guess_block_footer_size, parse_serial, parse_bad_sectors, verifyBlockCrc


def main(argv):
    if len(argv) != 2:
        print(f"Usage: {argv[0]} taws_image.bin")
        return 1

    _, taws_image = argv

    taws_image_size = os.stat(taws_image).st_size
    taws_image_sectors = taws_image_size // SECTOR_SIZE

    block_size, footer_size = guess_block_footer_size(taws_image_sectors)

    assert SECTOR_SIZE % (block_size + footer_size) == 0
    blocks_per_sector = SECTOR_SIZE // (block_size + footer_size)

    with open(taws_image, 'rb') as fd:
        header = fd.read(block_size)
        serial = parse_serial(header)
        print(f'Serial: {serial:08x}')

        fd.seek(block_size + footer_size)
        xblk = fd.read(block_size)
        bad_sectors = parse_bad_sectors(xblk, block_size)

        print("Bad sectors:", bad_sectors)

        for sector in range(taws_image_sectors):
            if sector in bad_sectors:
                continue

            fd.seek(sector * SECTOR_SIZE)

            for sector_block_idx in range(blocks_per_sector):
                data = fd.read(block_size)
                footer = fd.read(footer_size)

                idx = int.from_bytes(footer[:4], 'little')
                if idx == 0xffffffff:
                    continue

                if idx & 0x00ffffff != sector * blocks_per_sector + sector_block_idx:
                    raise ValueError(f"Unexpected idx: {idx}")

                verifyBlockCrc(data, footer)

if __name__ == "__main__":
    sys.exit(main(sys.argv))
