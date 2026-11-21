import sys
from hashlib import sha256
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import Mock

import pytest

from relayna.spooltail import _install


@pytest.mark.parametrize("expected_wheel", [True, False])
def test_installer_checks_the_release_wheel_before_installing(monkeypatch, expected_wheel) -> None:
    content = b"release wheel" if expected_wheel else b"unexpected wheel"
    monkeypatch.setattr(sys, "argv", ["relayna-install-spooltail"])
    monkeypatch.setattr(_install, "_WHEEL_SHA256", sha256(b"release wheel").hexdigest())

    def run_command(command, *, check):
        assert check
        if command[0] == "gh":
            assert command[:-1] == [
                "gh",
                "release",
                "download",
                "v0.0.1",
                "--repo",
                "sarattha/relayna-spooltail",
                "--pattern",
                _install._WHEEL,
                "--dir",
            ]
            (Path(command[-1]) / _install._WHEEL).write_bytes(content)
        else:
            assert command[:5] == ["uv", "pip", "install", "--python", sys.executable]
            assert Path(command[-1]).read_bytes() == content

    run = Mock(side_effect=run_command)
    monkeypatch.setattr(_install, "run", run)
    if expected_wheel:
        _install.main()
        assert run.call_count == 2
    else:
        with pytest.raises(SystemExit, match="SHA-256 mismatch"):
            _install.main()
        assert run.call_count == 1
    assert not Path(run.call_args_list[0].args[0][-1]).exists()


def test_installer_propagates_download_failure(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["relayna-install-spooltail"])
    run = Mock(side_effect=CalledProcessError(4, "gh"))
    monkeypatch.setattr(_install, "run", run)

    with pytest.raises(SystemExit) as error:
        _install.main()

    assert error.value.code == 4
    run.assert_called_once()
