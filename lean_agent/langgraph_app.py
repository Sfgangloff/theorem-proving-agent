"""
langgraph_app.py — LangGraph-based orchestration for the Lean agent.

Overview
--------
This module provides a compact state machine (via `langgraph`) that coordinates an
autonomous Lean 4 repair loop using five nodes:

1) **diagnose**      — Collect diagnostics for the target file (quick check).
2) **deterministic** — Try deterministic fixes (heuristics) and keep improvements.
3) **propose**       — Ask an LLM for a *unified diff* patch if deterministic fixes aren't enough.
4) **apply**         — Apply the unified diff (patch) to the workspace.
5) **build**         — Run `lake build` to confirm the project compiles.

The graph cycles until either the build succeeds or the `max_iters` budget is exhausted.

Design goals
------------
- Minimal, transparent control flow suitable for demos and experiments.
- LLM usage is optional: without an API key, the `propose` node is skipped (no patch).
- Unified diffs are used to contain changes to a single file and minimize collateral edits.

CLI
---
A thin `cli()` wrapper assembles and runs the state machine:
    python -m lean_agent.langgraph_app --file sample_project/Play.lean

Environment
-----------
- OPENAI_API_KEY is read from environment or from `openai_key.txt` as a fallback.
"""
from __future__ import annotations

import os
import pathlib
from typing import TypedDict, List, Literal, Optional

from rich.console import Console
from dotenv import load_dotenv

# Load simple environment for local runs. If there is an `openai_key.txt`, use it.
load_dotenv()
if "OPENAI_API_KEY" not in os.environ and pathlib.Path("openai_key.txt").exists():
    os.environ["OPENAI_API_KEY"] = pathlib.Path("openai_key.txt").read_text().strip()

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI

from .lean_server import LeanProject
from .editing import apply_unified_diff, propose_deterministic_fixes

console = Console()


class AgentState(TypedDict):
    """Graph state carried between nodes.

    Keys
    ----
    file : str
        Absolute or relative path to the Lean file being edited.
    iters : int
        Number of iterations performed so far.
    max_iters : int
        Upper bound on iterations (stopping criterion).
    status : Literal["ok","dirty","stuck"]
        Build status: 'ok' if build succeeded; 'dirty' if errors persist; 'stuck' if patch failed.
    errors : List[str]
        Raw diagnostic messages (typically stderr blobs).
    patch : Optional[str]
        A unified diff proposed by the LLM (or None if not available).
    """
    file: str
    iters: int
    max_iters: int
    status: Literal["ok", "dirty", "stuck"]
    errors: List[str]
    patch: Optional[str]


def diagnose_node(s: AgentState) -> AgentState:
    """Collect diagnostics for the target file using the project's toolchain.

    Sets:
      - s["errors"] to the list of error strings.
      - s["status"] to "ok" if no errors, otherwise "dirty".
    """
    proj = LeanProject.from_file(s["file"])
    diags = proj.diagnostics(s["file"])
    errs = [m.text for m in diags if m.severity == "error"]
    s["errors"] = errs
    s["status"] = "ok" if not errs else "dirty"
    return s


def build_node(s: AgentState) -> AgentState:
    """Run a full `lake build` to verify the project compiles.

    Updates:
      - s["status"] := "ok" when build succeeds, else "dirty"
      - s["errors"] := stderr blob on failure (if available)
      - s["iters"]  := s["iters"] + 1
    """
    proj = LeanProject.from_file(s["file"])
    rc, out, err = proj.run_lake_build()
    if rc == 0:
        s["status"] = "ok"
        s["errors"] = []
    else:
        s["status"] = "dirty"
        if err:
            s["errors"] = [err]
    s["iters"] += 1
    return s


