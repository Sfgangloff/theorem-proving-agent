# Lean Auto Agent — with LLM & LangGraph Orchestration

An **autonomous Lean 4 coding agent** that can:
1. Start from any Lean 4 project or file.
2. Propose **deterministic fixes** and/or **LLM-powered edits**.
3. Compile with **Lake**, gather diagnostics, and self-repair until it builds successfully.
4. (Optional) Perform **innovation cycles**: extend the file with new thematically relevant results.
5. (Optional) Enrich the code with **automatic documentation** explaining definitions, theorems, and proof steps.

This edition includes a **LangGraph** state machine (`lean-agent-graph`) for step-wise orchestration with checkpoints.

---

## ✨ Features

- **Autonomous compile–repair loop** for Lean 4 files.
- **Deterministic fix rules** in `editing.py` for common error patterns.
- **LLM repair mode** using GPT-5 for complex fixes (optional).
- **Innovation mode** to add new results/definitions in a chosen theme.
- **Documentation mode** to auto-comment and summarize your Lean file.
- Works with **plain CLI** (`lean-agent`) or **LangGraph orchestrator** (`lean-agent-graph`).

---

## 🚀 Quickstart

### 0. Install Lean 4 & Lake
Follow the official [Lean Getting Started Guide](https://leanprover-community.github.io/get_started.html).  
Ensure `lean` and `lake` are available in your `PATH`.

### 1. Set up Python environment
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 2. Install Lean Auto Agent
```bash
pip install -e .
```

### 3. Configure OpenAI API key
Required for LLM-powered repair, innovation, and documentation.
```bash
# Option A: .env file
echo "OPENAI_API_KEY=sk-your-key" > .env

# Option B: text file fallback
echo "sk-your-key" > openai_key.txt
```

### 4. Run the agent
```bash
# Minimal run — deterministic fixes only if no API key
lean-agent run --file sample_project/Play.lean

# With LangGraph orchestration
lean-agent-graph --file sample_project/Play.lean --max-iters 10

# With innovation (e.g. 2 updates in number theory)
lean-agent run --file MyFile.lean --updates 2 --theme "number theory"

# With innovation + documentation
lean-agent run --file MyFile.lean --updates 1 --theme "topology"
```

---

## 🛠 How it Works

- **Lean server diagnostics**: Quick error detection via `lean --server`.
- **Lake build confirmation**: Ensures the project compiles after each change.
- **Repair loop**:
  1. Try deterministic edits from `editing.py`.
  2. If needed, call GPT-5 for repairs.
- **Innovation loop**: Insert thematically consistent new results.
- **Documentation step**: Annotate with a module docstring, theorem summaries, and proof comments.

---

## 📂 Project Layout

```
lean_agent/
  main.py            # Typer CLI for classic loop
  runner.py          # Compile/repair/innovate/document loop
  lean_server.py     # Lean server + Lake wrappers
  editing.py         # Deterministic fix rules and diff applier
  git_utils.py       # Scratch branch creator
  langgraph_app.py   # LangGraph orchestration CLI
sample_project/
  lakefile.lean
  lean-toolchain
  Play.lean
.env.example         # Example env vars
```

---

## 📜 License
MIT License.
