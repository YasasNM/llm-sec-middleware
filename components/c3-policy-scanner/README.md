# C3 RAG Output Policy Scanner

Owner: Yasas · Port: 8003

- Build blueprint: [BLUEPRINT.md](BLUEPRINT.md)
- Shared interfaces: [../../contracts/CONTRACTS.md](../../contracts/CONTRACTS.md)
- Whole product: [../../PROJECT_BLUEPRINT.md](../../PROJECT_BLUEPRINT.md)
- Train on Colab (manual steps): [../../docs/runbooks/c3-training-colab.md](../../docs/runbooks/c3-training-colab.md)
- Which data is used, with licences: [../../docs/reports/progress/c3/data-sources.md](../../docs/reports/progress/c3/data-sources.md)
- Results of the first training run: [../../docs/reports/progress/c3/training-results-v0.md](../../docs/reports/progress/c3/training-results-v0.md)

## What exists

| Part | Requirements | State |
|------|--------------|-------|
| Policy parsing: PDF and Markdown to clauses with stable ids, lint. Handles real printed Acts (margin notes, alternating headers), Roman-numeral guidance and long sections | FR1, FR11, FR20 | Done, tested on the real Sri Lankan Act and FDA guidance |
| Live indexing: diff, embed only changed clauses, copy-on-write, one index per organisation | FR2, FR12, NFR13 | Done, tested |
| Policy API, file watcher, latency bench | FR10, FR13, FR18 | Done |
| `/v1/scan`: two-stage scan, grounding check, leak re-check, audit, fail closed, C4 events with outbox | FR3 to FR9, FR14 to FR17, FR25, FR27 | Done, tested against the shared contracts |
| Training pipeline: retriever, generator, calibration, evaluation, automatic leakage checks | FR21, FR22, FR26 | Runs end to end |
| **Public-data training**: real laws, NIH answers, Aegis labels, generated answers for real clauses | | Pipeline done and smoke-tested. **The full run needs Colab** (see below) |
| **Demo with a web UI** (clinician and admin views, live policy edit, C4 events) | | Done, `python -m c3.demo.launcher` |
| Gateway integration, Presidio, int8 serving, S3 and S4 sets | | Not started |

## Demo

```bash
pip install -e ".[dev]"
python -m c3.demo.launcher          # opens http://127.0.0.1:8000
```

Needs the trained models in `models/` (see the runbook; the folder already holds a first set). On Windows you can
double-click `start_demo.bat` and `run_checks.bat`, which always use the project's own Python.
It starts three local services: the C3 policy check (8003), a C4 stub (8004) and a mock gateway with the web
page (8000). The page has four tabs:

- **Chat.** Pick an example, edit the question or the assistant's answer, press *Check and send*.
  Switch **View as: Admin** to see the rule, the quote, the model's confidence and the timing. A clinician
  sees only a safe message and a reference. The *Was this result right?* buttons save your judgement to
  `var/feedback.jsonl` for later labelling.
- **Policies.** Load the small Riverside policy, the real Sri Lankan Act (PDF) or the HIPAA Privacy Rule.
  Upload, replace or delete a document, browse its clauses, or **edit the policy text and save**: the next check
  uses the new version, with the time-to-live shown.
- **Behaviour.** The events C3 sent to the C4 stub and a per-user level that rises with repeated violations.
- **About.** A plain-language explanation for the audience.

A real LLM can be connected with `--ollama http://localhost:11434 --ollama-model qwen2.5:7b`.

## Train

```bash
pip install -e ".[train,dev]"
python -m c3.training.public.fetch                     # download the public sources
python -m c3.training.public.generate ...              # answers for the real clauses (use Colab + Qwen2.5-7B)
python -m c3.training.pipeline --data public --stage all
```

The blueprint trains on Colab: open `training/notebooks/c3_train_colab.ipynb`, or follow the runbook. The earlier
hand-written pack is still available as `--data synthetic`, but it is no longer the default.

## Data in one paragraph

Policies are real: HIPAA Privacy and Breach rules, 42 CFR Part 2, 45 CFR Part 92 (US federal law, public domain), the
FDA clinical decision support guidance and the Sri Lankan Personal Data Protection Act 2022 (held out for testing).
Answers are real where possible: MedQuAD (NIH) and NVIDIA Aegis 2.0 (CC BY 4.0, human or LLM-jury labels). No public
dataset pairs clinical policy clauses with violating answers, so the answers that break the real clauses are written
by an open LLM and kept only if a judge agrees; a person can review them in a spreadsheet. See the data-sources page
for the exact numbers.

## Known limits

- `models/` holds a model trained on the **hand-written** pack with paired rule-removal rows, so it reads the policy
  (live edits change the verdict) but over-flags (false alarm rate about 0.3 to 0.5 on held-out sets). The first Colab
  public-data model is in `models_public_v1_colab/`: it scored S2 F1 0.97 but caught only 2 of 10 hand-written
  violations, because it learned the style of the machine-written answers. Rebuild the Colab zip (the code changed)
  and train again; check the three results in section 2b of the runbook before trusting any model.
- The demo shows a model trained on templated language. It misses some paraphrased violations (for example
  "She can skip the tablets for a few days." alone) and should be presented as a demonstration, not a clearance.
- The keyword baseline carries no signal; B1, B2 and B4 to B6 are not built.
- Docker image not built (no Docker on the dev machine).
- `sentence-transformers` and `datasets` are not used: on Windows machines with Application Control their `pyarrow`
  dependency fails. `TransformerEmbedder` does the same mean pooling on plain `transformers`.

## Data and models

Only manifests, the small demo policy and records are committed. Downloaded sources (`data/raw/`), generated data
(`data/processed/`), weights and tokenizers are git-ignored and live on Drive (K29).
