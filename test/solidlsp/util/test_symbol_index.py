"""Unit tests for SymbolIndex."""
import json
import os
import tempfile

import pytest

from solidlsp.lsp_protocol_handler.lsp_types import SymbolKind
from solidlsp.util.symbol_index import SymbolIndex, SymbolIndexEntry


@pytest.fixture
def sample_entries() -> list[SymbolIndexEntry]:
    """A small, representative set of index entries."""
    return [
        SymbolIndexEntry(name="MyClass", name_path="MyClass", relative_path="src/foo.cs", kind=SymbolKind.Class, line=10, parent_name_path=None),
        SymbolIndexEntry(name="DoWork", name_path="MyClass/DoWork", relative_path="src/foo.cs", kind=SymbolKind.Method, line=15, parent_name_path="MyClass"),
        SymbolIndexEntry(name="DoWork", name_path="OtherClass/DoWork", relative_path="src/bar.cs", kind=SymbolKind.Method, line=5, parent_name_path="OtherClass"),
        SymbolIndexEntry(name="OtherClass", name_path="OtherClass", relative_path="src/bar.cs", kind=SymbolKind.Class, line=1, parent_name_path=None),
        SymbolIndexEntry(name="GetValue", name_path="MyClass/GetValue", relative_path="src/foo.cs", kind=SymbolKind.Method, line=25, parent_name_path="MyClass"),
        SymbolIndexEntry(name="GetValueAsync", name_path="Helper/GetValueAsync", relative_path="src/helper.cs", kind=SymbolKind.Method, line=8, parent_name_path="Helper"),
    ]


@pytest.fixture
def populated_index(sample_entries: list[SymbolIndexEntry]) -> SymbolIndex:
    """An index populated with the sample entries."""
    index = SymbolIndex()
    for entry in sample_entries:
        index.add(entry)
    return index


class TestSymbolIndexBasics:
    """Tests for add, counts, and simple queries."""

    def test_empty_index(self) -> None:
        index = SymbolIndex()
        assert index.total_entries == 0
        assert index.total_files == 0
        assert index.total_symbols == 0
        assert index.lookup("Anything") == []
        assert index.files_for_symbol("Anything") == set()
        assert index.symbols_in_file("nonexistent.cs") == set()

    def test_add_and_counts(self, populated_index: SymbolIndex, sample_entries: list[SymbolIndexEntry]) -> None:
        assert populated_index.total_entries == len(sample_entries)
        assert populated_index.total_files == 3  # foo.cs, bar.cs, helper.cs
        assert populated_index.total_symbols == 5  # MyClass, DoWork, OtherClass, GetValue, GetValueAsync


class TestLookup:
    """Tests for exact and substring lookup."""

    def test_exact_lookup_single(self, populated_index: SymbolIndex) -> None:
        results = populated_index.lookup("MyClass")
        assert len(results) == 1
        assert results[0].relative_path == "src/foo.cs"
        assert results[0].kind == SymbolKind.Class

    def test_exact_lookup_multiple(self, populated_index: SymbolIndex) -> None:
        results = populated_index.lookup("DoWork")
        assert len(results) == 2
        paths = {e.relative_path for e in results}
        assert paths == {"src/foo.cs", "src/bar.cs"}

    def test_exact_lookup_miss(self, populated_index: SymbolIndex) -> None:
        assert populated_index.lookup("NonExistent") == []

    def test_substring_lookup(self, populated_index: SymbolIndex) -> None:
        results = populated_index.lookup("GetValue", substring_matching=True)
        assert len(results) == 2
        names = {e.name for e in results}
        assert names == {"GetValue", "GetValueAsync"}

    def test_substring_lookup_no_match(self, populated_index: SymbolIndex) -> None:
        results = populated_index.lookup("XYZ", substring_matching=True)
        assert results == []

    def test_kind_filter(self, populated_index: SymbolIndex) -> None:
        # only classes
        results = populated_index.lookup("MyClass", kind_filter=[SymbolKind.Class])
        assert len(results) == 1
        # filter out with wrong kind
        results = populated_index.lookup("MyClass", kind_filter=[SymbolKind.Method])
        assert results == []

    def test_substring_with_kind_filter(self, populated_index: SymbolIndex) -> None:
        results = populated_index.lookup("GetValue", substring_matching=True, kind_filter=[SymbolKind.Method])
        assert len(results) == 2  # both are methods
        results = populated_index.lookup("GetValue", substring_matching=True, kind_filter=[SymbolKind.Class])
        assert results == []


