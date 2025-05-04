import json
import shutil
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from jdmtool.avidyne import SFXFile
from jdmtool.common import get_data_dir
from jdmtool.main import cmd_transfer, PROMPT_CTX

TEST_DATA = Path(__file__).parent / "data" / "ifd400"


def test_transfer(tmp_path: Path):
    data_dir = get_data_dir()
    downloads_dir = data_dir / "downloads"
    downloads_dir.mkdir()

    shutil.copy(TEST_DATA / 'services.xml', data_dir)
    shutil.make_archive(str(downloads_dir / "service"), "zip", TEST_DATA / "zip")

    dest = tmp_path / 'device'
    dest.mkdir()

    vol_id = "1234-5678"

    PROMPT_CTX.set(lambda _: None)
    cmd_transfer(ids=[0], device=dest, no_download=True, vol_id=vol_id, full_erase=False)

    expected_debug = (TEST_DATA / "expected_debug.txt").read_text("utf-8")
    actual_debug = StringIO()

    with open(dest / "service.dsf", "rb") as fd:
        with redirect_stdout(actual_debug):
            SFXFile.debug(fd)

    assert actual_debug.getvalue() == expected_debug

    with open(TEST_DATA / "expected_jdm.json", encoding='utf-8') as fd:
        expected_jdm = json.load(fd)
    with open(dest / ".jdm", encoding='utf-8') as fd:
        actual_jdm = json.load(fd)

    # Clear sizes and hashes since they're not reproducable.
    for f in actual_jdm["ss"][0]["f"]:
        f["fs"] = -1
        f["fh"] = f["sh"] = "-"
    actual_jdm["z"] = "-"

    assert actual_jdm == expected_jdm
