---
description: "Enable C preprocessor expansion to index macro-heavy codebases like Watcom C and Win32."
---

# C Preprocessor Support

## Problem

C files that use macros as calling-convention qualifiers (between the return type and function name) produce `ERROR` nodes in tree-sitter, causing functions to be silently dropped from the index:

```c
// tree-sitter cannot parse this without macro expansion
br_pixelmap * BR_RESIDENT_ENTRY BrPixelmapAllocate(br_uint_8 type, br_int_32 w, br_int_32 h, void *pixels, int flags)
{
    ...
}
```

This pattern is common in Watcom C, Win32 (`WINAPI`, `CALLBACK`, `PASCAL`), GLib/GTK, and any project that encodes calling convention or visibility via macros.

The built-in parse recovery names most functions with a single macro between the return type and the name. What it cannot recover is a name the preprocessor builds, such as a method-declaration macro that token-pastes its arguments:

```c
#define BR_CMETHOD_DECL(t, m) BR_METHOD _M_##t##_##m

br_error BR_CMETHOD_DECL(br_device_pixelmap_mem, allocateSub)(br_device_pixelmap *self, ...)
```

Without expansion this function is never indexed as `_M_br_device_pixelmap_mem_allocateSub`, so nothing that calls it through that name links to it.

## Solution

Pass `--c-preprocess` (or set `CGR_C_PREPROCESS=1`) to run [pcpp](https://github.com/ned14/pcpp) over each `.c` file before tree-sitter sees it. Macro expansion produces syntactically clean C that parses correctly.

## Installation

```bash
uv add "code-graph-rag[c-preprocess]"
# or
pip install "code-graph-rag[c-preprocess]"
```

## Usage

```bash
cgr start --update-graph \
  --repo-path /path/to/repo \
  --c-preprocess \
  --c-define BR_RESIDENT_ENTRY= \
  --c-define BR_PUBLIC= \
  --c-include-dir inc \
  --c-include-dir ddi_inc
```

| Flag | Description |
|------|-------------|
| `--c-preprocess` | Enable C preprocessing via pcpp |
| `--c-define KEY=VAL` | Define a macro before preprocessing (repeatable). Use `KEY=` to strip a calling-convention macro. |
| `--c-include-dir DIR` | Extra include directory relative to the repo root (repeatable). |

The flags exist on `cgr start` and `cgr index`. Every other entry point that indexes (watch mode, the MCP server, workspace sync) reads the same settings from the environment:

| Variable | Equivalent |
|----------|------------|
| `CGR_C_PREPROCESS=1` | `--c-preprocess` |
| `CGR_C_INCLUDE_DIRS=inc,core/inc` | `--c-include-dir`, comma-separated |
| `CGR_C_DEFINES=__GNUC__=4,BR_PUBLIC=` | `--c-define`, comma-separated |

## Include path auto-detection

Without `--c-include-dir`, include paths are detected automatically in this order:

1. `compile_commands.json` at the repo root (emitted by CMake, Meson, Bazel)
2. Directories named `inc`, `include`, `headers`, or `ddi_inc` at the repo root

## BRender example

```bash
cgr start --update-graph \
  --repo-path ~/code/BRender/BRender-v1.3.2 \
  --c-preprocess \
  --c-define BR_RESIDENT_ENTRY= \
  --c-define BR_PUBLIC_ENTRY= \
  --c-define BR_PUBLIC= \
  --c-define BR_EXPORT= \
  --c-define BR_CALLBACK= \
  --c-define BR_ASM_DATA=
```

The `inc/` and `ddi_inc/` directories are auto-detected from the repo root.

Two things decide whether expansion actually happens, and both fail quietly (the file is indexed from its raw source instead):

- **Every header that defines the macro must be found.** A CMake project that adds per-module include directories (BlazingRenderer puts each module's `ddi/` on the path) needs those passed with `--c-include-dir`, or a `compile_commands.json` at the repo root.
- **Compiler-identity checks must pass.** A header that ends in `#error "Unknown Compiler"` stops expansion when no compiler macro is defined. pcpp defines none, so pass one, for example `--c-define __GNUC__=4`.

## Limitations

- Uses pcpp (pure-Python); complex token-pasting (`##`) and variadic macros may not expand correctly.
- Line numbers in the indexed output refer to the expanded source, not the original. For simple calling-convention macros that expand to nothing, line numbers stay accurate.
- Only `.c` (and `.C`) files are preprocessed; headers are not indexed through the preprocessor, although their contents are expanded into each `.c` file that includes them.
- After changing any of these settings, run `cgr delete-project --repo-path <repo>` (removes that one project and its hash cache) and index it again, so every C file is parsed under the new configuration. Avoid `--clean` for this: it deletes every project in the shared graph.
