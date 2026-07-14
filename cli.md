# Regeste on the command line (CLI)

The Regeste CLI offers the same core as the graphical interface — **sorting, OCR, transcription, translation, export** — in an **interactive** text session, suited to headless work (remote server, batch processing, scripting).

> Back to the [README](README.md).

## Launching the CLI

```bash
regeste --cli
```

To force the UI language (otherwise detected via `LANG` / `LC_ALL`):

```bash
regeste --cli --lang en
```

The CLI is **interactive**: it asks a series of questions and waits for your answers. For yes/no questions, a default is shown in brackets (`[Y/n]`); pressing Enter accepts the default.

---

## Walkthrough — new project

A **project = one image folder**. The first time you open a folder, the CLI walks you through the configuration.

1. **Source folder** — the path to the folder holding the images to transcribe.
2. **Project name** and **output folder** (for exports).
3. **OCR provider and model**:
   - pick the provider (Claude, Gemini, OpenAI, LM Studio, llama.cpp, Ollama);
   - API key (for cloud providers) or server URL (for local models);
   - **the CLI lists the provider's available models**; you pick one, or type an identifier by hand.
4. **Forced document language** (optional) — leave empty to let the model detect it.
5. **OCR prompt** — use the default prompt (paleographic instructions + output format), or enter a custom one.
6. **Translation model**:
   - **"Use the same model for translation?"** — if yes, translation reuses the OCR model;
   - if no, you pick a separate provider + model (with model listing, or manual entry).
7. **Translation prompt** — default, or custom.
8. **Image preprocessing** — deskew, denoise, contrast, upscaling (and quality upscaling if available).
9. **Resizing** — disable adaptive resizing, force a max pixel dimension, or a max file size in bytes.
10. **Parallel workers** and **spend ceiling** (optional, in $).
11. **Base export formats** — among `md`, `txt`, `json`, `pdf` (comma-separated), as a combined file and/or one file per image.

The CLI then shows the **number of files to process** and a rough **cost estimate**, and asks **"Start now?"**.

During transcription, it shows **per-file progress** and the **running cost**. At the end, it writes the base exports and lists their paths.

---

## Translation (headless)

After transcription and export, the CLI offers:

> **"Translate the transcribed pieces now?"**

If you answer yes:

- **Target language(s)** — you can give **several, comma-separated** (e.g. `en, de, es`): each piece is translated into all of them in a single pass.
- The CLI **warns that translation runs on raw OCR, without human review** — because the CLI has no review/validation step (see below).
- The corpus glossary, if any, is re-injected into every translation.

Translations are saved into the project's `data/` folder (and are therefore available afterwards in the graphical interface).

---

## Exporting to archival formats

Finally, the CLI offers:

> **"Export to archival formats now?"**

If yes, you pick from the 12 formats (comma-separated) and a destination folder:

`ead`, `dc` (Dublin Core), `mets`, `csv_light`, `csv_full`, `xlsx`, `zip`, `markdown`, `markdown_obsidian`, `sqlite`, `html`, `pdf`.

The produced files are listed at the end.

---

## Reopening a project — Resume mode

If the source folder already contains a `regeste.json`, the CLI detects the existing project and asks **"Resume this project?"**.

- **Resuming** keeps the configuration and **picks up where transcription stopped** (images already done are not reprocessed; failed images are retried; new images added to the folder are picked up).
- The CLI then asks **"Modify the project settings?"** — if yes, it re-runs the full configuration and rewrites `regeste.json` (if you abort midway, the previous configuration is kept).

---

## What the CLI does not cover

The CLI runs the whole **sorting → OCR → transcription → translation → export** pipeline, but **not review**:

- **Review / validation** (per-field correction, bulk validation, comparing OCR outputs, promoting an output) is only in the **graphical interface**. In the CLI, translation therefore runs on unreviewed OCR.
- **Glossary editing** and **named-entity management** are done in the graphical interface (the CLI *uses* the existing glossary but does not edit it).

For these steps, open the same project in the graphical interface (`regeste`), then return to the CLI if needed — both share the same project folder.

---

## Environment variables

- `LANG` / `LC_ALL` — default UI language (overridden by `--lang`).
