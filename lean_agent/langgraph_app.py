from __future__ import annotations
import os, pathlib
from typing import TypedDict, List, Literal, Optional
from rich.console import Console
from dotenv import load_dotenv

load_dotenv()
if "OPENAI_API_KEY" not in os.environ and pathlib.Path("openai_key.txt").exists():
    os.environ["OPENAI_API_KEY"] = pathlib.Path("openai_key.txt").read_text().strip()

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from .lean_server import LeanProject
from .editing import apply_unified_diff, propose_deterministic_fixes

console = Console()

class AgentState(TypedDict):
    file: str
    iters: int
    max_iters: int
    status: Literal["ok","dirty","stuck"]
    errors: List[str]
    patch: Optional[str]

def diagnose_node(s: AgentState) -> AgentState:
    proj = LeanProject.from_file(s["file"])
    diags = proj.diagnostics(s["file"])
    errs = [m.text for m in diags if m.severity == "error"]
    s["errors"] = errs
    s["status"] = "ok" if not errs else "dirty"
    return s

def build_node(s: AgentState) -> AgentState:
    proj = LeanProject.from_file(s["file"])
    rc, out, err = proj.run_lake_build()
    if rc == 0:
        s["status"] = "ok"; s["errors"] = []
    else:
        s["status"] = "dirty"
        if err: s["errors"] = [err]
    s["iters"] += 1
    return s

def deterministic_fix_node(s: AgentState) -> AgentState:
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
            p.write_text(src)
    return s

def propose_llm_patch_node(s: AgentState) -> AgentState:
    api = os.environ.get("OPENAI_API_KEY","")
    if not api:
        s["patch"] = None
        return s
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
    if not s["patch"] or not s["patch"].strip():
        return s
    ok = apply_unified_diff(s["patch"], pathlib.Path(s["file"]).parent)
    if not ok:
        s["status"] = "stuck"
    return s

def router_after_build(s: AgentState):
    if s["status"] == "ok" or s["iters"] >= s["max_iters"]:
        return END
    return "diagnose"

def cli(file: str = None, max_iters: int = 20):
    if file is None:
        console.print("[red]Provide --file path to a Lean file[/]")
        return
    g = StateGraph(AgentState)
    g.add_node("diagnose", diagnose_node)
    g.add_node("deterministic", deterministic_fix_node)
    g.add_node("propose", propose_llm_patch_node)
    g.add_node("apply", apply_patch_node)
    g.add_node("build", build_node)

    g.set_entry_point("diagnose")
    g.add_edge("diagnose","deterministic")
    g.add_edge("deterministic","propose")
    g.add_edge("propose","apply")
    g.add_edge("apply","build")
    g.add_conditional_edges("build", router_after_build, {END: END, "diagnose": "diagnose"})
    app = g.compile()

    state: AgentState = {"file": file, "iters": 0, "max_iters": max_iters, "status":"dirty", "errors": [], "patch": None}
    for _ in app.stream(state):
        pass
    console.print("[bold green]Done.[/]")
