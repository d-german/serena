# Serena Setup Guide & Fork Changes

## Quick Start: Setting Up Serena for C++ and C# Projects

### Prerequisites

- **Python 3.10+** with `uv` / `uvx` installed
- **VS Code** with GitHub Copilot (Serena runs as an MCP server)
- For C++: clangd is bundled by Serena (auto-downloaded on first use)
- For C#: .NET SDK installed (Roslyn/OmniSharp is bundled by Serena)

### Step 1: Configure MCP

Add Serena to your VS Code MCP settings (`.vscode/mcp.json` or user settings):

```json
{
  "servers": {
    "serena": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/d-german/serena.git@fork/csharp-large-repo", "serena",
               "--project", "C:/path/to/your/project"]
    }
  }
}
```

### Step 2: Create `.serena/project.yml`

```yaml
name: YourProject
languages:
  - cpp        # for C++ projects
  - csharp     # for C# projects (include both for polyglot repos)
active_workspace: "YourSolution.sln"  # required for C# — point to a .sln file
```

### Step 3: Set Up C++ Include Paths

clangd needs to know your include paths and compiler flags. You have two options:

#### Option A: Generate `compile_commands.json` (Recommended)

This gives clangd per-file compiler flags and enables full background indexing of every source file.

**If you can build the project:**

```powershell
pip install compiledb
compiledb msbuild YourSolution.sln /p:Configuration=Debug
```

**If you cannot build (MSBuild/.vcxproj projects):**

Generate a synthetic `compile_commands.json` from your include paths:

```powershell
cd C:\path\to\project

# Define your compiler flags
$flags = @("-std=c++17", "--target=x86_64-pc-windows-msvc", "-DWIN32", "-D_WINDOWS",
           "-DUNICODE", "-D_UNICODE", "-D_CRT_SECURE_NO_WARNINGS")

# Extract include paths from your .vcxproj AdditionalIncludeDirectories
$includes = @("INC_Inc", "INC_DLLINC", "LIB_Base", "LIB_Components") |  # <-- your dirs here
    ForEach-Object { "-IC:/path/to/project/$_" }

$allFlags = ($flags + $includes) -join " "

# Find all C/C++ source files
$files = Get-ChildItem . -Recurse -Include *.cpp,*.c -File

# Generate compile_commands.json
$sb = [System.Text.StringBuilder]::new(10MB)
[void]$sb.AppendLine("[")
$first = $true
foreach ($f in $files) {
    $fp = $f.FullName -replace '\\','/'
    if (-not $first) { [void]$sb.AppendLine(",") }
    [void]$sb.Append("  {`"directory`": `"C:/path/to/project`", `"command`": `"clang++ $allFlags $fp`", `"file`": `"$fp`"}")
    $first = $false
}
[void]$sb.AppendLine()
[void]$sb.AppendLine("]")
[System.IO.File]::WriteAllText("compile_commands.json", $sb.ToString(), [System.Text.Encoding]::UTF8)

Write-Host "Created compile_commands.json ($($files.Count) entries)"
```

To find your include directories, check your `.vcxproj` files:

```powershell
Select-String -Path "**/*.vcxproj" -Pattern "AdditionalIncludeDirectories" |
  Select-Object -First 1 | ForEach-Object { $_.Line.Trim() }
```

Convert each semicolon-separated path to an entry in the `$includes` array.

#### Option B: Create a `.clangd` file (Simpler but less complete)

Place a `.clangd` file in the project root. This gives clangd include paths for files it parses on-demand, but does **not** trigger full background indexing of all files.

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
    - -I/path/to/project/INC_Inc
    - -I/path/to/project/LIB_Components
    # ... add all AdditionalIncludeDirectories

Diagnostics:
  Suppress: ['*']   # Optimize for navigation, not build correctness

Index:
  Background: Build
