from __future__ import annotations
import pathlib, subprocess, tempfile
from dataclasses import dataclass
from typing import List
from rich.console import Console

console = Console()

@dataclass
class Edit:
    file: pathlib.Path
    start: int
    end: int
    replacement: str
    note: str = ""

def apply_edit(text: str, edit: Edit) -> str:
    return text[:edit.start] + edit.replacement + text[edit.end:]

def apply_unified_diff(patch_text: str, cwd: pathlib.Path) -> bool:
    """Apply a unified diff using `patch`. Returns True on success."""
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(patch_text)
        name = tf.name
    try:
        proc = subprocess.run(["patch","-p0","-i",name], cwd=str(cwd),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ok = proc.returncode == 0
        if not ok:
            console.log("[red]patch failed[/]")
            console.print(proc.stdout)
            console.print(proc.stderr)
        return ok
    finally:
        try: pathlib.Path(name).unlink(missing_ok=True)
        except Exception: pass

def propose_deterministic_fixes(file: pathlib.Path, source: str, errors: List[str]) -> List[Edit]:
    edits: List[Edit] = []
    err_blob = " ".join(errors)
    if "unknown identifier 'Real.log'" in err_blob and "Mathlib.Analysis.SpecialFunctions.Log.Basic" not in source:
        ins = "import Mathlib.Analysis.SpecialFunctions.Log.Basic\n"
        edits.append(Edit(file, 0, 0, ins + source, note="import log"))
    if "unknown identifier 'Classical'" in err_blob and "open Classical" not in source:
        edits.append(Edit(file, 0, 0, "open Classical\n" + source, note="open Classical"))
    return edits
