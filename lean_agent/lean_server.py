from __future__ import annotations
import subprocess, json, pathlib, time
from dataclasses import dataclass
from typing import List, Optional, Dict
from rich.console import Console

console = Console()

@dataclass
class Message:
    file_name: str
    pos: Dict[str,int] | None
    end_pos: Dict[str,int] | None
    severity: str
    text: str

@dataclass
class LeanProject:
    root: pathlib.Path
    lakefile: pathlib.Path | None

    @staticmethod
    def from_file(file: str) -> "LeanProject":
        p = pathlib.Path(file).resolve()
        cur = p.parent
        lakefile = None
        while True:
            lf = cur / "lakefile.lean"
            if lf.exists():
                lakefile = lf
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        return LeanProject(root=cur if lakefile else p.parent, lakefile=lakefile)

    def run_lake_build(self, target: Optional[str] = None, timeout: int = 1200) -> tuple[int,str,str]:
        cmd = ["lake", "build"]
        if target: cmd.append(target)
        console.log(f"[dim]Running: {' '.join(cmd)} in {self.root}[/]")
        proc = subprocess.run(cmd, cwd=str(self.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr

    def run_lean_make(self, file: str, timeout: float = 60.0) -> tuple[int,str,str]:
        # Prefer 'lake env' so Lean sees the same env as lake
        try:
            proc = subprocess.run(
                ["lake", "env", "lean", "--make", str(file)],
                cwd=str(self.root),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            # Fallback if 'lake' not available: raw lean (may miss deps)
            proc = subprocess.run(
                ["lean", "--make", str(file)],
                cwd=str(self.root),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout
            )
            return proc.returncode, proc.stdout, proc.stderr

    def diagnostics(self, file: str) -> List[Message]:
        rc, out, err = self.run_lean_make(file)
        # We return one synthetic message with the stderr blob; enough for the loop
        if rc == 0 and not err.strip():
            return []
        return [Message(
            file_name=str(file),
            pos=None, end_pos=None,
            severity="error" if rc != 0 else "warning",
            text=err.strip() or out.strip()
        )]

    def lint_file(self, file: str):
        issues = []
        txt = pathlib.Path(file).read_text()
        if "sorry" in txt: issues.append("contains `sorry`")
        if "admit" in txt: issues.append("contains `admit`")
        return issues
