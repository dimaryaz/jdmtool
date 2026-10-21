import filecmp
import json
import shutil
from pathlib import Path
import zipfile

from jdmtool.common import get_data_dir
from jdmtool.main import cmd_transfer, PROMPT_CTX

TEST_DATA_ROOT = Path(__file__).parent / "data"


def test_transfer(tmp_path: Path):
    test_data = TEST_DATA_ROOT / "g1000"

    data_dir = get_data_dir()
    downloads_dir = data_dir / "downloads"
    downloads_dir.mkdir()

    shutil.copy(test_data / 'services.xml', data_dir)

    expected_files = [
        "ldr_sys/avtn_db.bin",
        "ldr_sys/nav_db2.bin",
        "foo.txt",
        "dir/bar.txt",
    ]

    with zipfile.ZipFile(downloads_dir / "service.zip", 'w') as zf:
        for file in expected_files:
            zf.write(test_data / "zip" / file, file)

    dest = tmp_path / 'device'
    dest.mkdir()

    vol_id = "1234-5678"

    PROMPT_CTX.set(lambda prompt: None)
    cmd_transfer(ids=[0], device=str(dest), no_download=True, vol_id=vol_id, full_erase=False)

    assert filecmp.cmpfiles(test_data / "zip", dest, expected_files, shallow=False) == (expected_files, [], [])

    assert filecmp.cmp(test_data / "expected_feat_unlk.dat", dest / "feat_unlk.dat", shallow=False)

    with open(test_data / "expected_jdm.json", encoding='utf-8') as fd:
        expected_jdm = json.load(fd)
    with open(dest / ".jdm", encoding='utf-8') as fd:
        actual_jdm = json.load(fd)

    assert actual_jdm == expected_jdm
