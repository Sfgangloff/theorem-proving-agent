from __future__ import annotations
import subprocess, pathlib, datetime
from rich.console import Console

console = Console()

def ensure_git_branch(root: pathlib.Path, prefix: str):
    try:
        subprocess.run(["git","rev-parse","--is-inside-work-tree"], cwd=str(root), check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError:
        console.log("[dim]Not a git repo; skipping branch creation.[/]")
        return
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{prefix}-{ts}"
    subprocess.run(["git","checkout","-b", name], cwd=str(root))
    console.log(f"[dim]Created branch {name}[/]")
