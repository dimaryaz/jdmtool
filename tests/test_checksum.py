from jdmtool.checksum import crc32q_checksum, feat_unlk_checksum, sfx_checksum


def test_crc32q():
    assert crc32q_checksum(b'hello world') == 0x13aa9356
    assert crc32q_checksum(b'hello world' + crc32q_checksum(b'hello world').to_bytes(4, 'big')) == 0

    assert isinstance(crc32q_checksum(b'hello world'), int)  # numba/numpy sanity check

def test_crc32q_initial():
    assert crc32q_checksum(b'world', crc32q_checksum(b'hello ')) == 0x13aa9356


def test_feat_unlk():
    assert feat_unlk_checksum(b'hello world') == 0xf2b5ee7a
    assert feat_unlk_checksum(b'hello world' + feat_unlk_checksum(b'hello world').to_bytes(4, 'little')) == 0

    assert isinstance(feat_unlk_checksum(b'hello world'), int)  # numba/numpy sanity check

def test_feat_unlk_initial():
    assert feat_unlk_checksum(b'world', feat_unlk_checksum(b'hello ')) == 0xf2b5ee7a


def test_sfx():
    assert sfx_checksum(b'hello world') == 0xcd5fd321

    assert isinstance(sfx_checksum(b'hello world'), int)  # numba/numpy sanity check

def test_sfx_initial():
    assert sfx_checksum(b'world', sfx_checksum(b'hello ')) == 0xcd5fd321
