from typing import List


CRC32Q_POLYNOMIAL = 0x814141AB

# Full lookup table can be found in:
# "objdump -s --start-address=0xA049C0 --stop-address=0xA04DC0 jdm.exe"
# Jeppesen Distribution Manager Version 3.14.0 (Build 60)
SFX_POLYNOMIAL = 0x04C11DB7


def _create_lookup_table(polynomial: int) -> List[int]:
    lookup_table: List[int] = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            if value & (1 << 31):
                value = ((value << 1) & 0xFFFFFFFF) ^ polynomial
            else:
                value <<= 1

        lookup_table.append(value)

    return lookup_table


_crc32q_lookup_table = _create_lookup_table(CRC32Q_POLYNOMIAL)
_sfx_lookup_table = _create_lookup_table(SFX_POLYNOMIAL)


def crc32q_checksum(data: bytes, value: int = 0) -> int:
    for b in data:
        index = b ^ (value >> 24)
        value = _crc32q_lookup_table[index] ^ ((value & 0x00FFFFFF) << 8)
    return value


def sfx_checksum(data: bytes, value: int = 0) -> int:
    for b in data:
        x = (value & 0x00FFFFFF) << 8
        value = b ^ x ^ _sfx_lookup_table[value >> 24]
    return value


try:
    import numpy as np  # type: ignore
    from numba import jit  # type: ignore

    _crc32q_lookup_table = np.array(_crc32q_lookup_table)
    crc32q_checksum = jit(nopython=True, nogil=True)(crc32q_checksum)

    _sfx_lookup_table = np.array(_sfx_lookup_table)
    sfx_checksum = jit(nopython=True, nogil=True)(sfx_checksum)
except ImportError as ex:
    print("Using a slow checksum implementation; consider installing jdmtool[jit]")
