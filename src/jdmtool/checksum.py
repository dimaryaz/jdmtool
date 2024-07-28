import libscrc

def crc32q_checksum(data: bytes, initial_value: int = 0) -> int:
    return libscrc.crc32_q(data, initial_value)  # pylint: disable=no-member