def deterministic_fix_node(s: AgentState) -> AgentState:
    """Attempt deterministic (heuristic) fixes and keep improvements.

    Strategy:
      - Generate candidate edits from `propose_deterministic_fixes`.
      - For each edit:
          * Apply it to the file.
          * Re-diagnose with Lean.
          * If the error count did not increase, accept the edit and return.
          * Otherwise, revert and continue.

    The node returns immediately after the first non-worsening edit is accepted.
    If no edit helps, the original state is returned.
    """
    p = pathlib.Path(s["file"])
    src = p.read_text()
    edits = propose_deterministic_fixes(p, src, s["errors"])
    if not edits:
        return s
    for ed in edits:
        new_src = src[:ed.start] + ed.replacement + src[ed.end:]
        p.write_text(new_src)
        proj = LeanProject.from_file(s["file"])
        diags = proj.diagnostics(s["file"])
        errc = sum(1 for m in diags if m.severity == "error")
        console.log(f"Deterministic edit '{ed.note}' -> errors {errc}")
        if errc <= len(s["errors"]):
            s["errors"] = [m.text for m in diags if m.severity == "error"]
            return s
        else:
            # Revert if this edit made things worse.
            p.write_text(src)
    return s


def propose_llm_patch_node(s: AgentState) -> AgentState:
    """Ask an LLM for a *unified diff* patch to fix current errors.

    If `OPENAI_API_KEY` is not set, this node becomes a no-op and leaves `s["patch"] = None`.

    Returns:
      - s with `patch` set to the model output (string) or None if unavailable.
    """
    api = os.environ.get("OPENAI_API_KEY", "")
    if not api:
        s["patch"] = None
        return s
    # Default small model for patch proposals; tweak as needed.
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    prompt = f"""You are editing a Lean 4 file.
Errors:
{chr(10).join(s['errors'][:20])}

Return a unified diff (patch) that modifies only '{s['file']}' to fix errors.
If unsure, add missing imports or 'open' statements minimally.
"""
    resp = llm.invoke(prompt)
    s["patch"] = resp.content
    return s


def apply_patch_node(s: AgentState) -> AgentState:
    """Apply a unified diff to the workspace root.

    If the patch fails to apply, mark the state as 'stuck' so that the graph can terminate.
    """
    if not s["patch"] or not s["patch"].strip():
        return s
    ok = apply_unified_diff(s["patch"], pathlib.Path(s["file"]).parent)
    if not ok:
        s["status"] = "stuck"
    return s


def router_after_build(s: AgentState):
    """Routing logic after the build step.

    - If the build is 'ok' or we've reached the iteration budget, stop (END).
    - Otherwise, go back to 'diagnose' for another cycle.
    """
    if s["status"] == "ok" or s["iters"] >= s["max_iters"]:
        return END
    return "diagnose"


def cli(file: str = None, max_iters: int = 20):
    """Run the LangGraph agent on a target Lean file.

    Parameters
    ----------
    file : str
        Path to the Lean source file to repair.
    max_iters : int
        Maximum number of diagnose→fix→build cycles.

    Notes
    -----
    Produces console logs for each node's progress and finishes with a succinct "Done."
    """
    if file is None:
        console.print("[red]Provide --file path to a Lean file[/]")
        return

    # Build the graph structure.
    g = StateGraph(AgentState)
    g.add_node("diagnose", diagnose_node)
    g.add_node("deterministic", deterministic_fix_node)
    g.add_node("propose", propose_llm_patch_node)
    g.add_node("apply", apply_patch_node)
    g.add_node("build", build_node)

    g.set_entry_point("diagnose")
    g.add_edge("diagnose", "deterministic")
    g.add_edge("deterministic", "propose")
    g.add_edge("propose", "apply")
    g.add_edge("apply", "build")
    g.add_conditional_edges("build", router_after_build, {END: END, "diagnose": "diagnose"})
    app = g.compile()

    # Initial state
    state: AgentState = {
        "file": file,
        "iters": 0,
        "max_iters": max_iters,
        "status": "dirty",
        "errors": [],
        "patch": None,
    }

    # Stream execution for simple logging.
    for _ in app.stream(state):
        pass

    console.print("[bold green]Done.[/]")


