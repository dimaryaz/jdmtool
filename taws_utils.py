
OFFSET_SERIAL = 0x01f6

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


def verifyBlockCrc(data, footer):
    if len(data) == BLOCK_SIZE_256:
        block_crc = datablock_checksum_pagesize2048(data, footer[:-4])
        footer_block_crc = int.from_bytes(footer[-4:], 'little')
    else:
        block_crc = datablock_checksum_pagesize512(data, footer[:-2])
        footer_block_crc = int.from_bytes(footer[-2:], 'little')

    if block_crc != footer_block_crc:
        raise ValueError(f'Block failed the block checksum. {block_crc:02x} vs. {footer.hex()}')

    if len(data) == BLOCK_SIZE_128:
        crc = crc16_checksum(data, 0xFFFF)
        crc = crc16_checksum(footer[:-2], crc)
        if crc != 0:
            raise ValueError(f'Block failed the crc16 checksum. {crc:02x} vs. ' + ','.join([hex(x) for x in footer]))


SECTOR_SIZE = 0x10800


def translate_sector(bad_sectors: list[int], sector: int) -> int:
    for bad_sector in bad_sectors:
        if bad_sector > sector:
            break
        sector += 1
    return sector


def parse_serial(header: bytes) -> int:
    return int.from_bytes(header[OFFSET_SERIAL:OFFSET_SERIAL+4], 'little')


def write_serial(header: bytes, serial: int) -> bytes:
    return header[:OFFSET_SERIAL] + serial.to_bytes(4, 'little') + header[OFFSET_SERIAL+4:]


def parse_bad_sectors(xblk: bytes, block_size: int) -> list[int]:
    bb_count = int.from_bytes(xblk[6:8], 'little')

    bad_sectors: list[int] = []
    for i in range(8, 8 + bb_count * 2, 2):
        blk_id = int.from_bytes(xblk[i:i+2], 'little')
        if block_size == BLOCK_SIZE_256:
            bad_sectors.append(blk_id * 2)
            bad_sectors.append(blk_id * 2 + 1)
        else:
            assert blk_id % 4 == 0, blk_id
            bad_sectors.append(blk_id // 4)

    return bad_sectors


def guess_block_footer_size(sector_count: int) -> tuple[int, int]:
    if sector_count == 0x1000:
        return BLOCK_SIZE_256, FOOTER_SIZE_256
    elif sector_count == 0x7C1:
        return BLOCK_SIZE_128, FOOTER_SIZE_128
    else:
        raise ValueError(f"Unexpected number of sectors: {sector_count}")


def create_footer(data: bytes, current_idx: int, footer_size: int) -> bytes:
    footer = current_idx.to_bytes(4, 'little').ljust(footer_size - 4, b'\x00')
    if footer_size == FOOTER_SIZE_128:
        from fastcrc import crc16
        footer += crc16.mcrf4xx(footer).to_bytes(2, 'little')
        footer += datablock_checksum_pagesize512(data, footer).to_bytes(2, 'little')
    else:
        footer += datablock_checksum_pagesize2048(data, footer).to_bytes(4, 'little')
    assert len(footer) == footer_size, len(footer)
    return footer