class TestFileMaps:
    """Tests for the reverse file-to-symbol map."""

    def test_files_for_symbol(self, populated_index: SymbolIndex) -> None:
        files = populated_index.files_for_symbol("DoWork")
        assert files == {"src/foo.cs", "src/bar.cs"}

    def test_symbols_in_file(self, populated_index: SymbolIndex) -> None:
        symbols = populated_index.symbols_in_file("src/foo.cs")
        assert symbols == {"MyClass", "DoWork", "GetValue"}

    def test_symbols_in_file_miss(self, populated_index: SymbolIndex) -> None:
        assert populated_index.symbols_in_file("nonexistent.cs") == set()


class TestIterAll:
    """Tests for iter_all_entries."""

    def test_iter_all(self, populated_index: SymbolIndex, sample_entries: list[SymbolIndexEntry]) -> None:
        all_entries = list(populated_index.iter_all_entries())
        assert len(all_entries) == len(sample_entries)


class TestPersistence:
    """Tests for save/load round-tripping."""

    def test_save_load_roundtrip(self, populated_index: SymbolIndex) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "index.pkl")
            populated_index.save(path)

            loaded = SymbolIndex.load(path)
            assert loaded is not None
            assert loaded.total_entries == populated_index.total_entries
            assert loaded.total_files == populated_index.total_files
            assert loaded.total_symbols == populated_index.total_symbols

            # verify actual data
            for name in ["MyClass", "DoWork", "GetValue", "GetValueAsync", "OtherClass"]:
                orig = populated_index.lookup(name)
                reloaded = loaded.lookup(name)
                assert len(orig) == len(reloaded)
                for o, r in zip(orig, reloaded):
                    assert o == r

    def test_save_load_with_extra_version(self, populated_index: SymbolIndex) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "index.pkl")
            populated_index.save(path, extra_version="v1")

            # loading with matching version should work
            loaded = SymbolIndex.load(path, extra_version="v1")
            assert loaded is not None
            assert loaded.total_entries == populated_index.total_entries

            # loading with mismatched version should return None
            loaded = SymbolIndex.load(path, extra_version="v2")
            assert loaded is None

    def test_load_nonexistent(self) -> None:
        loaded = SymbolIndex.load("/nonexistent/path/index.pkl")
        assert loaded is None

    def test_empty_roundtrip(self) -> None:
        index = SymbolIndex()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "index.pkl")
            index.save(path)
            loaded = SymbolIndex.load(path)
            assert loaded is not None
            assert loaded.total_entries == 0


class TestJsonExport:
    """Tests for export_json."""

    def test_export_json(self, populated_index: SymbolIndex) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "index.json")
            populated_index.export_json(path)

            with open(path, encoding="utf-8") as f:
                data = json.load(f)

            assert "MyClass" in data
            assert "DoWork" in data
            assert len(data["DoWork"]) == 2

    def test_export_json_empty(self) -> None:
        index = SymbolIndex()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "index.json")
            index.export_json(path)

            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert data == {}


class TestFrozenEntry:
    """Tests that SymbolIndexEntry is immutable."""

    def test_frozen(self) -> None:
        entry = SymbolIndexEntry(name="Foo", name_path="Foo", relative_path="x.cs", kind=SymbolKind.Class, line=0, parent_name_path=None)
        with pytest.raises(AttributeError):
            entry.name = "Bar"  # type: ignore[misc]

    def test_hashable(self) -> None:
        entry = SymbolIndexEntry(name="Foo", name_path="Foo", relative_path="x.cs", kind=SymbolKind.Class, line=0, parent_name_path=None)
        # should be usable as dict key / set member
        s = {entry}
        assert entry in s
