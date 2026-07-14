"""Self-contained static HTML export with vanilla-JS full-text search — no external assets."""

from __future__ import annotations

import json
from pathlib import Path

from regeste.pivot import Piece, global_status

from .common import filter_pieces

_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Export Regeste</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
.piece {{ border-bottom: 1px solid #ccc; padding: 1rem 0; }}
input {{ width: 100%; padding: 0.5rem; margin-bottom: 1rem; box-sizing: border-box; }}
</style>
</head>
<body>
<input id="search" placeholder="Rechercher...">
<div id="results"></div>
<script>
const pieces = {data};
const results = document.getElementById("results");
function render(list) {{
  results.innerHTML = list.map(p => `
    <div class="piece">
      <strong>${{p.call_number || p.id}}</strong> — ${{p.date}}<br>
      <em>${{p.sender}} → ${{p.recipient}}</em>
      <p>${{p.summary}}</p>
      <details><summary>Transcription</summary><pre>${{p.transcription}}</pre></details>
    </div>
  `).join("");
}}
document.getElementById("search").addEventListener("input", (e) => {{
  const q = e.target.value.toLowerCase();
  render(pieces.filter(p => JSON.stringify(p).toLowerCase().includes(q)));
}});
render(pieces);
</script>
</body>
</html>
"""


def export_html(pieces: list[Piece], output_path: Path, *, validated_only: bool = False) -> Path:
    pieces = filter_pieces(pieces, validated_only=validated_only)
    data = [
        {
            "id": p.id,
            "call_number": p.call_number,
            "date": p.date,
            "sender": p.sender,
            "recipient": p.recipient,
            "summary": p.summary,
            "transcription": p.transcription,
            "status": global_status(p),
        }
        for p in pieces
    ]
    output_path.write_text(
        _TEMPLATE.format(data=json.dumps(data, ensure_ascii=False)), encoding="utf-8"
    )
    return output_path
