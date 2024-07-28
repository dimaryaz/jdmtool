import libscrc

def calculate_crc32_q(data: bytes, initial_value: int = 0) -> int:
    return libscrc.crc32_q(data, initial_value)  # pylint: disable=no-member
