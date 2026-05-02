"""Tests for incremental symbol index updates.

Tests the dirty tracking and lazy patching logic on SolidLanguageServer
without requiring a real language server process.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from solidlsp.ls import DocumentSymbols, SolidLanguageServer
from solidlsp.ls_types import SymbolKind
from solidlsp.util.symbol_index import SymbolIndex, SymbolIndexEntry


# -- Fixtures ---------------------------------------------------------------


@pytest.fixture
def sample_index() -> SymbolIndex:
    """An index with known entries across two files."""
    index = SymbolIndex()
    entries = [
        SymbolIndexEntry(name="ClassA", name_path="ClassA", relative_path="src/a.cs", kind=SymbolKind.Class, line=1, parent_name_path=None),
        SymbolIndexEntry(name="MethodA", name_path="ClassA/MethodA", relative_path="src/a.cs", kind=SymbolKind.Method, line=5, parent_name_path="ClassA"),
        SymbolIndexEntry(name="ClassB", name_path="ClassB", relative_path="src/b.cs", kind=SymbolKind.Class, line=1, parent_name_path=None),
        SymbolIndexEntry(name="MethodA", name_path="ClassB/MethodA", relative_path="src/b.cs", kind=SymbolKind.Method, line=10, parent_name_path="ClassB"),
    ]
    for e in entries:
        index.add(e)
    return index


# -- Tests for remove_file + re-add equivalence ----------------------------


class TestRemoveFileReaddEquivalence:
    """Verify that remove + re-add gives same result as full rebuild."""

    def test_remove_and_readd_same_as_original(self, sample_index: SymbolIndex):
        # Remove entries for a.cs
        sample_index.remove_file("src/a.cs")
        # Re-add different entries for a.cs (simulating an edit)
        new_entries = [
            SymbolIndexEntry(name="ClassA", name_path="ClassA", relative_path="src/a.cs", kind=SymbolKind.Class, line=1, parent_name_path=None),
            SymbolIndexEntry(name="NewMethod", name_path="ClassA/NewMethod", relative_path="src/a.cs", kind=SymbolKind.Method, line=8, parent_name_path="ClassA"),
        ]
        for e in new_entries:
            sample_index.add(e)

        # Verify new state
        assert sample_index.total_files == 2
        assert "NewMethod" in sample_index.symbols_in_file("src/a.cs")
        assert "MethodA" not in sample_index.symbols_in_file("src/a.cs")
        # MethodA still exists in b.cs
        methods = sample_index.lookup("MethodA")
        assert len(methods) == 1
        assert methods[0].relative_path == "src/b.cs"

    def test_remove_readd_vs_fresh_build(self, sample_index: SymbolIndex):
        """Incremental path should produce identical results to full rebuild."""
        # Simulate edit: remove a.cs, add new symbols
        sample_index.remove_file("src/a.cs")
        edited_entries = [
            SymbolIndexEntry(name="ClassA", name_path="ClassA", relative_path="src/a.cs", kind=SymbolKind.Class, line=1, parent_name_path=None),
            SymbolIndexEntry(name="Renamed", name_path="ClassA/Renamed", relative_path="src/a.cs", kind=SymbolKind.Method, line=5, parent_name_path="ClassA"),
        ]
        for e in edited_entries:
            sample_index.add(e)

        # Build a fresh index with the same data
        fresh = SymbolIndex()
        for e in edited_entries:
            fresh.add(e)
        fresh.add(SymbolIndexEntry(name="ClassB", name_path="ClassB", relative_path="src/b.cs", kind=SymbolKind.Class, line=1, parent_name_path=None))
        fresh.add(SymbolIndexEntry(name="MethodA", name_path="ClassB/MethodA", relative_path="src/b.cs", kind=SymbolKind.Method, line=10, parent_name_path="ClassB"))

        # Compare
        assert sample_index.total_entries == fresh.total_entries
        assert sample_index.total_files == fresh.total_files
        assert sample_index.total_symbols == fresh.total_symbols


# -- Tests for dirty tracking -----------------------------------------------


class TestDirtyTracking:
    """Test dirty tracking fields and mark_index_dirty behavior."""

    def test_mark_index_dirty_adds_to_set(self):
        """mark_index_dirty should add the file and set needs_save."""
        ls = MagicMock(spec=SolidLanguageServer)
        # Set up real attributes
        ls._index_dirty_files = set()
        ls._symbol_index_needs_save = False
        # Call real method
        SolidLanguageServer.mark_index_dirty(ls, "src/foo.cs")
        assert "src/foo.cs" in ls._index_dirty_files
        assert ls._symbol_index_needs_save is True

    def test_mark_index_dirty_multiple_files(self):
        """Multiple marks accumulate in the dirty set."""
        ls = MagicMock(spec=SolidLanguageServer)
        ls._index_dirty_files = set()
        ls._symbol_index_needs_save = False
        SolidLanguageServer.mark_index_dirty(ls, "src/a.cs")
        SolidLanguageServer.mark_index_dirty(ls, "src/b.cs")
        SolidLanguageServer.mark_index_dirty(ls, "src/a.cs")  # duplicate
        assert ls._index_dirty_files == {"src/a.cs", "src/b.cs"}

    def test_mark_index_dirty_idempotent(self):
        """Marking same file twice doesn't double-add."""
        ls = MagicMock(spec=SolidLanguageServer)
        ls._index_dirty_files = set()
        ls._symbol_index_needs_save = False
        SolidLanguageServer.mark_index_dirty(ls, "src/x.cs")
        SolidLanguageServer.mark_index_dirty(ls, "src/x.cs")
        assert len(ls._index_dirty_files) == 1


