# Fork Changes: Large C++ / Polyglot Repo Support

This fork (`fork/csharp-large-repo`) contains fixes and documentation for using Serena with large C++ monorepos (tested on a ~190K-symbol, 8,400-file codebase) and polyglot C++/C# projects.

## Bug Fixes

### 1. Symbol index not persisting during `project index` (incremental save bug)

**File:** `src/solidlsp/ls.py` — `_save_symbol_index()`

**Problem:** When `project index` runs, intermediate saves occur every 30 seconds. If the symbol index already existed on disk but new files had been parsed (marked dirty), `_save_symbol_index()` would skip the patch step. This caused the index to contain only symbols from files processed before the first save.

**Symptom:** Symbol index had 784 entries from 2 directories instead of 190K entries from 6,970 files.

**Fix:** Added `elif self._index_dirty_files: self._patch_symbol_index()` to handle the case where the index exists but has dirty (newly-parsed) files pending.

### 2. Symbol index never built when doc cache is warm (cache-hit bug)

**File:** `src/solidlsp/ls.py` — `save_cache()`

**Problem:** When re-indexing with a warm document symbols cache, `request_document_symbols()` returns cached results without setting dirty flags. The `save_cache()` method only triggered an index build when `_symbol_index_needs_save` was true, but with all cache hits, this flag was never set — so the index was never built.

**Fix:** Changed the condition to also trigger index build when `self._symbol_index is None and self._document_symbols_cache` (i.e., no index exists but we have cached symbol data to build from).

## MSBuild / .vcxproj Projects: `.clangd` Configuration

**This applies to any Serena installation (upstream or fork) using clangd with MSBuild-based C++ projects.**

### The Problem

clangd needs to know include paths, preprocessor defines, and compiler flags for each file. CMake projects provide this via `compile_commands.json` automatically. MSBuild/`.vcxproj` projects do **not** — clangd falls back to heuristics and misses cross-directory includes.

**Impact:** `find_referencing_symbols` and `find_implementations` return incomplete results because clangd can't resolve `#include` directives across project boundaries.

### The Solution

Create a `.clangd` file in the project root with include paths extracted from `.vcxproj` files:

```yaml
CompileFlags:
  Add:
    - -std=c++17
    - --target=x86_64-pc-windows-msvc
    - -DWIN32
    - -D_WINDOWS
    - -DUNICODE
    - -D_UNICODE
    - -D_CRT_SECURE_NO_WARNINGS
    # Add all AdditionalIncludeDirectories from your .vcxproj files:
    - -I/path/to/project/INC_Inc
    - -I/path/to/project/INC_DLLINC
    - -I/path/to/project/LIB_Components
    # ... etc

Diagnostics:
  Suppress: ['*']  # We're optimizing for navigation, not build correctness

Index:
  Background: Build
```

### How to extract include paths

Look for `<AdditionalIncludeDirectories>` in your `.vcxproj` files:

```powershell
Select-String -Path "**/*.vcxproj" -Pattern "AdditionalIncludeDirectories" |
  Select-Object -First 1 |
  ForEach-Object { $_.Line.Trim() }
```

Convert each semicolon-separated path to a `-I/absolute/path` entry in `.clangd`.

### Ideal alternative: `compile_commands.json`

If you **can** build the project locally:

```powershell
pip install compiledb
compiledb msbuild YourSolution.sln /p:Configuration=Debug
```

This produces a `compile_commands.json` with exact per-file flags — better than `.clangd` but requires a successful build.

### Setup order matters

1. Create `.clangd` (or `compile_commands.json`) — **before** indexing
2. Configure `.serena/project.yml` with all languages
3. Run `project index`

The `.clangd` file must exist before indexing because `project index` asks clangd for symbols, and clangd needs the include paths to parse files correctly.

## Polyglot Projects (C++ + C#)

For repositories containing both C++ and C# code, add both languages to `.serena/project.yml`:

```yaml
languages:
  - cpp
  - csharp
```

And set `active_workspace` to a `.sln` file that contains the C# projects:

```yaml
active_workspace: "YourSolution.sln"
```

This starts both clangd (for C++) and Roslyn (for C#), giving full symbol coverage across both languages.

## Symbol Index Architecture

The symbol index is an inverted index with two structures:

- **Forward index** (`_by_name`): symbol name → list of `SymbolIndexEntry` (file, line, kind, name_path)
- **Reverse index** (`_by_file`): file path → set of symbol names

The reverse index enables O(1) file removal during incremental updates. When a file changes, all its old symbols are removed via the reverse index, then new symbols are added from the fresh LSP parse.

**Threshold:** If more than 20 files are dirty (`_INDEX_PATCH_THRESHOLD`), Serena does a full rebuild instead of incremental patching.

## Tested Scale

- **190,202** unique symbol names
- **369,215** total index entries
- **6,970** indexed files
- **8,405** total source files in doc cache
- Incremental updates verified: extract classes to new file → index correctly reflects the move
