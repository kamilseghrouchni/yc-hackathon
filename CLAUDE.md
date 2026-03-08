# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a YC hackathon project. The repository is in early stages with a Python-oriented setup (see `.gitignore`).

## Language & Environment

- Python project (based on `.gitignore` configuration)
- No package manager or dependency file configured yet

## Workflow Orchestration

### 1. Plan Node Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One tack per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Pipeline Execution Protocol

When the user asks a perturbation biology question or says "run the pipeline":

1. **Keep the main conversation clean** — delegate ALL heavy work (Bash commands,
   file reads, web searches, data processing) to subagents. The main thread should
   only show high-level status updates and final results.

2. **Use background agents for independent work** — spawn agents with
   `run_in_background: true` when their results aren't immediately needed.
   You will be notified when they complete.

3. **Parallelize aggressively** — spawn multiple agents in a single message
   whenever steps are independent. Examples:
   - Assessment: spawn N assessment agents in parallel (per concurrent-assessment-workflow)
   - Identifier resolution: resolve genes and molecules in parallel

4. **Agent delegation pattern** — each subagent gets:
   - The relevant SKILL.md content as context
   - Specific input data (query, papers, file paths)
   - Expected output format
   - Full tool access (no permission blocks)

5. **Code execution in subagents** — NEVER write inline multi-line Python in Bash:
   - Write scripts to files first (`Write` tool → `scripts/tmp_<name>.py`)
   - Then execute with `Bash("python3 scripts/tmp_<name>.py")`
   - Then clean up: `Bash("rm scripts/tmp_<name>.py")`
   - This avoids security prompt blocks on multi-line commands with comments

5. **Status updates only** — in the main thread, report:
   - "Parsing your question..." → show structured query
   - "Searching for papers..." → show candidate count
   - "Assessing top N papers with 3 agents..." → show consensus ranking
   - "Preprocessing dataset..." → show cell/gene counts
   - Final results summary

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.
