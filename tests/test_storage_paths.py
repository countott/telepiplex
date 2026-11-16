import builtins
import os
from pathlib import Path

import pytest


def normalize(value, **policy):
    from telepiplex_plugin_sdk.storage_paths import normalize_115_directory
    return normalize_115_directory(value, **policy)


@pytest.mark.parametrize("value, expected", [
    ("/电影/动作", "/电影/动作"), ("电影/动作", "/电影/动作"),
    ("/Movies/", "/Movies"), ("Movies/", "/Movies"),
    ("/MiXeD", "/MiXeD"), ("/e\u0301/é", "/e\u0301/é"),
    (" space / inner /tail ", "/ space / inner /tail "),
])
def test_normalization_preserves_directory_identity(value, expected):
    assert normalize(value, allow_relative=True, allow_root=False) == expected


@pytest.mark.parametrize("value", [None, 42, "", "/a//b", "//a", "/a//", "/a/./b", "/a/../b", ".", "..", "a\\b", "/a\x00", "/a\n", "/a\t", "/a\x7f", "/a\x85"])
def test_unsafe_or_non_string_paths_are_rejected(value):
    with pytest.raises(ValueError):
        normalize(value, allow_relative=True, allow_root=True)


def test_root_and_relative_input_are_explicit_caller_policies():
    assert normalize("/", allow_relative=False, allow_root=True) == "/"
    with pytest.raises(ValueError):
        normalize("/", allow_relative=True, allow_root=False)
    with pytest.raises(ValueError):
        normalize("电影", allow_relative=False, allow_root=True)


def test_normalization_does_not_access_the_filesystem(monkeypatch):
    from telepiplex_plugin_sdk.storage_paths import normalize_115_directory

    def forbidden(*args, **kwargs):
        pytest.fail("115 directory normalization accessed the filesystem")

    for owner, names in [(builtins, ["open"]), (os, ["stat", "mkdir", "makedirs", "listdir"]), (Path, ["open", "mkdir", "exists", "resolve"])]:
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    assert normalize_115_directory("cloud/目录/", allow_relative=True, allow_root=False) == "/cloud/目录"
