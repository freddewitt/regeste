# Regeste

**AI-vision transcription and description of archival documents.**

Regeste batch-transcribes and describes scanned archival images (OCR + content description) using an AI vision model, then lets you **review, validate, translate and export** the corpus in standard archival formats. The same core is exposed through a **graphical interface** (PySide6) and an **interactive CLI**, with crash-safe resume, live cost tracking, and a UI available in 9 languages.

Built for work on archival record groups (for example the *3 U 794 "Le Corbeau"* fonds), multi-provider: **Claude, Gemini, OpenAI, and local models**.

> **Command-line usage:** see the dedicated guide **[cli.md](cli.md)**.
>
> **Changes:** see the **[CHANGELOG](CHANGELOG.md)**.

---

## Features

### Transcription (OCR)
- **Batch** transcription and description of archival images with an AI vision model.
- **Multi-provider**: Claude (Anthropic), Gemini (Google), OpenAI, and **local** models (LM Studio, llama.cpp, Ollama) through an OpenAI-compatible client.
- **Editable OCR prompt** (paleographic instructions by default), with reset-to-default.
- **Automatic document-language detection.**
- Optional **image preprocessing**: deskew, denoise, contrast enhancement, upscaling (OpenCV / Real-ESRGAN as optional dependencies), adaptive resizing.
- **Crash-safe resume**: state is saved continuously to `regeste.json`; an interrupted run picks up exactly where it stopped.
- **Live cost tracking**, spend ceiling, parallel processing (workers).

### Review and validation (graphical interface)
- Per-field correction and **validation**.
- **Bulk validation** with a confidence threshold and sampling.
- Compare OCR outputs across providers, promote one output to the reference transcription.
- Piece image preview.

### Translation (graphical interface and CLI)
- **Dedicated translation model**: the same as OCR, or a separate model (cloud or local providers).
- **Editable translation prompt** with placeholders (source/target language, glossary, entities to preserve).
- **Corpus glossary** and validated **named entities** re-injected into every translation for consistent terminology.
- **Auto-detected source language** (from OCR), editable by hand.

### Exports
- **4 base formats**: Markdown, plain text, JSON, and **searchable PDF** (real selectable text / Ctrl+F), as a combined file and/or one file per image.
- **12 archival formats** built from the pivot model: **EAD (XML), Dublin Core (XML), METS/PREMIS, light/full CSV, XLSX, SQLite, HTML, ZIP, Markdown (Obsidian), consultation PDF**, and a **review journal**.

### Interface
- **Graphical interface** (PySide6) and **interactive CLI** sharing the same core.
- **9 UI languages** with hot switching (no restart): `en`, `fr`, `de`, `es`, `pt`, `ja`, `zh`, `ar` (right-to-left), `ru`.

---

## Installation

Python **3.11 or newer** is required (see `pyproject.toml`).

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

`pip install -e .` installs Regeste in editable mode (handy for development); `pip install .` does a regular install. Either way, a `regeste` executable is created in the virtual environment.

**Optional dependencies:**

```bash
pip install -e ".[preprocessing]"   # image preprocessing (OpenCV)
pip install -e ".[upscale]"         # quality upscaling (Real-ESRGAN)
pip install -e ".[dev]"             # test tooling (pytest, pytest-qt)
pip install -e ".[build]"           # standalone executable (PyInstaller)
```

---

## Running

```bash
regeste                # graphical interface (default)
regeste --cli          # interactive CLI — see cli.md
regeste --lang en      # force the UI language (otherwise detected via LANG/LC_ALL)
```

UI language codes:

| Code | Language | Code | Language |
|------|----------|------|----------|
| `en` | English | `ja` | 日本語 |
| `fr` | Français | `zh` | 中文 |
| `de` | Deutsch | `ar` | العربية (right-to-left) |
| `es` | Español | `ru` | Русский |
| `pt` | Português | | |

---

## Supported providers

- **Claude** (Anthropic) — API key required
- **Gemini** (Google) — API key required
- **OpenAI** — API key required
- **LM Studio**, **llama.cpp**, **Ollama** — via an OpenAI-compatible client (local `base_url`), **no API key**

> ⚠️ Claude/Gemini/OpenAI API keys are stored **in plain text** in `regeste.json` (the project file at the root of the source folder). This is a deliberate design choice, not an oversight: **do not share that file** if you have entered a key.

---

## The project concept

A **project = one source image folder**. At its root, Regeste keeps a `regeste.json` file rewritten continuously (atomically) that holds the entire configuration (providers, models, prompts, settings) and the state of each image (pending / done / error, transcription, language, costs). The `data/` folder holds validations and translations.

There is no "Save" button: **saving is continuous**. To reopen a project, just point Regeste at the same folder — it resumes in **Resume mode**.

---

## Standalone executable (PyInstaller)

For a machine without a Python environment:

```bash
pip install -e ".[build]"
pyinstaller regeste.spec
```

The `regeste` executable is produced in `dist/`. It bundles the graphical interface, the CLI (`./dist/regeste --cli`) and the translation catalogs for all 9 languages. PyInstaller does **not** cross-compile: run the build on each target system (macOS, Windows, Linux) to get its executable.

---

## Contributing a translation

`scripts/extract_translations.py` regenerates the `regeste/locale/regeste.pot` catalog from the source strings, then updates each `.po` file without losing existing translations. After translating a `.po`, compile it to `.mo`:

```bash
pybabel compile -d regeste/locale -D regeste
```

---

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests make **no real API calls**: all providers are mocked.
