#!/usr/bin/env python3

import os
import sys


BLOCK_SIZE_128 = 0x200
FOOTER_SIZE_128 = 0x10
BLOCK_SIZE_256 = 0x800
FOOTER_SIZE_256 = 0x40

_datablock_lookup_table = (b'\x00\x01\x03\x02\x05\x04\x06\x07\x07\x06\x04\x05\x02\x03\x01\x00\x09\x08\x0A\x0B\x0C\x0D'
                           b'\x0F\x0E\x0E\x0F\x0D\x0C\x0B\x0A\x08\x09\x0B\x0A\x08\x09\x0E\x0F\x0D\x0C\x0C\x0D\x0F\x0E'
                           b'\x09\x08\x0A\x0B\x02\x03\x01\x00\x07\x06\x04\x05\x05\x04\x06\x07\x00\x01\x03\x02\x0D\x0C'
                           b'\x0E\x0F\x08\x09\x0B\x0A\x0A\x0B\x09\x08\x0F\x0E\x0C\x0D\x04\x05\x07\x06\x01\x00\x02\x03'
                           b'\x03\x02\x00\x01\x06\x07\x05\x04\x06\x07\x05\x04\x03\x02\x00\x01\x01\x00\x02\x03\x04\x05'
                           b'\x07\x06\x0F\x0E\x0C\x0D\x0A\x0B\x09\x08\x08\x09\x0B\x0A\x0D\x0C\x0E\x0F\x0F\x0E\x0C\x0D'
                           b'\x0A\x0B\x09\x08\x08\x09\x0B\x0A\x0D\x0C\x0E\x0F\x06\x07\x05\x04\x03\x02\x00\x01\x01\x00'
                           b'\x02\x03\x04\x05\x07\x06\x04\x05\x07\x06\x01\x00\x02\x03\x03\x02\x00\x01\x06\x07\x05\x04'
                           b'\x0D\x0C\x0E\x0F\x08\x09\x0B\x0A\x0A\x0B\x09\x08\x0F\x0E\x0C\x0D\x02\x03\x01\x00\x07\x06'
                           b'\x04\x05\x05\x04\x06\x07\x00\x01\x03\x02\x0B\x0A\x08\x09\x0E\x0F\x0D\x0C\x0C\x0D\x0F\x0E'
                           b'\x09\x08\x0A\x0B\x09\x08\x0A\x0B\x0C\x0D\x0F\x0E\x0E\x0F\x0D\x0C\x0B\x0A\x08\x09\x00\x01'
                           b'\x03\x02\x05\x04\x06\x07\x07\x06\x04\x05\x02\x03\x01\x00')


def datablock_checksum_pagesize512(datablock, footer):
    value = 0
    index = 0x600
    for d in footer:
        value ^= _datablock_lookup_table[d] << 0x1c
        if (_datablock_lookup_table[d] & 1) != 0:
            value = value ^ index
        index += 1

    index = 0xc00
    for d in datablock:
        value = value ^ _datablock_lookup_table[d] << 0x1c
        if (_datablock_lookup_table[d] & 1) != 0:
            value = value ^ index
        index += 1
    index = value << 4
    value = index | value >> 0x1c

    index = (index >> 8 << 24 | _datablock_lookup_table[(index >> 8 ^ value) >> 1 & 0xff]) & 0xffffff01
    return (index | (index ^ value)) & 0xffff


def datablock_checksum_pagesize2048(datablock, footer):
    crc = 0
    index = 0x6000000
    for d in footer:
        crc ^= _datablock_lookup_table[d]
        if (_datablock_lookup_table[d] & 1) != 0:
            crc = crc ^ index << 4
        index += 1

    index = 0xc000000
    for d in datablock:
        crc = crc ^ _datablock_lookup_table[d]
        if (_datablock_lookup_table[d] & 1) != 0:
            crc = crc ^ index << 4
        index += 1

    index = crc >> 0x10 ^ crc
    return _datablock_lookup_table[(index >> 9 ^ index >> 1) & 0xff] & 0x1 ^ crc


def crc16_checksum(data: bytes, value: int = 0xffff) -> int:
    from fastcrc import crc16
    return crc16.mcrf4xx(data, value)



SECTOR_SIZE = 0x10800

def main(argv):
    if len(argv) != 4:
        print(f"Usage: {argv[0]} taws_image.bin sector database.bin")
        return 1

    _, taws_image, sector_str, database = argv
    sector = int(sector_str)

    taws_image_size = os.stat(taws_image).st_size
    taws_image_sectors = taws_image_size // SECTOR_SIZE

    if taws_image_sectors == 0x1000:
        print("256MB")
        block_size = BLOCK_SIZE_256
        footer_size = FOOTER_SIZE_256
    elif taws_image_sectors == 0x500:
        print("128MB")
        from fastcrc import crc16
        block_size = BLOCK_SIZE_128
        footer_size = FOOTER_SIZE_128
    else:
        assert False

    assert SECTOR_SIZE % (block_size + footer_size) == 0
    blocks_per_sector = SECTOR_SIZE // (block_size + footer_size)

    starting_idx = sector * blocks_per_sector

    with open(database, 'rb') as fd_in, open(taws_image, 'r+b') as fd_out:
        fd_out.seek(sector * SECTOR_SIZE)

        file_size = os.fstat(fd_in.fileno()).st_size
        block_count = -(-file_size // (block_size))

        for idx in range(starting_idx, starting_idx + block_count):
            data = fd_in.read(block_size).ljust(block_size, b'\xff')
            fd_out.write(data)

            footer = idx.to_bytes(4, 'little').ljust(footer_size - 4, b'\x00')
            if block_size == BLOCK_SIZE_128:
                footer += crc16.mcrf4xx(footer).to_bytes(2, 'little')
                footer += datablock_checksum_pagesize512(data, footer).to_bytes(2, 'little')
            else:
                footer += datablock_checksum_pagesize2048(data, footer).to_bytes(4, 'little')

            assert len(footer) == footer_size, len(footer)
            fd_out.write(footer)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
