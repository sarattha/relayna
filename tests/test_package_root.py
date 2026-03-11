import relayna


def test_package_root_exports_only_version() -> None:
    assert relayna.__all__ == ["__version__"]
    assert hasattr(relayna, "__version__")
    assert not hasattr(relayna, "RelaynaRabbitClient")
