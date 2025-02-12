import pytest

from jdmtool.ngt import (
    add_checksum,
    decode_packet,
    encode_packet,
    remove_checksum,
    wrap_message,
    unwrap_message,
)


SAMPLE1 = (
    b"\x7E\x09\x00\x24\x00\xFF\xA0\x06\x00\xE8\x00\x21\x01\x0B\x00\x04"
    b"\x80\xB8\x02\x00\x00\xBC\x00\x00\x08\x0D\x60\xA3\x05\x0E\x00\xD4"
    b"\x2C\x83\x06\x00\x00\xD0\x00\x00\x00\xDD\x0C\xC7\xBB\x7E\x7E\x09"
    b"\x00\x08\x00\xEF\x10\xE0\x97\xEF\x10\x00\x18\xE7\x21\xE8\xAF\x7E"
)


def test_decode_encode() -> None:
    chunks = decode_packet(SAMPLE1)
    assert len(chunks) == 2
    assert encode_packet(chunks) == SAMPLE1

    msg1 = remove_checksum(chunks[0])
    msg2 = remove_checksum(chunks[1])

    assert add_checksum(msg1) == chunks[0]
    assert add_checksum(msg2) == chunks[1]

    msg_type1, msg_content1 = unwrap_message(msg1)
    msg_type2, msg_content2 = unwrap_message(msg2)

    assert wrap_message(msg_type1, msg_content1) == msg1
    assert wrap_message(msg_type2, msg_content2) == msg2
