import hats_registry


def test_version():
    """Check to see that we can get the package version"""
    assert hats_registry.__version__ is not None
