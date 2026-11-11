"""Missing-input handling for the extract commands.

cmd_extract_basemap used to let FileNotFoundError escape (main() only
translates JdmToolException into a clean message + exit 1), so a typo'd
input path dumped a traceback. It now raises UserException naming the
file, which main() reports cleanly.
"""
import pytest

from jdmtool.main import UserException, cmd_extract_basemap


class TestExtractBasemapMissingInput:
    def test_missing_input_raises_user_exception(self, tmp_path):
        missing = str(tmp_path / "nope.bin")
        with pytest.raises(UserException, match="nope.bin"):
            cmd_extract_basemap(missing, str(tmp_path / "out.img"))

    def test_missing_input_is_clean_cli_error(self, tmp_path, capsys):
        from jdmtool.main import JdmToolException

        missing = str(tmp_path / "nope.bin")
        try:
            cmd_extract_basemap(missing, str(tmp_path / "out.img"))
        except JdmToolException as ex:
            print(ex)
        out = capsys.readouterr().out
        assert "Input file not found" in out
        assert "Traceback" not in out
