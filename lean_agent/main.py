import typer
from rich.console import Console
from .runner import AgentRunner
from .lean_server import LeanProject
from .git_utils import ensure_git_branch

app = typer.Typer(add_completion=False)
console = Console()

@app.command()
def run(
    file: str = typer.Option(..., "--file", "-f", help="Path to Lean file"),
    max_iters: int = typer.Option(20, "--max-iters"),
    beam: int = typer.Option(3, "--beam"),
    scratch_branch: bool = typer.Option(True, "--scratch-branch/--no-scratch-branch"),
):
    proj = LeanProject.from_file(file)
    if scratch_branch:
        ensure_git_branch(proj.root, "agent/run")
    runner = AgentRunner(project=proj, target_file=file, max_iters=max_iters, beam=beam)
    ok = runner.loop()
    if ok: console.print("[bold green]Build OK[/]")
    else: console.print("[bold yellow]Stopped without full success[/]")
