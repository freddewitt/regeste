# État des lieux — Regeste

**HEAD : 12e3de6 (2026-07-19) — v0.2.2**

---

## 1. Résumé

Regeste est une application Python de transcription et description d'archives par vision IA. Elle traite par lot les images d'un dossier source (documents d'archives, correspondance, registres manuscrits) via un modèle de vision externe (Claude, Gemini, OpenAI ou un serveur local compatible), produit des exports texte cherchables (md/txt/json/pdf-searchable) et des formats d'archives standardisés (EAD, DC, METS, CSV, XLSX, etc.). Un modèle pivot « Piece » permet la revue, la validation et la traduction du corpus avant export. La même logique métier est exposée par une interface graphique PySide6 et une CLI interactive. Le schéma registre v2 supporte désormais l'import multi-lots avec traçabilité du mode de transcription (Littéral/Hypothèses) par fichier. 288 tests automatisés.

---

## 2. Stack technique

| Couche | Technologie | Version minimale |
|---|---|---|
| Langage | Python | ≥ 3.11 |
| GUI | PySide6 | ≥ 6.7.0 |
| SDK Claude | `anthropic` | ≥ 0.40.0 |
| SDK Gemini | `google-genai` | ≥ 1.50.0 |
| SDK OpenAI | `openai` | ≥ 1.50.0 |
| Images | `Pillow` | ≥ 10.4.0 |
| Images HEIC | `pillow-heif` | ≥ 0.18.0 |
| PDF | `reportlab` | ≥ 4.2.0 |
| i18n | `Babel` | ≥ 2.16.0 |
| HTTP | `requests` | ≥ 2.32.0 |
| XLSX | `openpyxl` | ≥ 3.1.0 |

**Dépendances optionnelles :**
- `opencv-python-headless` ≥ 4.10.0 (preprocessing : deskew, denoise, contrast)
- `realesrgan` (upscaling qualité Real-ESRGAN)

**Build :** `pyinstaller` ≥ 6.0 (exécutable autonome via `regeste.spec`)

**Tests :** `pytest` ≥ 8.0.0, `pytest-mock` ≥ 3.14.0, `pytest-qt` ≥ 4.4.0

**Outil de build :** `setuptools` ≥ 68, build backend `setuptools.build_meta`

---

## 3. Architecture réelle

### Arborescence commentée

```
regeste/
├── __main__.py              # Point d'entrée CLI : GUI par défaut, --cli pour CLI, --lang, --mode
├── __init__.py
├── i18n.py                  # gettext + Babel, set_language(), _(), format_cost(), is_rtl(), 9 langues
│
├── core/                    # Cœur métier — ne dépend ni de gui/ ni de cli/
│   ├── __init__.py
│   ├── costs.py             # Rate, CostTracker (cumul O(1)), Projection, estimate_before_run()
│   ├── export.py            # Export OCR : md/txt/json/pdf, ExportOptions, export_registry()
│   ├── imaging.py           # load_image, preprocess (deskew→denoise→contrast→upscale),
│   │                        # resize_for_provider (binaire sur qualité JPEG), ProviderLimit,
│   │                        # cache RealESRGANer global
│   ├── project.py           # ProjectConfig, ProviderConfig — to_meta()/from_meta(), ~15 champs
│   ├── registry.py          # Registry (regeste.json), FileEntry/SourceInfo/TranscriptionInfo,
│   │                        # écriture atomique tempfile+os.replace, migration v1→v2 automatique,
│   │                        # display_name(), get_by_display_name(), record_result(), record_error()
│   ├── transcription_mode.py# TranscriptionMode (enum LITERAL/HYPOTHESES), HYPOTHESES_BLOCK
│   ├── transcriber.py       # Transcriber.run() — fenêtre glissante ThreadPoolExecutor, backoff
│   │                        # exponentiel, arrêt propre, plafond de dépense, résolution de prompt
│   │                        # par mode (LITERAL/HYPOTHESES)
│   └── providers/
│       ├── __init__.py      # PROVIDER_KINDS (6), REQUIRES_API_KEY_KINDS, DEFAULT_BASE_URLS
│       ├── base.py          # Provider (ABC), ModelInfo, TranscriptionResult, parse_all() regex unique
│       ├── claude.py        # ClaudeProvider (SDK anthropic), filtre familles vision
│       ├── gemini.py        # GeminiProvider (SDK google-genai), filtre generateContent
│       └── openai_compat.py # OpenAICompatProvider (OpenAI + LM Studio + llama.cpp + Ollama),
│                            # cache Ollama /api/show par base_url, heuristique de nom pour les autres
│
├── cli/
│   ├── __init__.py
│   └── app.py               # CLI interactive complète (~600 lignes) : flux nouveau/reprise,
│                            # configuration provider, transcription, traduction, export
│
├── gui/
│   ├── __init__.py          # run() — QApplication, thème (Fusion + QSS clair/sombre auto), RTL
│   ├── main_window.py       # MainWindow (QMainWindow) ~900 lignes : 6 onglets, menu Fichier,
│                            # orchestration run/import/export
│   ├── theme.py             # QSS clair + sombre, auto-switch via colorSchemeChanged
│   ├── worker.py            # TranscriptionWorker, ModelFetchWorker, ExportWorker,
│   │                        # TranslationWorker, TranslationBatchWorker, start_worker()
│   ├── prompt_dialog.py     # PromptEditDialog (édition du prompt OCR avec reset-to-default)
│   ├── import_dialog.py     # BatchImportDialog (sélection dossier, identifiant, sur place/copie,
│   │                        # résolution collision batch_id)
│   ├── import_worker.py     # BatchImportWorker (copie shutil.copy2 vers images/<batch_id>/,
│   │                        # alimentation registre v2, rollback si échec)
│   └── panels/
│       ├── __init__.py
│       ├── settings_panel.py# SettingsPanel : 4 sous-onglets (OCR/Translation/General/Costs),
│       │                    # graphique QPainter coûts, tableau des taux, plafond, workers
│       ├── export_panel.py  # ExportPanel : 12 formats pivot (EAD/DC/METS/CSV/XLSX/…),
│       │                    # sélecteur langue d'export, filtre validated_only
│       ├── review_panel.py  # ReviewPanel : liste triée avec pastille colorée + badge "H" pour
│       │                    # hypothèses, vue simple/avancée, 3 boutons groupe, validation par seuil
│       ├── translation_panel.py # TranslationPanel : lanceur batch corpus-level, prompt éditable
│       │                        # (masqué par défaut), glossaire, guards
│       └── log_panel.py     # LogPanel : logs centralisés (scroll/recherche/copie), mode Verbose
│
├── pivot/                   # Modèle pivot archivistique
│   ├── __init__.py          # Exporte Piece, CONTENT_FIELDS, build_pieces_from_registry, etc.
│   ├── models.py            # Piece, Event, FieldValidation, StatusChange, NamedEntity, Translation
│   │                        # hypothesis_mode: bool ajouté en phase 3
│   ├── build.py             # build_pieces_from_registry() : registre → Piece, dérive hypothesis_mode
│   ├── store.py             # load/save piece (atomique), load_corpus, bundle_corpus
│   ├── hashing.py           # hash_transcription(), is_translation_stale()
│   ├── atomic_io.py         # atomic_write() (tempfile + os.replace)
│   ├── status.py            # global_status() (calculé, pas stocké)
│   └── utils.py             # _now() (UTC ISO)
│
├── review/                  # Revue et validation
│   ├── __init__.py
│   ├── validation.py        # apply_field_validation(), apply_correction(), apply_group_status()
│   └── queue.py             # sorted_by_confidence(), sorted_for_review(), sample(), bulk_validate()
│
├── translation/             # Traduction
│   ├── __init__.py          # Exporte DEFAULT_TRANSLATION_PROMPT, create_translation_provider, etc.
│   ├── provider.py          # TranslationProvider (ABC), ClaudeTrans/GeminiTrans/OpenAICompatTrans
│   ├── translate.py         # translate_piece(), build_prompt() avec placeholders
│   ├── guards.py            # check_guards() — bloque si non validé, avertit si confiance basse
│   └── glossary.py          # load/save glossary (source_dir/data/glossary.json)
│
├── export/                  # 12 exporteurs pivot (read-only)
│   ├── __init__.py          # export_review_journal()
│   ├── formats.py           # PIVOT_EXPORTERS — registre commun GUI/CLI
│   ├── common.py            # helpers transverses
│   ├── mapping.py           # FIELD_MAPPING — table de correspondance Piece → EAD/DC/METS
│   ├── ead.py               # EAD (XML) — export_ead()
│   ├── dc.py                # Dublin Core (XML) — export_dublin_core()
│   ├── mets.py              # METS/PREMIS — export_mets()
│   ├── csv_export.py        # CSV light + full — export_csv_light(), export_csv_full()
│   ├── xlsx_export.py       # XLSX avec miniature — export_xlsx()
│   ├── xlsx_common.py       # insert_thumbnail()
│   ├── markdown_export.py   # Markdown + Obsidian
│   ├── html_export.py       # HTML avec recherche client-side (search_index[] précalculé)
│   ├── pdf_export.py        # Consultation PDF avec image native + texte sélectionnable
│   ├── sqlite_export.py     # SQLite
│   ├── zip_export.py        # ZIP avec images
│   └── journal.py           # Journal de revue (XLSX)
│
├── locale/                  # 9 langues
│   ├── regeste.pot          # Template (247 msgid, 2026-07-19)
│   └── {ar,de,en,es,fr,ja,pt,ru,zh}/LC_MESSAGES/regeste.{po,mo}
│
└── assets/
    └── screenshot.png       # Capture d'écran README
```

### Écarts par rapport à la spec d'origine

| Point de spec | État actuel | Raison / trace |
|--------------|-------------|----------------|
| Onglet Export (pivot 12 formats) | Déplacé dans l'onglet Transcription (repliable), puis promu en onglet « Export archive » autonome | Session 11 (replié), Session 19 (promu) |
| Onglet « Type de sortie » | Créé puis supprimé — les contrôles (combiné/par fichier, formats, mode) sont maintenant dans l'onglet Transcription | Session 18 (créé), Session 19 (intégré dans Transcription) |
| Dialogue Settings modal | Remplacé par onglet Settings permanent, 4 sous-onglets (OCR/Translation/General/Costs) | Session 11 |
| TranslationPanel mono-pièce | Remplacé par lanceur batch corpus-level | Session 10 |
| `FileEntry` champs plats | Remplacé par `SourceInfo`/`TranscriptionInfo` namespaces (schéma v2) | Session 19 (migration v2) |
| Lot unique implicite | Multi-lots via `source.batch_id`, clés préfixées | Session 19 |
| Aucun menu Fichier | Menu Fichier ajouté (Nouveau/Ouvrir/Enregistrer/Export/Quitter) | Session 19 |
| Aucun flux d'import | `BatchImportDialog` + `BatchImportWorker` | Session 19 |
| Prompt de traduction toujours visible | Masqué par défaut, bouton Afficher/Masquer | Session 18 |
| Pas de mode Hypothèses | `core/transcription_mode.py`, `HYPOTHESES_BLOCK`, mode par fichier, badge H dans Revue | Session 18 + Phase 3 |

---

## 4. Fonctionnalités implémentées et fonctionnelles

### Transcription OCR
- Batch transcription par fenêtre glissante (workers 1-64), `ThreadPoolExecutor`
- 6 providers : claude, gemini, openai, lm_studio, llama_cpp, ollama (via `OpenAICompatProvider`)
- Contrat de sortie : `## TEXT` / `## DESCRIPTION` / `## LANGUE` (parsing via `parse_all()` regex unique)
- Prompt système paléographique (français) par défaut, éditable et persisté
- Mode Littéral (prompt historique) et Hypothèses (bloc `## HYPOTHESES` ajouté, notation `[[...]]`)
- Détection des modèles vision : Ollama via `/api/show`, autres par heuristique de nom
- Redimensionnement adaptatif par provider (limites configurables dans `imaging.py`)
- Chaîne de preprocessing : deskew → denoise → contraste → upscale (ordre fixe)
- Real-ESRGAN pour upscaling qualité (optionnel, avec cache d'instance global)
- Plafond de dépense avec arrêt automatique
- Sauvegarde atomique du registre après chaque fichier
- Arrêt propre : les tâches non soumises ne démarrent pas après `request_stop()`
- Estimation du coût avant lancement (1500 input / 500 output tokens proxy)

### CLI interactive
- Création et reprise de projet
- Configuration complète du provider (type, URL, clé API, modèle)
- Choix du mode Littéral/Hypothèses (question interactive ou flag `--mode`)
- Lancement transcription avec retry sur erreur
- Suivi en direct (barre de progression texte, coûts)
- Translation intégrée (optionnelle) dans le flux
- Export automatique OCR + pivot après transcription
- IO injectable pour les tests

### GUI (PySide6)
- 6 onglets : Transcription, Review, Translation, Export archive, Settings, Log
- Sélecteur de langue permanent en haut de fenêtre
- Changement de langue à chaud (`_rebuild_ui()` sans perte d'état)
- RTL pour l'arabe
- Thème clair/sombre automatique (Fusion + QSS)
- Threading : 5 workers (Transcription, Export, Translation, Batch Translation, Batch Import)
- Projet : estimation du coût avant lancement
- Barre de progression + roue animée + liste des fichiers en cours
- Mode Verbose (logs DEBUG) dans l'onglet Log
- Menu Fichier : Nouveau projet, Ouvrir, Enregistrer, Export projet, Quitter
- Import de lot : dialogue + copie `shutil.copy2` vers `images/<batch_id>/`

### Revue et validation
- Revue par champ avec historique (5 champs : call_number, date, sender, recipient, transcription)
- Boutons Valider/Refuser/Attente (appliquent statut aux 5 champs, mode groupe)
- Mode simple (image + transcription + description + 3 boutons) / Avancé (champs détaillés)
- Liste de pièces : triée pending→validated→rejected, pastille colorée, préfixe « H » pour hypothèses
- Filtre « Hypothétique uniquement » (case à cocher)
- Validation par seuil de confiance + échantillonnage aléatoire
- Correction inline (flag des traductions obsolètes via `is_translation_stale`)
- Aperçu image dans l'onglet Revue (`QPixmap`)

### Traduction
- Provider dédié (même que l'OCR ou séparé Claude/Gemini/OpenAI-compat)
- Prompt éditable avec placeholders (`{entites_a_preserver}`, `{glossaire}`, etc.)
- Glossaire de corpus persistant (`data/glossary.json`)
- Entités nommées validées réinjectées dans le prompt
- Guards : `check_guards()` bloque si non validé, avertit si confiance basse
- Lanceur batch corpus-level (langue cible, périmètre validé/tout, progression)
- Sauvegarde incrémentale (chaque pièce sauvegardée individuellement)
- Prompt masqué par défaut (bouton « Show prompt »)

### Export OCR (core/export.py)
- 4 formats : markdown, plain text, JSON, PDF searchable (reportlab)
- Modes combiné (fichier unique `combined/`) et/ou par fichier (`per_file/`)
- PDF : image native + texte sélectionnable en dessous
- Polices CID pour japonais (`HeiseiKakuGo-W5`) et chinois (`STSong-Light`)
- Mode Hypothèses : bloc préfixé dans md/txt, objet enveloppe dans json, page d'intro dans pdf
- Noms d'affichage (sans préfixe batch_id) dans les exports

### Export pivot (12 formats)
- EAD (XML), Dublin Core (XML), METS/PREMIS, CSV light/full, XLSX, ZIP
- Markdown, Markdown (Obsidian), SQLite, HTML (recherche client-side), Consultation PDF
- Journal de revue (XLSX)
- Filtre « Validated pieces only »
- Sélecteur de langue d'export (inclut traduction si disponible)
- Tous read-only (ne modifient jamais le corpus)

---

## 5. Registre et reprise

### Nom du fichier
`regeste.json` à la racine du dossier projet (ex `source_dir/regeste.json`).

### Schéma complet (v2)

```json
{
  "meta": {
    "schema_version": 2,
    "project_name": "mon_projet",
    "source_dir": "/path/to/project",
    "output_dir": "/path/to/output",
    "provider": {
      "kind": "claude",
      "model": "claude-sonnet-5",
      "base_url": null,
      "api_key": "sk-ant-..."
    },
    "preprocessing": {
      "deskew": false,
      "denoise": false,
      "contrast": false,
      "upscale": false,
      "upscale_quality": false
    },
    "resize": {
      "disabled": false,
      "max_px_override": null,
      "max_bytes_override": null
    },
    "forced_language": null,
    "system_prompt": null,
    "export": {
      "formats": ["md", "json"],
      "single_file": true,
      "per_file": true,
      "transcription_mode": "literal"
    },
    "transcription_mode": "literal",
    "rates": {
      "claude-sonnet-5": { "input_per_million": 3.0, "output_per_million": 15.0 },
      "gemini-2.5-flash": { "input_per_million": 0.30, "output_per_million": 2.50 }
    },
    "spend_ceiling": null,
    "workers": 4,
    "ui_language": null,
    "translation_provider": null,
    "translation_same_as_ocr": true,
    "translation_prompt": null
  },
  "files": {
    "mon_lot_image001.jpg": {
      "source": {
        "batch_id": "mon_lot",
        "physical_path": "/path/to/project/images/mon_lot/mon_lot_image001.jpg",
        "imported": true,
        "key_prefixed": true
      },
      "transcription": {
        "status": "ok",
        "mode": "literal",
        "text": "Cher Monsieur,\nJe vous écris depuis Lyon...",
        "tokens_in": 1520,
        "tokens_out": 480,
        "cost": 0.0032,
        "model": "claude-sonnet-5",
        "date": "2026-07-19T12:00:00+00:00",
        "error_message": null
      },
      "description": "Lettre manuscrite du 3 mars 1917, encre noire sur papier ligné.",
      "language": "fr"
    }
  }
}
```

### Logique de reprise
- **Mode `"new"`** : traite tous les fichiers du registre (`files_to_process("new")` retourne toutes les clés).
- **Mode `"resume"`** : `files_to_process("resume")` retourne les fichiers dont `transcription.status != "ok"` — les `ok` sont sautés, les `error` sont retentés (auto-retry).

### Écriture atomique
`Registry.save()` (`core/registry.py:243`) : création d'un fichier temporaire `.regeste.json.*.tmp` dans le même dossier, `json.dump()`, puis `os.replace()` atomique. Si une exception survient avant `os.replace()`, le temporaire est supprimé (`try/except`). Le fichier source n'est jamais corrompu.

### Migration v1→v2
`Registry.load()` détecte v1 si `meta.schema_version` est absent ou < 2. Appelle `_migrate_v1_entry()` qui regroupe les champs plats (`status`, `text`, `description`, `tokens_in`, etc.) dans la structure namespacée. `batch_id` par défaut = nom du dossier source. `transcription.mode` hérité de `meta.transcription_mode`. Bump à `schema_version=2` et ré-écriture immédiate.

---

## 6. Export — formats supportés

### Export OCR (core/export.py)

| Format | Extension | Combiné | Par fichier | Mode Hypothèses |
|--------|-----------|---------|-------------|-----------------|
| Markdown | .md | ✅ `combined/<projet>.md` | ✅ `per_file/<base>.md` | ✅ bloc préfixé |
| Plain text | .txt | ✅ `combined/<projet>.txt` | ✅ `per_file/<base>.txt` | ✅ bloc préfixé |
| JSON | .json | ✅ `combined/<projet>.json` | ✅ `per_file/<base>.json` | ✅ objet enveloppe |
| PDF searchable | .pdf | ✅ `combined/<projet>.pdf` | ✅ `per_file/<base>.pdf` | ✅ page d'intro |

### Export pivot (export/formats.py)

| Format | Fichier de sortie | Statut |
|--------|-------------------|--------|
| EAD (XML) | `ead.xml` | ✅ |
| Dublin Core (XML) | `dublin_core.xml` | ✅ |
| METS/PREMIS | `mets/` (dossier) | ✅ |
| CSV light | `export_light.csv` | ✅ |
| CSV full | `export_full.csv` | ✅ |
| XLSX | `export.xlsx` | ✅ (avec miniature) |
| ZIP | `archive.zip` | ✅ (avec images) |
| Markdown | `export.md` | ✅ |
| Markdown (Obsidian) | `obsidian/` (dossier) | ✅ |
| SQLite | `export.db` | ✅ |
| HTML | `export.html` | ✅ (recherche client-side) |
| Consultation PDF | `export.pdf` | ✅ |
| Journal de revue | `journal_de_revue.xlsx` | ✅ |

Tous les exporteurs pivot supportent le filtre `validated_only` et le paramètre `target_language` (traduction).

---

## 7. Configuration par projet

Toute la configuration est stockée dans `regeste.json` → champ `meta`, via `ProjectConfig.to_meta()` / `from_meta()` (`core/project.py`).

15 champs persistés :
- `project_name`, `source_dir`, `output_dir` — identification
- `provider` — kind, model, base_url, api_key (en clair, choix explicite)
- `preprocessing` — 5 booléens (deskew, denoise, contrast, upscale, upscale_quality)
- `resize` — disabled, max_px_override, max_bytes_override
- `forced_language` — str ou None
- `system_prompt` — str ou None (None = utiliser le défaut du mode)
- `export` — formats, single_file, per_file, transcription_mode
- `transcription_mode` — "literal"/"hypotheses"
- `rates` — dict modèle → Rate (input_per_million, output_per_million)
- `spend_ceiling` — float ou None
- `workers` — int, défaut 4
- `ui_language` — str ou None (None = auto-détection)
- `translation_provider` — ProviderConfig ou None
- `translation_same_as_ocr` — bool, défaut True
- `translation_prompt` — str ou None (None = défaut)

Absence d'un champ dans `meta` → valeur par défaut dans `ProjectConfig.from_meta()` (rétrocompatibilité).

---

## 8. Internationalisation

### Langues couvertes

9 langues, toutes avec `.po` et `.mo` compilés :

| Code | Langue | msgid | Fuzzy | Non traduites | RTL |
|------|--------|-------|-------|---------------|-----|
| en | English | 247 | 0 | 0 | Non |
| fr | Français | 247 | 0 | 0 | Non |
| de | Deutsch | 247 | 0 | 0 | Non |
| es | Español | 247 | 0 | 0 | Non |
| pt | Português | 247 | 0 | 0 | Non |
| ja | 日本語 | 247 | 0 | 0 | Non |
| zh | 中文 | 247 | 0 | 0 | Non |
| ar | العربية | 247 | 0 | 0 | **Oui** |
| ru | Русский | 247 | 0 | 0 | Non |

### Template .pot
- 247 msgid, créé le 2026-07-19 19:34+0200
- Extraction via Babel (`babel.cfg` + `scripts/extract_translations.py`)

### Mécanisme
- `gettext` standard via `_()` dans tout le code
- `set_language(None)` → auto-détection via `LC_ALL` / `LANG` / `locale.getlocale()`
- `set_language("fr")` → charge le catalogue français
- Changement de langue à chaud : `set_language()` + `_rebuild_ui()` (reconstruit les widgets, les ivars survivent)
- RTL : `is_rtl()` pour l'arabe → `Qt.LayoutDirection.RightToLeft`, appliqué au démarrage et au changement de langue

---

## 9. Points d'entrée

### GUI (par défaut)
```bash
python -m regeste
python -m regeste --lang fr
```

### CLI interactive
```bash
python -m regeste --cli
python -m regeste --cli --lang fr --mode hypotheses
```

### Flags disponibles (`__main__.py`)

| Flag | Type | Défaut | Description |
|------|------|--------|-------------|
| `--cli` | bool | False | Lance la CLI au lieu de la GUI |
| `--lang` | str | None | Langue d'interface (surpasse LANG/LC_ALL) |
| `--mode` | literal/hypotheses | None | Mode transcription : pré-sélection nouveau, override reprise |

### Menu Fichier (GUI)
- Nouveau projet (`Ctrl+N` via raccourci)
- Ouvrir un projet (`Ctrl+O`)
- Enregistrer (`Ctrl+S`)
- Export projet (OCR + formats archivés en un clic)
- Quitter (`Ctrl+Q`)

### Exécutable autonome
`pyinstaller` via `regeste.spec` (paquet `.mo` inclus).

---

## 10. Chantiers en cours

**Aucun chantier en cours identifié.** Le RESTE À FAIRE de `manifest.md` est intégralement barré (items 1 à 14). Tous les items du backlog initial sont implémentés.

### Points non couverts (absents, jamais commencés)
- CI/CD (aucun fichier de CI, pas de déploiement PyPI)
- Format `.rgt` (projet formalisé avec extension propre)
- Documentation README non mise à jour des évolutions récentes (ne mentionne pas menu Fichier, import batch, schéma v2, Export archive)
- Aucune traduction des exports (les catalogues .po couvrent l'interface, pas le contenu des documents exportés)
- Détection automatique des zones `[[...]]` dans le texte (has_uncertain_passages) — non demandé

### Annotations TODO dans le code
- `core/providers/claude.py:29` : factorisation SDK avec `ClaudeTranslationProvider`
- `core/transcriber.py:152` : offload preprocessing vers un `ThreadPoolExecutor` CPU dédié (race-prone en tests)

---

## 11. Terminologie

| Terme métier | Désignation dans le code | Module | Classe/Type |
|-------------|--------------------------|--------|-------------|
| **Regeste** | Nom de l'application | `regeste/` | — |
| **Registre** | `Registry` | `core/registry.py` | `Registry`, `FileEntry`, `SourceInfo`, `TranscriptionInfo` |
| **Fichier** | `file_name` (clé), `name` | `core/registry.py` | Clé du dict `files` — préfixée `<batch_id>_<nom>` |
| **Lot** | `batch_id` | `core/registry.py` → `SourceInfo.batch_id` | `str` |
| **Pièce** | `Piece` | `pivot/models.py` | `Piece` (dataclass) |
| **Pivot** | Sous-système pivot/ | `pivot/`, `review/`, `translation/`, `export/` | — |
| **Provider** | `Provider` | `core/providers/base.py` | `Provider` (ABC) |
| **Claude** | `ClaudeProvider` | `core/providers/claude.py` | `ClaudeProvider(Provider)` |
| **Gemini** | `GeminiProvider` | `core/providers/gemini.py` | `GeminiProvider(Provider)` |
| **OpenAI-compat** | `OpenAICompatProvider` | `core/providers/openai_compat.py` | `OpenAICompatProvider(Provider)` |
| **Mode transcription** | `TranscriptionMode` | `core/transcription_mode.py` | `TranscriptionMode` (enum) |
| **Mode Littéral** | `TranscriptionMode.LITERAL` | `core/transcription_mode.py` | `"literal"` |
| **Mode Hypothèses** | `TranscriptionMode.HYPOTHESES` | `core/transcription_mode.py` | `"hypotheses"` |
| **Type de sortie** | Contrôles dans l'onglet Transcription | `gui/main_window.py:_build_transcription_tab()` | Radios + checkboxes |
| **Export archive** | Onglet (anciennement « Archival formats ») | `gui/main_window.py` | `ExportPanel` (12 formats pivot) |
| **Revue** | Sous-système review/ | `review/` | `ReviewPanel`, `validation.py`, `queue.py` |
| **Glossaire** | Glossaire de corpus | `translation/glossary.py` | Fichier `data/glossary.json` |
| **Traduction** | Sous-système translation/ | `translation/` | `TranslationProvider` (ABC) |
| **Coûts** | Sous-onglet Settings > Costs | `gui/panels/settings_panel.py` | `CostsChartWidget` (QPainter) |
| **Cote** | `Piece.call_number` | `pivot/models.py` | Champ str |
| **Fonds/Série** | `Piece.fonds`, `Piece.series` | `pivot/models.py` | Champs str |
| **Sauvegarde atomique** | `Registry.save()` | `core/registry.py:243` | `tempfile.mkstemp` + `os.replace` |
| **Fenêtre glissante** | `Transcriber.run()` | `core/transcriber.py:205` | `ThreadPoolExecutor` + `_submit_next()` |
