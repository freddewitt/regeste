# Changelog

All notable changes to Regeste are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] – 2026-07-18

### Added
- **Translation catalogs completed** — 26 new UI strings for the Review simple/advanced modes, Verbose logging toggle, Translation batch tab, Language selector in Export, Settings tab (3 sub-tabs OCR/Translation/General), and enriched Session 13 logs (starting/processing/token and cost details). Translated into 8 languages (fr, de, es, pt, ja, zh, ar, ru): 208 new translations, 0 fuzzy, 0 untranslated across all 9 catalogs.

### Changed
- **Version bump to 0.2.0** — project now at v0.2.0.

### Performance
- **Real-ESRGAN model caching** — `RealESRGANer` instance is now cached per `(model_name, device)` pair instead of being reloaded from disk for every image, eliminating redundant model loading during batch upscaling.
- **Cost tracking O(n²) → O(1)** — `CostTracker.total_cost` now maintains a running `_cumulative_cost` updated in `record()`, avoiding a `sum()` over the entire history on every call. Same for input/output token totals.
- **JPEG quality search linear → dichotomic** — replaced the linear descent (up to 6 full encodes) with a binary search on quality (range 10–95, max 8 iterations), significantly reducing re-encode overhead for oversized non-JPEG images.
- **Image reader reuse in PDF export** — when both combined and per-file PDF exports are requested, the source image is opened once and shared between both passes via an `image_cache` dict, halving disk I/O for the image-heavy path.
- **Combined text/description/language parsing** — `parse_all()` performs a single regex scan over the model response to extract all three fields (`## TEXT`, `## DESCRIPTION`, `## LANGUAGE`), replacing the previous double-scan pattern. The existing `parse_text_description()` and `parse_language()` are preserved as thin wrappers.
- **HTTP session reuse** — `OpenAICompatProvider` now uses a persistent `requests.Session()` for all API calls and model-listing requests, reusing TCP connections instead of opening a new one per request.
- **Lazy SDK imports** — heavy imports (`anthropic`, `google.genai`, `openai`) moved from module level into the constructors of their respective `TranslationProvider` subclasses, reducing startup overhead when only one provider is configured.
- **Pre-process / network separation** — `Transcriber` now separates image pre-processing (CPU-bound) from provider API calls (I/O-bound); a TODO marks the future dual-pool path.
- **HTML export search index** — the client-side search in HTML exports now uses a precomputed `search_index[]` of keyword strings per piece (title, description, shelfmark, etc.) instead of `JSON.stringify(p).toLowerCase().includes(q)` on the entire piece data.
- **Corpus cache in GUI** — all panels (Review, Translation, Export) now share a central corpus cache in `MainWindow.get_corpus()`, invalidated on project changes, instead of each panel calling `load_corpus()` independently.

## [0.1.2] – 2026-07-14

### Added
- **Settings tab** — the old "Settings..." button/modal dialog in Transcription is now a permanent tab, positioned just before Log, with three sub-tabs: OCR (provider/model, OCR prompt, image resizing/preprocessing, forced document language, costs, worker count), Translation (provider/model, "same model as OCR" checkbox), and General (interface language). A "Save settings" button replaces the old OK/Cancel.

### Changed
- The "Logs" tab is now labelled "Log".

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
