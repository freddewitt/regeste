# Changelog

All notable changes to Regeste are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] – 2026-07-14

### Added
- **Review tab, simple/advanced modes** — a always-visible simple view (enlarged image, transcription, image description, Validate/Reject/Hold buttons applying to the whole piece at once, "View image" opening the system viewer) with an "Advanced" checkbox revealing the existing per-field editors unchanged. The piece list shows a status dot per entry and is sorted pending → validated → rejected.
- **Verbose diagnostic logging** — a "Verbose" checkbox in the Logs tab switches the logger to DEBUG, surfacing exhaustive request/response, retry, image-processing, and registry-write logs for troubleshooting. An always-visible warning now signals when OpenCV is missing and a requested preprocessing step was silently skipped.
- **Translation tab, corpus-level batch launcher** — repositioned right after Review, before Export. Replaces the single-piece workbench with: target language, scope ("validated pieces only" or "all pieces"), an inline editable translation prompt, a progress bar, and a run log. Writes go through the same `translate_piece`/pivot mechanism as before, one worker thread per batch.
- **Translation content in exports** — a "Language" selector in the Export tab includes the selected language's translation in CSV (light/full), XLSX, Markdown, Markdown (Obsidian), HTML, consultation PDF, and SQLite when available. EAD, Dublin Core, and METS now also list every available language (source + translations) via a `<language>` element.
- **README screenshot** of the main window.

## [0.1.0] – 2026-07-14

Initial development version.

### Added
- **OCR pipeline** — batch transcription and description of archival images with an AI vision model. Multi-provider: Claude (Anthropic), Gemini (Google), OpenAI, and local models (LM Studio, llama.cpp, Ollama) through an OpenAI-compatible client.
- **Crash-safe resume** — project state is saved continuously to `regeste.json`; an interrupted run resumes exactly where it stopped. Live cost tracking, spend ceiling, and parallel workers.
- **Image preprocessing** — deskew, denoise, contrast enhancement, upscaling (OpenCV / Real-ESRGAN as optional dependencies) and adaptive resizing.
- **Editable OCR prompt** — a dedicated dialog to edit, reset to default, and save the OCR prompt (paleographic instructions by default), opened from the Providers and models tab.
- **Document-language detection** — the OCR run detects the document language, stores it on the pivot model, and pre-fills the translation source language.
- **Review and validation (graphical interface)** — per-field correction and validation, bulk validation with a confidence threshold and sampling, multi-provider OCR comparison, and promoting an output to the reference transcription.
- **Translation** — a dedicated translation model (the same as OCR or a separate cloud/local model), an editable and persisted translation prompt with placeholders, a corpus glossary and validated named entities re-injected into every translation, and an auto-detected, editable source language.
- **Exports** — 4 base formats (Markdown, plain text, JSON, searchable PDF) plus 12 archival formats built from the pivot model: EAD (XML), Dublin Core (XML), METS/PREMIS, light/full CSV, XLSX, SQLite, HTML, ZIP, Markdown (Obsidian), consultation PDF, and a review journal. Available from both the graphical interface and the CLI.
- **Command-line interface** — an interactive CLI sharing the same core as the graphical interface: project configuration, transcription, headless translation into one or several comma-separated target languages, and export to the 12 archival formats. Project settings can be edited when resuming, and translation providers offer model listing. See [cli.md](cli.md).
- **Internationalization** — 9 UI languages (`en`, `fr`, `de`, `es`, `pt`, `ja`, `zh`, `ar` right-to-left, `ru`) with hot switching (no restart).
- **Documentation** — [README](README.md) and a dedicated CLI guide ([cli.md](cli.md)).

### Changed
- The translation provider/model selector lives in the Providers and models settings tab, next to the OCR selector (rather than in the Translation tab).
- The default OCR prompt produces a markdown `## TEXT` / `## DESCRIPTION` / `## LANGUE` output contract.
- The archival exporter registry is shared between the graphical interface and the CLI, so formats are defined in a single place.

### Fixed
- The window no longer opens taller than the screen: tab pages are scrollable and the default size is clamped to the available screen area.
- Removed a duplicate log panel from the Transcription tab; logs remain in the dedicated Logs tab.
- Completed all 9 translation catalogs: corrected mismatched fuzzy entries (including the English identity locale) and filled in untranslated strings.
