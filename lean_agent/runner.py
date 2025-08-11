"""
runner.py — Core agent loop that repairs, innovates, and documents Lean files.

Overview
--------
This module implements the main control loop of the agent:
1) Build the project containing a target Lean file.
2) If the file does not compile, try deterministic fixes; if none help, call GPT-5 to propose a
   full corrected file and retry until it compiles or we run out of iterations.
3) If the file compiles and `updates > 0`, perform *innovation* steps:
   - Ask GPT-5 to extend the file with new, thematically consistent results (lemmas/defs/theorems).
   - Then repair again until the extended file compiles.
4) When `updates == 0` and the file compiles, call GPT-5 once to add documentation:
   - A module docstring summarizing the contents,
   - `--` comments before each `def`/`lemma`/`theorem`,
   - proof-step comments inside `by` blocks when helpful,
   - sanity-check by rebuilding; revert if comments break the build.

Environment & API keys
----------------------
- The OpenAI key is read from `OPENAI_API_KEY` (preferred) or from `openai_key.txt` at repo root
  (useful for local development). The `.env` in the parent directory is also loaded if present.

Notes
-----
- The loop writes snapshots under `<project>/.agent_runs/<timestamp>/snapshots` and also updates
  the working file in-place so that `lake build` sees changes.
- All prompts request full Lean code only (no prose) to keep the loop deterministic.

See also
--------
- lean_server.py — tiny wrapper around `lake`/`lean` to build and get diagnostics.
- editing.py — helper functions that compute deterministic edits (heuristics).
"""
from __future__ import annotations

import pathlib
from typing import List, Optional
import os
from datetime import datetime
from rich.console import Console
from openai import OpenAI

from .lean_server import LeanProject
from .editing import Edit, apply_edit, propose_deterministic_fixes

from dotenv import load_dotenv
from pathlib import Path

# Load `.env` located in the parent folder of this module (if present).
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

console = Console()

# Load API key (env or text file fallback).
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    key_file = Path(__file__).parent.parent / "openai_key.txt"
    if key_file.exists():
        api_key = key_file.read_text().strip()

# Initialize the OpenAI client if we have a key; otherwise the LLM calls are skipped gracefully.
client = OpenAI(api_key=api_key) if api_key else None


def _strip_fences(s: str) -> str:
    """Remove Markdown code fences from an LLM response.

    The agent prompts require *Lean code only*, but some models still return code fenced inside
    triple backticks. This helper extracts the inner code safely.

    Parameters
    ----------
    s : str
        Raw model output.

    Returns
    -------
    str
        Code content without surrounding code fences.
    """
    t = s.strip()
    if t.startswith("```"):
        # remove leading ```lang line if present
        parts = t.split("\n", 1)
        t = parts[1] if len(parts) == 2 else ""
        if t.rstrip().endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


