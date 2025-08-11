"""
lean_server.py — Thin wrapper around `lake`/`lean` for building Lean projects and collecting diagnostics.

This module provides:
- A `LeanProject` dataclass that discovers the project root (by walking up to find `lakefile.lean`),
  and offers convenience methods to build (`lake build`) and to compile a single file (`lean --make`
  via `lake env`), returning return codes and stdout/stderr blobs.
- A minimal `diagnostics` method that maps the subprocess result to a list of `Message` objects.
  It intentionally collapses errors into a single synthetic message containing the stderr text,
  which is sufficient for an iterative repair loop.
- A tiny `lint_file` helper to flag obvious anti-patterns (`sorry`, `admit`).

Design goals
------------
Keep the interface *very small* and robust for scripting:
- No LSP required.
- No parsing of Lean's JSON output (we only need pass/fail and the error text).
- Compatible with Lake-managed projects: we always prefer `lake env` so dependencies are visible.

Example
-------
>>> proj = LeanProject.from_file("sample_project/Play.lean")
>>> rc, out, err = proj.run_lake_build()
>>> diags = proj.diagnostics("sample_project/Play.lean")
"""
from __future__ import annotations

import subprocess
import json  # kept for future extension (e.g., JSON diagnostics), unused for now
import pathlib
import time  # reserved for potential future timing/logging
from dataclasses import dataclass
from typing import List, Optional, Dict
from rich.console import Console

console = Console()


@dataclass
class Message:
    """A minimal diagnostic record used by the agent loop.

    Attributes
    ----------
    file_name : str
        Path to the file for which this message was produced.
    pos : dict | None
        Start position (line/column) if available. We leave it as `None` here.
    end_pos : dict | None
        End position (line/column) if available. We leave it as `None` here.
    severity : str
        One of {"error", "warning"} (minimal set for our loop).
    text : str
        The raw diagnostic text (usually stderr from Lean).
    """
    file_name: str
    pos: Dict[str, int] | None
    end_pos: Dict[str, int] | None
    severity: str
    text: str


@dataclass
class LeanProject:
    """Represents a Lean project rooted at (or above) a given file.

    The root is discovered by walking up from the given file until a `lakefile.lean` is found.
    If none is found, we treat the file's parent directory as the root and set `lakefile=None`.

    Attributes
    ----------
    root : pathlib.Path
        Project root directory (directory containing `lakefile.lean`, or the file's parent).
    lakefile : pathlib.Path | None
        Path to `lakefile.lean` if it was found; otherwise None.
    """
    root: pathlib.Path
    lakefile: pathlib.Path | None

    @staticmethod
    def from_file(file: str) -> "LeanProject":
        """Discover the project root from a file path.

        Parameters
        ----------
        file : str
            Path to a Lean source file.

        Returns
        -------
        LeanProject
            An instance with resolved `root` and optional `lakefile`.
        """
        p = pathlib.Path(file).resolve()
        cur = p.parent
        lakefile = None
        # Walk upward until we find a `lakefile.lean`, or hit filesystem root.
        while True:
            lf = cur / "lakefile.lean"
            if lf.exists():
                lakefile = lf
                break
            if cur.parent == cur:
                break
            cur = cur.parent
        return LeanProject(root=cur if lakefile else p.parent, lakefile=lakefile)

    def run_lake_build(self, target: Optional[str] = None, timeout: int = 1200) -> tuple[int, str, str]:
        """Run `lake build` in the project root.

        Parameters
        ----------
        target : Optional[str]
            Optional specific target to build (defaults to the whole project).
        timeout : int
            Timeout in seconds (defaults to 20 minutes).

        Returns
        -------
        (returncode, stdout, stderr) : tuple[int, str, str]
        """
        cmd = ["lake", "build"]
        if target:
            cmd.append(target)
        console.log(f"[dim]Running: {' '.join(cmd)} in {self.root}[/]")
        proc = subprocess.run(
            cmd,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def run_lean_make(self, file: str, timeout: float = 60.0) -> tuple[int, str, str]:
        """Compile a single Lean file via `lean --make`, preferring `lake env` for environment consistency.

        Parameters
        ----------
        file : str
            Path to a Lean source file (absolute or relative to `self.root`).
        timeout : float
            Timeout in seconds (default: 60).

        Returns
        -------
        (returncode, stdout, stderr) : tuple[int, str, str]

        Notes
        -----
        - We try `lake env lean --make …` first so dependencies and toolchain match the Lake project.
        - If `lake` is not available (FileNotFoundError), we fall back to invoking `lean` directly.
          This may fail if the environment isn’t configured, but it’s a useful fallback in CI/manual runs.
        """
        try:
            proc = subprocess.run(
                ["lake", "env", "lean", "--make", str(file)],
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            # Fallback: raw `lean --make`, which might miss Lake-declared deps
            proc = subprocess.run(
                ["lean", "--make", str(file)],
                cwd=str(self.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            return proc.returncode, proc.stdout, proc.stderr

    def diagnostics(self, file: str) -> List[Message]:
        """Return minimal diagnostics for a file by invoking `run_lean_make`.

        This condenses the result into either an empty list (success) or a singleton
        error/warning `Message` with the stderr (or stdout) text. This is intentionally
        simple for use in automated repair loops.

        Parameters
        ----------
        file : str
            Path to the Lean source file to check.

        Returns
        -------
        List[Message]
            Empty list if the file compiles cleanly, otherwise a list containing one `Message`.
        """
        rc, out, err = self.run_lean_make(file)
        # If successful and no stderr content, there are no diagnostics to report.
        if rc == 0 and not err.strip():
            return []
        # Collapse all errors into a single synthetic message.
        return [
            Message(
                file_name=str(file),
                pos=None,
                end_pos=None,
                severity="error" if rc != 0 else "warning",
                text=err.strip() or out.strip(),
            )
        ]

    def lint_file(self, file: str) -> List[str]:
        """Very lightweight linter that flags obvious placeholders.

        Currently checks for:
        - occurrences of `sorry`
        - occurrences of `admit`

        Parameters
        ----------
        file : str
            Path to a Lean source file.

        Returns
        -------
        List[str]
            A list of issue descriptions (empty if none found).
        """
        issues: List[str] = []
        txt = pathlib.Path(file).read_text()
        if "sorry" in txt:
            issues.append("contains `sorry`")
        if "admit" in txt:
            issues.append("contains `admit`")
        return issues

