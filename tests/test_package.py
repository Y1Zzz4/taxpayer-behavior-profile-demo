"""Smoke tests for the package skeleton."""


def test_package_can_be_imported() -> None:
    import taxpayer_profile

    assert taxpayer_profile.__version__ == "0.1.0"

