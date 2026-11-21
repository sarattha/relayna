"""Install Relayna's pinned native Spooltail SDK from GitHub Releases."""

from argparse import ArgumentParser
from hashlib import file_digest
from pathlib import Path
from subprocess import CalledProcessError, run
from sys import executable
from tempfile import TemporaryDirectory

_REPOSITORY = "sarattha/relayna-spooltail"
_SDK_VERSION = "0.0.1"
_WHEEL = f"relayna_spooltail-{_SDK_VERSION}-py3-none-any.whl"
_WHEEL_SHA256 = "3076a0e4d1d6d6c974b5368be12b50ac974e2771d501d4607122f44b0675fd46"


def main() -> None:
    ArgumentParser(description="Install the official Spooltail SDK into this Relayna environment.").parse_args()
    try:
        with TemporaryDirectory(prefix="relayna-spooltail-") as directory:
            run(
                [
                    "gh",
                    "release",
                    "download",
                    f"v{_SDK_VERSION}",
                    "--repo",
                    _REPOSITORY,
                    "--pattern",
                    _WHEEL,
                    "--dir",
                    directory,
                ],
                check=True,
            )
            wheel = Path(directory) / _WHEEL
            with wheel.open("rb") as contents:
                if file_digest(contents, "sha256").hexdigest() != _WHEEL_SHA256:
                    raise SystemExit(f"SHA-256 mismatch for {_WHEEL}")

            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    executable,
                    "--reinstall-package",
                    "relayna-spooltail",
                    str(wheel),
                ],
                check=True,
            )
    except CalledProcessError as error:
        raise SystemExit(error.returncode) from None
    except FileNotFoundError as error:
        raise SystemExit(f"Required command or file is missing: {error.filename}") from None
