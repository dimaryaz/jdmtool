from typing import List

CRC32Q_POLYNOMIAL = 0x814141AB


def _create_crc32_q_lookup_table() -> List[int]:
    lookup_table: List[int] = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            if value & (1 << 31):
                value = ((value << 1) & 0xFFFFFFFF) ^ CRC32Q_POLYNOMIAL
            else:
                value <<= 1

        lookup_table.append(value)

    return lookup_table


_crc32_q_lookup_table = _create_crc32_q_lookup_table()


def calculate_crc32_q(data: bytes, value: int = 0) -> int:
    for b in data:
        index = b ^ (value >> 24)
        value = _crc32_q_lookup_table[index] ^ ((value & 0x00FFFFFF) << 8)
    return int(value)  # Handle the numpy case


try:
    import numpy as np  # type: ignore
    from numba import jit  # type: ignore

    _crc32_q_lookup_table = np.array(_crc32_q_lookup_table)
    calculate_crc32_q = jit(nopython=True, nogil=True)(calculate_crc32_q)
except ImportError as ex:
    print("Using a slow crc32q implementation; consider installing jdmtool[jit]")