# -- Tests for save_cache skipping -------------------------------------------


class TestSaveCacheBehavior:
    """Test that save_cache only persists index when dirty."""

    def test_save_cache_skips_when_not_dirty(self):
        """save_cache should NOT call _save_symbol_index when needs_save is False."""
        ls = MagicMock(spec=SolidLanguageServer)
        ls._symbol_index_needs_save = False
        ls._save_raw_document_symbols_cache = MagicMock()
        ls._save_document_symbols_cache = MagicMock()
        ls._save_symbol_index = MagicMock()
        SolidLanguageServer.save_cache(ls)
        ls._save_symbol_index.assert_not_called()

    def test_save_cache_calls_save_when_dirty(self):
        """save_cache SHOULD call _save_symbol_index when needs_save is True."""
        ls = MagicMock(spec=SolidLanguageServer)
        ls._symbol_index_needs_save = True
        ls._save_raw_document_symbols_cache = MagicMock()
        ls._save_document_symbols_cache = MagicMock()
        ls._save_symbol_index = MagicMock()
        SolidLanguageServer.save_cache(ls)
        ls._save_symbol_index.assert_called_once()


# -- Tests for property-based lazy patch trigger ----------------------------


class TestSymbolIndexProperty:
    """Test the symbol_index property triggers patch only when needed."""

    def test_property_returns_none_when_no_index(self):
        """If _symbol_index is None, property returns None without patching."""
        ls = MagicMock(spec=SolidLanguageServer)
        ls._symbol_index = None
        ls._index_dirty_files = {"src/x.cs"}
        ls._patching_index = False
        result = SolidLanguageServer.symbol_index.fget(ls)
        assert result is None

    def test_property_returns_index_when_clean(self):
        """If no dirty files, property returns index without patching."""
        ls = MagicMock(spec=SolidLanguageServer)
        index = SymbolIndex()
        ls._symbol_index = index
        ls._index_dirty_files = set()
        ls._patching_index = False
        result = SolidLanguageServer.symbol_index.fget(ls)
        assert result is index

    def test_property_triggers_patch_when_dirty(self):
        """If dirty files exist, property calls _patch_symbol_index."""
        ls = MagicMock(spec=SolidLanguageServer)
        index = SymbolIndex()
        ls._symbol_index = index
        ls._index_dirty_files = {"src/foo.cs"}
        ls._patching_index = False
        ls._patch_symbol_index = MagicMock()
        SolidLanguageServer.symbol_index.fget(ls)
        ls._patch_symbol_index.assert_called_once()

    def test_property_skips_patch_during_reentrant_access(self):
        """Re-entrancy guard: if _patching_index is True, skip patching."""
        ls = MagicMock(spec=SolidLanguageServer)
        index = SymbolIndex()
        ls._symbol_index = index
        ls._index_dirty_files = {"src/foo.cs"}
        ls._patching_index = True  # already patching
        ls._patch_symbol_index = MagicMock()
        result = SolidLanguageServer.symbol_index.fget(ls)
        ls._patch_symbol_index.assert_not_called()
        assert result is index


# -- Tests for threshold fallback -------------------------------------------


class TestPatchThreshold:
    """Test that exceeding the dirty file threshold triggers full rebuild."""

    def test_threshold_triggers_full_rebuild(self):
        """When dirty files exceed threshold, _patch_symbol_index does full rebuild."""
        ls = MagicMock(spec=SolidLanguageServer)
        index = SymbolIndex()
        ls._symbol_index = index
        ls._patching_index = False
        ls._symbol_index_needs_save = False
        # Create 25 dirty files (exceeds default threshold of 20)
        ls._index_dirty_files = {f"src/file{i}.cs" for i in range(25)}
        ls._INDEX_PATCH_THRESHOLD = 20
        rebuilt_index = SymbolIndex()
        ls._build_symbol_index = MagicMock(return_value=rebuilt_index)
        ls.request_document_symbols = MagicMock()

        SolidLanguageServer._patch_symbol_index(ls)

        ls._build_symbol_index.assert_called_once()
        assert ls._symbol_index is rebuilt_index
        assert ls._index_dirty_files == set()
        assert ls._symbol_index_needs_save is True
        # request_document_symbols should NOT be called (full rebuild doesn't need per-file refresh)
        ls.request_document_symbols.assert_not_called()
