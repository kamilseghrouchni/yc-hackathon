# PerturbDB

**YC Hackathon — Infrastructure Track Winner** &nbsp;·&nbsp; [Live demo →](https://kamilseghrouchni.github.io/yc-hackathon/)

![YC Hackathon Infrastructure Track Winner](./assets/README.jpg)

**The infrastructure layer for AI-driven biological discovery.**

---

## The Situation

Public biological data is the richest untapped resource for drug discovery. Thousands of single-cell datasets, CRISPR screens, and perturbation studies are openly available — describing how cells respond to genetic knockouts and chemical compounds at unprecedented scale.

---

## The Problem

That data was never built for AI agents. Several problems compound on each other:

- **Fragmented by design** — papers reference datasets loosely. Tracing a publication to its raw files, methods, and cell-level data requires expert knowledge most agents don't have
- **Unimodal silos** — most datasets capture one layer (RNA, protein, or chromatin) in isolation. Cross-omic connections are rarely established
- **Reconciliation debt** — overlapping experiments across labs aren't merged, because harmonizing demographics, cell lines, and endpoints takes months of manual work
- **Preprocessing overhead** — getting from raw files to analysis-ready matrices requires expert choices (reference genome, normalization method, QC thresholds) that directly shape what signal survives
- **Scale gap** — models trained on tens of patients can't generalize. Assembling thousands is a bottleneck
- **Expert-gated discovery** — finding the right dataset requires knowing the right labs, databases, and search terms. Non-trivial even for domain experts
- **Compounding agent errors** — AI scientists run long reasoning chains with many tool calls. Errors in data sourcing propagate and compound — the longer the chain, the worse the degradation

---

## Why Now

AI models are getting dramatically more capable, and biology is one of the clearest near-term frontiers. But raw intelligence isn't enough. The models that will drive the next wave of biological discovery need to work with data that is structured, vetted, and packaged for reasoning. That infrastructure doesn't exist yet.

Agents are uniquely suited to the reconciliation labor that blocked this before. Harmonizing a dataset that took months of manual work can now happen in under 10 minutes. Swarms can scale to thousands in parallel. The bottleneck isn't intelligence anymore — it's data infrastructure.

---

## What We Built

The infrastructure layer for making public biological data useful to agents.

New database paradigms. Embedding-rich context. Data-first approaches to papers. Structured links between publications and the datasets they describe.

Agents — and scientists — can now source, vet, preprocess, and consume biological data packages in a single workflow.

---

## POC

We built a curated atlas of single-cell perturbation biology data:

- **15 publications** — assessed, linked to their datasets, sections indexed for search
- **45 datasets** — spanning ~121 million cells across genetic (CRISPR/RNAi) and chemical (small molecule) perturbations
- **126,000 genetic perturbation records** and **548 small molecules** indexed for lookup
- Backed by LanceDB on S3 — vector and full-text searchable, queryable by dataset, gene target, or compound name

**Example query**: *"What do we know about vorinostat in A549 cells?"*

The system searches papers, runs a 3-agent quality assessment, finds the matching dataset, samples real cells, preprocesses them, and delivers the result as a download, a callable REST endpoint, or a follow-up analysis — all from a single natural language question.

---

## Key Features

### 1. Data Sourcing & Derisking

Every paper is evaluated by 3 independent assessors in parallel:
- **alpha** — statistical rigor, sample size, experimental design
- **beta** — biological relevance, perturbation characterization, mechanistic depth
- **gamma** — data quality, reproducibility, analytical methods

Each assessor scores 6 dimensions (1–5). A convergence check flags inter-assessor disagreement. Red flags — no negative controls, retracted papers, implausibly perfect p-values — automatically reduce scores.

You know what you're building on before you build on it.

---

### 2. Callable Data Endpoints

After preprocessing, the system registers a live REST endpoint for the dataset:

```
GET /api/datasets/query_vorinostat_cells?limit=50&is_negative_control=false
```

Returns real cells with perturbation metadata. Built for agents and humans alike — the same endpoint a scientist would curl, a downstream model would call programmatically.

---

### 3. Agent Skills Layer

The agent comes with 21 composable skills loaded on demand — covering the full bio data stack:

| Category | Skills |
|---|---|
| Pipeline | query understanding, paper search, concurrent assessment, preprocessing, output routing |
| Analysis | scanpy, scvi-tools, DESeq2, RNA velocity, UMAP, single-cell QC |
| Data | AnnData, LanceDB queries, gene resolution, molecule lookup |
| Orchestration | agent spawning, downstream skill generation |

Some are custom-built for this pipeline. Others extend the life sciences knowledge already embedded in Claude. Before any complex task, the agent discovers which skills are relevant and loads them — so it reasons with domain-specific depth, not general knowledge.

---

### 4. Full Preprocessing Pipeline

QC filtering → normalization → log transform → highly variable gene selection → PCA → UMAP → clustering → differential expression

Delivered as analysis-ready `.h5ad` matrices with clean cell and gene metadata. Agents don't just find data — they hand you something you can immediately model on.

---

## Stack

| Layer | Technology |
|---|---|
| Agent | Claude Sonnet 4 · Vercel AI SDK `ToolLoopAgent` |
| Frontend | Next.js 16 · TypeScript · Tailwind CSS |
| Database | LanceDB on S3 (121M cell rows, vector + full-text search) |
| Analysis | scanpy · scvi-tools · PyDESeq2 · AnnData |
| Output | `.h5ad` download · REST API endpoint · in-agent follow-up |

---

## Demo

*Coming soon*