class AgentRunner:
    """Main agent that repairs, innovates, and documents a Lean file.

    Parameters
    ----------
    project : LeanProject
        The Lean project containing the target file; also used to run builds/diagnostics.
    target_file : str
        Path to the Lean file to improve.
    max_iters : int, default=20
        Maximum number of loop iterations (repair/innovate/document cycles).
    beam : int, default=3
        Number of deterministic candidate edits to try at each iteration.
    updates : int, default=0
        Number of *innovation* cycles to perform once the file compiles.
    theme : str, default=""
        High-level theme that guides innovation (e.g., "complex analysis").

    Behavior
    --------
    - Writes snapshots in `.agent_runs/<timestamp>/snapshots` for reproducibility.
    - Updates the working file in-place so that `lake build` compiles the latest version.
    - Calls GPT-5 only if deterministic fixes fail or for innovation/documentation steps.
    """
    def __init__(self, project: LeanProject, target_file: str,
                 max_iters: int = 20, beam: int = 3,
                 updates: int = 0, theme: str = ""):
        self.project = project
        self.target = pathlib.Path(target_file).resolve()
        self.max_iters = max(1, max_iters)
        self.beam = max(1, beam)
        self.updates = max(0, updates)
        self.theme = theme
        self.did_doc = False  # add documentation at most once

        # Create run directory under the project so Lake sees same module path.
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = self.project.root / ".agent_runs" / ts
        self.snapshots_dir = self.run_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        console.log(f"[debug] Project root: {self.project.root}")
        console.log(f"[debug] Working target file: {self.target} (exists={self.target.exists()})")
        if not self.target.exists():
            raise FileNotFoundError(f"Target file does not exist: {self.target}")

    def _save_snapshot(self, tag: str, content: str):
        """Save a snapshot of the full file content with a descriptive tag.

        Snapshots are stored as `<stem>.<tag>.lean` under the run's `snapshots` directory.
        """
        snap_file = self.snapshots_dir / f"{self.target.stem}.{tag}{self.target.suffix}"
        snap_file.write_text(content, encoding="utf-8")
        return snap_file

    def _write_target(self, content: str, tag: str):
        """Write new content to the working file, bump mtime, and snapshot.

        We bump the file's modification time so that Lake does not skip the build due to unchanged
        timestamps.
        """
        before = self.target.read_text(encoding="utf-8")
        if before == content:
            console.log(f"[debug] No change to {self.target.name} (content identical).")
        self.target.write_text(content, encoding="utf-8")
        os.utime(self.target, None)  # ensure mtime bump
        after = self.target.read_text(encoding="utf-8")
        console.log(f"[debug] WROTE {len(after)} bytes to {self.target} (snapshot tag={tag})")
        self._save_snapshot(tag, after)

    def _call_llm_repair(self, file_text: str, errs: List[str]) -> Optional[str]:
        """Use GPT-5 to propose a full corrected file when the build fails and heuristics don't help.

        Returns the new file content or `None` if no code was produced.
        """
        if client is None:
            console.log("[dim]No OPENAI_API_KEY; skipping LLM step.[/]")
            return None
        err_blob = "\n\n".join(errs[:20]).strip()
        prompt = f"""
You are a Lean 4 coding assistant. The file fails to compile with the following errors:

{err_blob or "(no diagnostics available)"}

Return a complete corrected version of the file that compiles with `lake build`.
Respond with LEAN CODE ONLY (no explanations). If imports are needed, add them.

```lean
{file_text}
```
""".strip()
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": "You are a precise Lean 4 refactoring and repair agent."},
                {"role": "user", "content": prompt},
            ],
        )
        code = _strip_fences((resp.choices[0].message.content or "").strip())
        return code if code else None

    def _call_llm_innovate(self, file_text: str) -> Optional[str]:
        """Ask GPT-5 to extend the file with new results consistent with the given theme.

        Returns the extended file content or `None` if no code was produced.
        """
        if client is None:
            console.log("[dim]No OPENAI_API_KEY; skipping LLM step.[/]")
            return None
        prompt = f"""
You are a Lean 4 coding assistant. The current file compiles and comprises results in the following theme: "{self.theme}".
Add a main new result or definition that is not currently in the file. You can add any number of lemmas or definitions,
as long as they are not already in the file and are necessary to complete the proof of the new result. Before you do, take into consideration the comments in the file, and choose the new result in line with these comments,
in order to continue the logical order it follows. Ensure the result still compiles. Return LEAN CODE ONLY.

```lean
{file_text}
```
""".strip()
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": "You extend Lean 4 files with thematically consistent new results."},
                {"role": "user", "content": prompt},
            ],
        )
        code = _strip_fences((resp.choices[0].message.content or "").strip())
        return code if code else None

    def _call_llm_document(self, file_text: str) -> Optional[str]:
        """Ask GPT-5 to add a module docstring and explanatory comments, preserving semantics.

        The documentation step is only triggered once at the end (after updates are exhausted and
        the file compiles). If comments break the build, we revert to the compiled version.
        """
        if client is None:
            console.log("[dim]No OPENAI_API_KEY; skipping documentation step.[/]")
            return None
        prompt = f"""
You are a Lean 4 documentation assistant.
Enrich the following Lean file by adding documentation and comments WITHOUT changing its behavior.
Requirements:
- Add a top-level module docstring using `/-! ... -/` summarizing the theme and listing main definitions/lemmas/theorems.
- Immediately before each `def`, `lemma`, or `theorem`, add a brief `--` comment describing what it states and its role.
- For nontrivial proofs, add a few inline `--` comments inside `by` blocks explaining key steps.
- Do NOT rename identifiers. Do NOT reorder imports unless necessary. Do NOT introduce non-compiling code.
- Return LEAN CODE ONLY. No explanations outside comments.

Here is the file:

```lean
{file_text}
```
""".strip()
        resp = client.chat.completions.create(
            model="gpt-5",
            messages=[
                {"role": "system", "content": "You add documentation and comments to Lean 4 files without changing their semantics."},
                {"role": "user", "content": prompt},
            ],
        )
        code = _strip_fences((resp.choices[0].message.content or "").strip())
        return code if code else None

    def loop(self) -> bool:
        """Run the agent loop.

        Returns
        -------
        bool
            True iff the project builds successfully at exit (possibly with innovation/docs).
        """
        # Initial state: snapshot the starting file.
        src = self.target.read_text(encoding="utf-8")
        self._save_snapshot("iter000", src)

        for it in range(1, self.max_iters + 1):
            console.rule(f"[cyan]Iteration {it}")
            console.log(f"[debug] Building project at root {self.project.root}")
            rc, out, err = self.project.run_lake_build()

            # Case A: Clean build (no 'sorry') → either innovate or document.
            if rc == 0 and "sorry" not in src:
                console.print("[bold green]Build OK.[/]")
                if self.updates > 0:
                    console.print(f"[green]Innovation step: remaining updates = {self.updates} (theme '{self.theme}').[/green]")
                    self.updates -= 1
                    new_code = self._call_llm_innovate(self.target.read_text(encoding='utf-8'))
                    if not new_code:
                        console.print("[red]LLM returned no code during innovation. Stopping.[/]")
                        return True  # keep last compiled version
                    self._write_target(new_code, tag=f"iter{it:03d}_innov")
                    src = new_code
                    # Continue to next loop to compile the innovation.
                    continue
                else:
                    # Final documentation enrichment (only once).
                    if not self.did_doc:
                        console.print("[blue]Adding documentation/comments to the compiled file…[/]")
                        pre = self.target.read_text(encoding="utf-8")
                        doc_code = self._call_llm_document(pre)
                        if doc_code:
                            self._write_target(doc_code, tag=f"iter{it:03d}_docs")
                            # Sanity-check: rebuild after adding comments/docstrings.
                            rc2, _, _ = self.project.run_lake_build()
                            if rc2 != 0:
                                console.print("[yellow]Documentation broke the build; reverting to compiled version.[/]")
                                self._write_target(pre, tag=f"iter{it:03d}_docs_revert")
                            else:
                                src = doc_code
                                self.did_doc = True
                        else:
                            console.print("[dim]No documentation was produced; keeping compiled file.[/]")
                    console.print("[bold green]No more updates required. Stopping.[/]")
                    return True

            # Case B: Build failed → gather diagnostics and try deterministic fixes.
            diags = self.project.diagnostics(str(self.target))
            errs = [m.text for m in diags if m.severity == "error"]
            console.log(f"[debug] Errors: {len(errs)}")

            edits = propose_deterministic_fixes(self.target, src, errs)
            if edits:
                best_text: Optional[str] = None
                best_errs = 10**9

                for ed in edits[:self.beam]:
                    cand = apply_edit(src, ed)
                    # Try candidate in place on the same file path so lake sees it.
                    self.target.write_text(cand, encoding="utf-8")
                    diags2 = self.project.diagnostics(str(self.target))
                    err2 = sum(1 for m in diags2 if m.severity == "error")
                    console.log(f"Trying deterministic edit {ed.note!r} → errors: {err2}")
                    if err2 < best_errs:
                        best_errs = err2
                        best_text = cand

                if best_text is None:
                    console.print("[red]No viable deterministic candidate.[/]")
                    return False

                self._write_target(best_text, tag=f"iter{it:03d}_det")
                src = best_text
                continue

            # Case C: No deterministic fixes → ask the LLM to repair the file.
            console.print("[yellow]No deterministic fixes. Using GPT-5 for repair…[/]")
            new_code = self._call_llm_repair(self.target.read_text(encoding='utf-8'), errs)
            if not new_code:
                console.print("[red]LLM returned no code during repair. Stopping.[/]")
                return False
            self._write_target(new_code, tag=f"iter{it:03d}_llmrepair")
            src = new_code

        return False

