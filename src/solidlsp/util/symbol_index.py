"""
Precomputed inverted symbol index that maps symbol names to their file locations.

Built from the per-file document-symbol cache at index time and persisted to disk.
At query time, enables O(matching_files) symbol lookup instead of O(all_files) tree walks.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Hashable, Iterator

from solidlsp.lsp_protocol_handler.lsp_types import SymbolKind
from solidlsp.util.cache import load_cache, save_cache

log = logging.getLogger(__name__)

INDEX_VERSION: tuple[str, int] = ("SymbolIndex", 1)


@dataclass(frozen=True, slots=True)
class SymbolIndexEntry:
    """An entry in the symbol index representing a single symbol's location.

    :param name: the simple name of the symbol (e.g. ``MyClass``).
    :param name_path: the full name path within the file (e.g. ``MyClass/my_method``).
    :param relative_path: the file path relative to the project root.
    :param kind: the LSP symbol kind.
    :param line: the 0-based start line of the symbol definition.
    :param parent_name_path: the name path of the parent symbol, or ``None`` for root symbols.
    """

    name: str
    name_path: str
    relative_path: str
    kind: int
    line: int
    parent_name_path: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for JSON export."""
        return {
            "name": self.name,
            "name_path": self.name_path,
            "relative_path": self.relative_path,
            "kind": self.kind,
            "line": self.line,
            "parent_name_path": self.parent_name_path,
        }


class SymbolIndex:
    """Inverted index from symbol names to their locations across all indexed files.

    The index is built from the per-file ``DocumentSymbols`` cache and supports
    fast name-based lookup without requiring a directory walk or live LSP calls.
    """

    def __init__(self) -> None:
        # forward map: symbol name → list of index entries
        self._by_name: dict[str, list[SymbolIndexEntry]] = defaultdict(list)
        # reverse map: relative file path → set of symbol names in that file
        self._by_file: dict[str, set[str]] = defaultdict(set)

    # -- build interface -------------------------------------------------------

    def add(self, entry: SymbolIndexEntry) -> None:
        """Add a single entry to the index."""
        self._by_name[entry.name].append(entry)
        self._by_file[entry.relative_path].add(entry.name)

    def remove_file(self, relative_path: str) -> int:
        """Remove all entries for the given file from the index.

        :param relative_path: the file whose symbols should be purged.
        :return: the number of entries removed.
        """
        names = self._by_file.pop(relative_path, None)
        if not names:
            return 0

        removed = 0
        for name in names:
            entries = self._by_name.get(name)
            if entries is None:
                continue
            before = len(entries)
            self._by_name[name] = [e for e in entries if e.relative_path != relative_path]
            removed += before - len(self._by_name[name])
            if not self._by_name[name]:
                del self._by_name[name]

        return removed

    @property
    def total_entries(self) -> int:
        """Total number of index entries across all symbols."""
        return sum(len(entries) for entries in self._by_name.values())

    @property
    def total_files(self) -> int:
        """Number of distinct files in the index."""
        return len(self._by_file)

    @property
    def total_symbols(self) -> int:
        """Number of distinct symbol names in the index."""
        return len(self._by_name)

    # -- query interface -------------------------------------------------------

    def lookup(
        self,
        name_pattern: str,
        *,
        substring_matching: bool = False,
        kind_filter: list[int] | None = None,
    ) -> list[SymbolIndexEntry]:
        """Look up index entries by symbol name.

        :param name_pattern: exact symbol name or, if *substring_matching* is True,
            a substring that must appear in the symbol name.
        :param substring_matching: when True, match any symbol whose name contains
            *name_pattern* (case-sensitive).
        :param kind_filter: optional list of ``SymbolKind`` integer values.
            If provided, only entries matching one of the kinds are returned.
        :return: list of matching entries.
        """
        # collect candidate entries
        if substring_matching:
            entries = self._lookup_substring(name_pattern)
        else:
            entries = list(self._by_name.get(name_pattern, []))

        # apply kind filter
        if kind_filter:
            kind_set = set(kind_filter)
            entries = [e for e in entries if e.kind in kind_set]

        return entries

    def _lookup_substring(self, pattern: str) -> list[SymbolIndexEntry]:
        """Return all entries whose symbol name contains *pattern*."""
        result: list[SymbolIndexEntry] = []
        for name, entries in self._by_name.items():
            if pattern in name:
                result.extend(entries)
        return result

    def files_for_symbol(self, name: str) -> set[str]:
        """Return the set of file paths that contain a symbol with the given exact *name*."""
        return {e.relative_path for e in self._by_name.get(name, [])}

    def symbols_in_file(self, relative_path: str) -> set[str]:
        """Return the set of symbol names defined in the given file."""
        return set(self._by_file.get(relative_path, set()))

    def iter_all_entries(self) -> Iterator[SymbolIndexEntry]:
        """Iterate over every entry in the index."""
        for entries in self._by_name.values():
            yield from entries

    # -- persistence -----------------------------------------------------------

    def save(self, path: str, extra_version: Hashable = None) -> None:
        """Persist the index to disk using the cache serialisation pattern.

        :param path: file path for the cache file.
        :param extra_version: optional extra version component to combine with the
            built-in index version (e.g. an LS-specific fingerprint).
        """
        version = (INDEX_VERSION, extra_version) if extra_version is not None else INDEX_VERSION

        # store as plain lists of tuples for compact pickling
        serialisable = {name: [self._entry_to_tuple(e) for e in entries] for name, entries in self._by_name.items()}
        save_cache(path, version, serialisable)
        log.info("Saved symbol index with %d entries across %d files to %s", self.total_entries, self.total_files, path)

    @classmethod
    def load(cls, path: str, extra_version: Hashable = None) -> SymbolIndex | None:
        """Load a previously persisted index from disk.

        :param path: file path of the cache file.
        :param extra_version: must match the value used when saving.
        :return: the loaded index, or ``None`` if the file is missing or version-mismatched.
        """
        version = (INDEX_VERSION, extra_version) if extra_version is not None else INDEX_VERSION
        try:
            data = load_cache(path, version)
        except (FileNotFoundError, OSError):
            return None
        if data is None:
            return None

        index = cls()
        for name, tuple_entries in data.items():
            for t in tuple_entries:
                entry = cls._tuple_to_entry(t)
                index.add(entry)

        log.info("Loaded symbol index with %d entries across %d files from %s", index.total_entries, index.total_files, path)
        return index

    # -- JSON export -----------------------------------------------------------

    def export_json(self, path: str) -> None:
        """Export the index as a human-readable JSON file.

        :param path: destination file path.
        """
        data = {name: [e.to_dict() for e in entries] for name, entries in sorted(self._by_name.items())}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info("Exported symbol index as JSON to %s", path)

    # -- internal helpers ------------------------------------------------------

    @staticmethod
    def _entry_to_tuple(entry: SymbolIndexEntry) -> tuple[str, str, str, int, int, str | None]:
        """Convert an entry to a compact tuple for serialisation."""
        return (entry.name, entry.name_path, entry.relative_path, entry.kind, entry.line, entry.parent_name_path)

    @staticmethod
    def _tuple_to_entry(t: tuple[str, str, str, int, int, str | None]) -> SymbolIndexEntry:
        """Reconstruct an entry from its serialised tuple form."""
        return SymbolIndexEntry(name=t[0], name_path=t[1], relative_path=t[2], kind=t[3], line=t[4], parent_name_path=t[5])
