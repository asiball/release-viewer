import pytest

from release_collect.semver import Constraint, Version, VersionError, compare


def v(s):
    return Version.parse(s)


@pytest.mark.parametrize("lower,higher", [
    ("1.0.0", "1.0.1"),
    ("1.9.0", "1.10.0"),
    ("1.0.0-alpha", "1.0.0"),
    ("1.0.0-alpha", "1.0.0-alpha.1"),
    ("1.0.0-alpha.1", "1.0.0-alpha.beta"),
    ("1.0.0-beta.2", "1.0.0-beta.11"),
    ("1.0.0-rc.1", "1.0.0"),
])
def test_precedence(lower, higher):
    assert compare(v(lower), v(higher)) < 0
    assert compare(v(higher), v(lower)) > 0


def test_build_metadata_is_ignored_in_precedence():
    assert compare(v("1.2.1+acme.1"), v("1.2.1")) == 0
    assert Constraint.parse(">=1.2.1").satisfied_by(v("1.2.1+acme.1"))


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "01.2.3", "1.2.3-", "1.2.3-01", ""])
def test_invalid_versions(bad):
    with pytest.raises(VersionError):
        v(bad)


@pytest.mark.parametrize("constraint,version,ok", [
    (">=1.2.0, <2.0.0", "1.2.0", True),
    (">=1.2.0, <2.0.0", "1.9.9", True),
    (">=1.2.0, <2.0.0", "2.0.0", False),
    (">=1.2.0, <2.0.0", "1.1.9", False),
    ("==1.0.0", "1.0.0+x", True),
    ("!=1.0.1", "1.0.1", False),
    (">1.0.0", "1.0.0", False),
    ("<=1.0.0", "1.0.0", True),
])
def test_constraints(constraint, version, ok):
    assert Constraint.parse(constraint).satisfied_by(v(version)) is ok


@pytest.mark.parametrize("bad", ["^1.2.0", "~1.2", "1.2.0", ">=1.2", ""])
def test_unsupported_constraint_syntax(bad):
    with pytest.raises(VersionError):
        Constraint.parse(bad)
