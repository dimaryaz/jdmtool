CRC32Q_POLYNOMIAL = 0x814141AB

# Full lookup table can be found in:
# "objdump -s --start-address=0xA049C0 --stop-address=0xA04DC0 jdm.exe"
# Jeppesen Distribution Manager Version 3.14.0 (Build 60)
SFX_POLYNOMIAL = 0x04C11DB7

# Full lookup table can be found in:
# "objdump -s --start-address=0x10028108 --stop-address=0x10028508 plugins/oem_garmin/GrmNavdata.dll"
# Jeppesen Distribution Manager Version 3.14.0 (Build 60)
FEAT_UNLK_POLYNOMIAL_1 = 0x076dc419
FEAT_UNLK_POLYNOMIAL_2 = 0x77073096


def _create_lookup_table(polynomial: int, length: int) -> list[int]:
    lookup_table: list[int] = []
    for index in range(length):
        value = index << 24
        for _ in range(8):
            if value & (1 << 31):
                value = ((value << 1) & 0xFFFFFFFF) ^ polynomial
            else:
                value <<= 1

        lookup_table.append(value)

    return lookup_table


_crc32q_lookup_table = _create_lookup_table(CRC32Q_POLYNOMIAL, 256)
_sfx_lookup_table = _create_lookup_table(SFX_POLYNOMIAL, 256)
_feat_unlk_lookup_table = [
    x ^ y
    for x in _create_lookup_table(FEAT_UNLK_POLYNOMIAL_1, 64)
    for y in _create_lookup_table(FEAT_UNLK_POLYNOMIAL_2, 4)
]


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


def feat_unlk_checksum(data: bytes, value: int = 0xFFFFFFFF) -> int:
    for b in data:
        index = b ^ (value & 0xFF)
        value = _feat_unlk_lookup_table[index] ^ (value >> 8)
    return value


try:
    import numpy as np  # type: ignore
    from numba import jit  # type: ignore

    _crc32q_lookup_table = np.array(_crc32q_lookup_table)
    crc32q_checksum = jit(nopython=True, nogil=True)(crc32q_checksum)

    _sfx_lookup_table = np.array(_sfx_lookup_table)
    sfx_checksum = jit(nopython=True, nogil=True)(sfx_checksum)

    _feat_unlk_lookup_table = np.array(_feat_unlk_lookup_table)
    feat_unlk_checksum = jit(nopython=True, nogil=True)(feat_unlk_checksum)
except ImportError as ex:
    print("Using a slow checksum implementation; consider installing jdmtool[jit]")
