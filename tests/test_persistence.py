"""
tests/test_persistence.py
---------------------------
native_app.persistence's `_to_bool()` exists because QSettings' INI backend
(the one actually used on Linux/macOS, not the Windows registry backend)
doesn't preserve a bare bool's type -- it round-trips as the STRING "true"
or "false", and a plain `if value:` truthiness check treats "false" as
truthy (any non-empty string is). This silently forced dark mode on at
every launch regardless of what was actually saved, since theme/dark is
stored as a bare bool (unlike the AUTOSAVE_ATTRS checkboxes, which dodge
this by wrapping values in a ["check", value] list that Qt DOES
type-preserve correctly). Caught by inspecting a real ~/.config/AlphaNest/
AlphaNest.conf that already correctly recorded `dark=false`.
"""

from native_app.persistence import _to_bool


def test_to_bool_parses_ini_backed_false_string_as_false():
    assert _to_bool("false") is False
    assert _to_bool("False") is False
    assert _to_bool("FALSE") is False


def test_to_bool_parses_ini_backed_true_string_as_true():
    assert _to_bool("true") is True
    assert _to_bool("True") is True
    assert _to_bool("1") is True


def test_to_bool_passes_through_real_booleans():
    assert _to_bool(True) is True
    assert _to_bool(False) is False


def test_to_bool_naive_truthiness_would_have_gotten_this_wrong():
    """Documents the exact bug this function fixes: a plain `if value:`
    on the string "false" is True in Python."""
    assert bool("false") is True  # the naive check that caused the bug
    assert _to_bool("false") is False  # what the fix actually returns