```

### Option A vs Option B

| Feature | `compile_commands.json` | `.clangd` only |
|---------|------------------------|----------------|
| Per-file flags | ✅ Yes | ❌ Same flags for all files |
| Background index | ✅ Indexes ALL source files | ❌ Only indexes files opened by Serena |
| `find_referencing_symbols` | Complete cross-references | Incomplete — misses files never opened |
| Setup effort | More work (script above) | Minimal |
| Disk usage | ~35 MB index for 4K files | Negligible |

**Recommendation:** Use `compile_commands.json` for any serious codebase. The `.clangd` file alone is fine for quick exploration but will miss cross-references.

### Step 4: Activate and Index

1. In VS Code Copilot chat, activate the project (Serena does this automatically on first tool use)
2. clangd will discover `compile_commands.json` and begin background indexing
3. Monitor progress:

```powershell
# Check clangd CPU/memory (high CPU = actively indexing)
Get-Process -Name "clangd*" | Select-Object Id, CPU, @{N='MB';E={[math]::Round($_.WorkingSet64/1MB)}}

# Check index directory (appears once indexing starts)
Get-ChildItem "C:\path\to\project\.cache\clangd\index" -File | Measure-Object | Select-Object Count
```

4. When clangd's memory drops back to ~30MB and CPU stops climbing, indexing is done
5. Index is stored at `<project>/.cache/clangd/index/` (persists across sessions)

### Step 5: Force Re-index (if needed)

If you change `.clangd` or `compile_commands.json` after clangd is already running, kill the old clangd process so Serena spawns a fresh one:

```powershell
# Find and kill old clangd
Get-Process -Name "clangd*" | Stop-Process -Force

# Then use any Serena tool — it will spawn a new clangd that picks up the updated config
```

### C# Setup Notes

- Set `active_workspace` in `project.yml` to a `.sln` file containing your C# projects
- Roslyn starts automatically — no additional configuration needed
- If the solution contains multiple `.sln` files, pick the one that includes the projects you care about
- Roslyn provides full cross-references out of the box (no compile database needed)

---

## Fork Changes

This fork (`fork/csharp-large-repo`) contains fixes for using Serena with large C++ monorepos (tested on a ~190K-symbol, 8,400-file codebase) and polyglot C++/C# projects.

### Bug Fix 1: Symbol index not persisting during `project index`

**File:** `src/solidlsp/ls.py` — `_save_symbol_index()`

**Problem:** When `project index` runs, intermediate saves occur every 30 seconds. If the symbol index already existed on disk but new files had been parsed (marked dirty), `_save_symbol_index()` would skip the patch step. This caused the index to contain only symbols from files processed before the first save.

**Symptom:** Symbol index had 784 entries from 2 directories instead of 190K entries from 6,970 files.

**Fix:** Added `elif self._index_dirty_files: self._patch_symbol_index()` to handle the case where the index exists but has dirty (newly-parsed) files pending.

### Bug Fix 2: Symbol index never built when doc cache is warm

**File:** `src/solidlsp/ls.py` — `save_cache()`

**Problem:** When re-indexing with a warm document symbols cache, `request_document_symbols()` returns cached results without setting dirty flags. The `save_cache()` method only triggered an index build when `_symbol_index_needs_save` was true, but with all cache hits, this flag was never set — so the index was never built.

**Fix:** Changed the condition to also trigger index build when `self._symbol_index is None and self._document_symbols_cache` (i.e., no index exists but we have cached symbol data to build from).

## Symbol Index Architecture

The symbol index is an inverted index with two structures:

- **Forward index** (`_by_name`): symbol name → list of `SymbolIndexEntry` (file, line, kind, name_path)
- **Reverse index** (`_by_file`): file path → set of symbol names

The reverse index enables O(1) file removal during incremental updates. When a file changes, all its old symbols are removed via the reverse index, then new symbols are added from the fresh LSP parse.

**Threshold:** If more than 20 files are dirty (`_INDEX_PATCH_THRESHOLD`), Serena does a full rebuild instead of incremental patching.

## Tested Scale

- **190,202** unique symbol names
- **369,215** total index entries
- **6,970** indexed files (C++ via clangd + C# via Roslyn)
- **8,405** total source files in doc cache
- **8,226** clangd background index files (35 MB) from `compile_commands.json`
- **4,031** C/C++ source files in compile database
- Incremental updates verified: extract classes to new file → index correctly reflects the move
- Cross-references verified: `HL7EncodingRules` returns 9 referencing files (vs 4-5 without background index)
