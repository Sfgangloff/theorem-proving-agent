from __future__ import annotations
import pathlib
from typing import List,Optional
import os
from datetime import datetime
from rich.console import Console
from openai import OpenAI
from .lean_server import LeanProject
from .editing import Edit, apply_edit, propose_deterministic_fixes

from dotenv import load_dotenv
from pathlib import Path

# Point to parent folder .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

console = Console()

# Load API key (env or text file fallback)
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    key_file = Path(__file__).parent.parent / "openai_key.txt"
    if key_file.exists():
        api_key = key_file.read_text().strip()

client = OpenAI(api_key=api_key)

def _strip_fences(s: str) -> str:
    t = s.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()

class AgentRunner:
    def __init__(self, project: LeanProject, target_file: str,
                 max_iters: int = 20, beam: int = 3,
                 updates: int = 0, theme: str = ""):
        self.project = project
        self.target = pathlib.Path(target_file).resolve()
        self.max_iters = max(1, max_iters)
        self.beam = max(1, beam)
        self.updates = max(0, updates)
        self.theme = theme

        # Create run directory under the project so Lake sees same module path
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = self.project.root / ".agent_runs" / ts
        self.snapshots_dir = self.run_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        console.log(f"[debug] Project root: {self.project.root}")
        console.log(f"[debug] Working target file: {self.target} (exists={self.target.exists()})")
        if not self.target.exists():
            raise FileNotFoundError(f"Target file does not exist: {self.target}")

    def _save_snapshot(self, tag: str, content: str):
        snap_file = self.snapshots_dir / f"{self.target.stem}.{tag}{self.target.suffix}"
        snap_file.write_text(content, encoding="utf-8")
        return snap_file

    def _write_target(self, content: str, tag: str):
        """Write to the actual working file path, log bytes, and update mtime so Lake rebuilds."""
        before = self.target.read_text(encoding="utf-8")
        if before == content:
            console.log(f"[debug] No change to {self.target.name} (content identical).")
        self.target.write_text(content, encoding="utf-8")
        # Touch to make sure mtime changes
        os.utime(self.target, None)
        after = self.target.read_text(encoding="utf-8")
        console.log(f"[debug] WROTE {len(after)} bytes to {self.target} (snapshot tag={tag})")
        # Save snapshot too
        self._save_snapshot(tag, after)

    def _call_llm_repair(self, file_text: str, errs: List[str]) -> Optional[str]:
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
        if client is None:
            console.log("[dim]No OPENAI_API_KEY; skipping LLM step.[/]")
            return None
        prompt = f"""
You are a Lean 4 coding assistant. The current file compiles and belongs to the theme "{self.theme}".
Add a few *new* results that are not currently in the file, pushing the topic a bit further while
remaining in the same theme. Ensure the result still compiles. Return LEAN CODE ONLY.

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

    def loop(self) -> bool:
        # initial state
        src = self.target.read_text(encoding="utf-8")
        self._save_snapshot("iter000", src)

        for it in range(1, self.max_iters + 1):
            console.rule(f"[cyan]Iteration {it}")
            console.log(f"[debug] Building project at root {self.project.root}")
            rc, out, err = self.project.run_lake_build()

            if rc == 0 and "sorry" not in src:
                console.print("[bold green]Build OK.[/]")
                if self.updates > 0:
                    console.print(f"[green]Innovation step: remaining updates = {self.updates} (theme '{self.theme}').[/green]")
                    self.updates -= 1
                    new_code = self._call_llm_innovate(self.target.read_text(encoding='utf-8'))
                    if not new_code:
                        console.print("[red]LLM returned no code during innovation. Stopping.[/]")
                        return True  # stop but keep last compiled version
                    self._write_target(new_code, tag=f"iter{it:03d}_innov")
                    src = new_code
                    # continue to next loop to compile the innovation
                    continue
                else:
                    console.print("[bold green]No more updates requested. Stopping.[/]")
                    return True

            # Build failed → diagnostics
            diags = self.project.diagnostics(str(self.target))
            errs = [m.text for m in diags if m.severity == "error"]
            console.log(f"[debug] Errors: {len(errs)}")

            # Deterministic fixes
            edits = propose_deterministic_fixes(self.target, src, errs)
            if edits:
                best_text: Optional[str] = None
                best_errs = 10**9

                for ed in edits[:self.beam]:
                    cand = apply_edit(src, ed)
                    # try candidate in place
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

            # No deterministic fixes → LLM repair
            console.print("[yellow]No deterministic fixes. Using GPT-5 for repair…[/yellow]")
            new_code = self._call_llm_repair(self.target.read_text(encoding='utf-8'), errs)
            if not new_code:
                console.print("[red]LLM returned no code during repair. Stopping.[/]")
                return False
            self._write_target(new_code, tag=f"iter{it:03d}_llmrepair")
            src = new_code

        return False
