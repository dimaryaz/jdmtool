from jdmtool.crc32q import calculate_crc32_q

def test_crc32_q():
    assert calculate_crc32_q(b'hello world') == 0x13aa9356
    assert calculate_crc32_q(b'hello world' + calculate_crc32_q(b'hello world').to_bytes(4, 'big')) == 0

def test_crc32_q_initial():
    assert calculate_crc32_q(b'world', calculate_crc32_q(b'hello ')) == 0x13aa9356
