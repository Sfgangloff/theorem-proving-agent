"""
Command-line interface for the Lean repair/innovation agent.

This CLI exposes a single `run` command that:
1) Builds the Lean project containing the target file.
2) If the build fails, applies deterministic repairs and, if needed, GPT-5 repair.
3) If the build succeeds and `--updates > 0`, performs *innovation* cycles:
   - Asks GPT-5 to extend the file with new, thematically consistent results.
   - Repairs again until the extended file compiles.
4) When `--updates` is exhausted and the file compiles, adds documentation:
   - Inserts a module docstring summarizing contents.
   - Adds comments before `def`/`lemma`/`theorem` and key proof steps.
   - Rebuilds; if comments break the build, reverts to the last compiled version.

Typical usage
-------------
- Repair only (no innovation/documentation):
    lean-agent --file sample_project/Play.lean

- Repair + 3 innovation cycles within a specific theme:
    lean-agent --file sample_project/Play.lean --updates 3 --theme "complex analysis"

- Enable temporary branch creation (disabled by default):
    lean-agent --file sample_project/Play.lean --scratch-branch

Environment
-----------
- OPENAI_API_KEY is read from environment or from an optional `openai_key.txt`
  at the repository root (handled by the runner).
"""
import typer
from rich.console import Console

from .runner import AgentRunner
from .lean_server import LeanProject
from .git_utils import ensure_git_branch

app = typer.Typer(add_completion=False)
console = Console()

@app.command(help="Repair/extend a Lean file using deterministic fixes and GPT-5, then optionally document it.")
def run(
    file: str = typer.Option(..., "--file", "-f", help="Path to the Lean file to improve."),
    max_iters: int = typer.Option(20, "--max-iters", help="Maximum iterations across all steps."),
    beam: int = typer.Option(3, "--beam", help="How many deterministic candidates to try per iteration."),
    updates: int = typer.Option(0, "--updates", help="Number of innovation cycles after a clean build."),
    theme: str = typer.Option("", "--theme", help="Guides innovation, e.g., 'complex analysis'."),
    scratch_branch: bool = typer.Option(False, "--scratch-branch/--no-scratch-branch",
                                        help="Create a temporary git branch (disabled by default)."),
):
    """
    Build/repair the given Lean file, optionally perform several 'innovation' cycles in a theme,
    and finally enrich the file with documentation and comments.
    """
    proj = LeanProject.from_file(file)

    # Optional: create a temporary git branch so edits are isolated
    if scratch_branch:
        ensure_git_branch(proj.root, "agent/run")

    runner = AgentRunner(
        project=proj,
        target_file=file,
        max_iters=max_iters,
        beam=beam,
        updates=updates,
        theme=theme,
    )
    ok = runner.loop()
    console.print("[bold green]Build OK[/]" if ok else "[bold yellow]Stopped without full success[/]")


if __name__ == "__main__":
    app()
