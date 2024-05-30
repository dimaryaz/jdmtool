import binascii
from typing import List

# From "objdump -s --start-address=0x10028108 --stop-address=0x10028508 plugins/oem_garmin/GrmNavdata.dll"
# Jeppesen Distribution Manager Version 3.14.0 (Build 60)
LOOKUP_TABLE: List[int] = [int.from_bytes(binascii.a2b_hex(v), 'little') for v in b'''
    00000000 96300777 2c610eee ba510999
    19c46d07 8ff46a70 35a563e9 a395649e
    3288db0e a4b8dc79 1ee9d5e0 88d9d297
    2b4cb609 bd7cb17e 072db8e7 911dbf90
    6410b71d f220b06a 4871b9f3 de41be84
    7dd4da1a ebe4dd6d 51b5d4f4 c785d383
    56986c13 c0a86b64 7af962fd ecc9658a
    4f5c0114 d96c0663 633d0ffa f50d088d
    c8206e3b 5e10694c e44160d5 727167a2
    d1e4033c 47d4044b fd850dd2 6bb50aa5
    faa8b535 6c98b242 d6c9bbdb 40f9bcac
    e36cd832 755cdf45 cf0dd6dc 593dd1ab
    ac30d926 3a00de51 8051d7c8 1661d0bf
    b5f4b421 23c4b356 9995bacf 0fa5bdb8
    9eb80228 0888055f b2d90cc6 24e90bb1
    877c6f2f 114c6858 ab1d61c1 3d2d66b6
    9041dc76 0671db01 bc20d298 2a10d5ef
    8985b171 1fb5b606 a5e4bf9f 33d4b8e8
    a2c90778 34f9000f 8ea80996 18980ee1
    bb0d6a7f 2d3d6d08 976c6491 015c63e6
    f4516b6b 62616c1c d8306585 4e0062f2
    ed95066c 7ba5011b c1f40882 57c40ff5
    c6d9b065 50e9b712 eab8be8b 7c88b9fc
    df1ddd62 492dda15 f37cd38c 654cd4fb
    5861b24d ce51b53a 7400bca3 e230bbd4
    41a5df4a d795d83d 6dc4d1a4 fbf4d6d3
    6ae96943 fcd96e34 468867ad d0b860da
    732d0444 e51d0333 5f4c0aaa c97c0ddd
    3c710550 aa410227 10100bbe 86200cc9
    25b56857 b3856f20 09d466b9 9fe461ce
    0ef9de5e 98c9d929 2298d0b0 b4a8d7c7
    173db359 810db42e 3b5cbdb7 ad6cbac0
    2083b8ed b6b3bf9a 0ce2b603 9ad2b174
    3947d5ea af77d29d 1526db04 8316dc73
    120b63e3 843b6494 3e6a6d0d a85a6a7a
    0bcf0ee4 9dff0993 27ae000a b19e077d
    44930ff0 d2a30887 68f2011e fec20669
    5d5762f7 cb676580 71366c19 e7066b6e
    761bd4fe e02bd389 5a7ada10 cc4add67
    6fdfb9f9 f9efbe8e 43beb717 d58eb060
    e8a3d6d6 7e93d1a1 c4c2d838 52f2df4f
    f167bbd1 6757bca6 dd06b53f 4b36b248
    da2b0dd8 4c1b0aaf f64a0336 607a0441
    c3ef60df 55df67a8 ef8e6e31 79be6946
    8cb361cb 1a8366bc a0d26f25 36e26852
    95770ccc 03470bbb b9160222 2f260555
    be3bbac5 280bbdb2 925ab42b 046ab35c
    a7ffd7c2 31cfd0b5 8b9ed92c 1daede5b
    b0c2649b 26f263ec 9ca36a75 0a936d02
    a906099c 3f360eeb 85670772 13570005
    824abf95 147ab8e2 ae2bb17b 381bb60c
    9b8ed292 0dbed5e5 b7efdc7c 21dfdb0b
    d4d2d386 42e2d4f1 f8b3dd68 6e83da1f
    cd16be81 5b26b9f6 e177b06f 7747b718
    e65a0888 706a0fff ca3b0666 5c0b0111
    ff9e658f 69ae62f8 d3ff6b61 45cf6c16
    78e20aa0 eed20dd7 5483044e c2b30339
    612667a7 f71660d0 4d476949 db776e3e
    4a6ad1ae dc5ad6d9 660bdf40 f03bd837
    53aebca9 c59ebbde 7fcfb247 e9ffb530
    1cf2bdbd 8ac2baca 3093b353 a6a3b424
    0536d0ba 9306d7cd 2957de54 bf67d923
    2e7a66b3 b84a61c4 021b685d 942b6f2a
    37be0bb4 a18e0cc3 1bdf055a 8def022d
'''.split()]


def feat_unlk_checksum(data: bytes) -> int:
    value = 0xFFFFFFFF
    for b in data:
        x = b ^ (value & 0xFF)
        value >>= 8
        value ^= LOOKUP_TABLE[x]
    return value
