# C3: train the models on Colab (manual steps)

The blueprint trains C3 on Google Colab (section 13.2). This is the step-by-step version.
Everything the notebook does can also be run on your own machine (second half).

**What you get:** a fine-tuned retriever, a fine-tuned generator, a calibration file, a results report
and a data report saying exactly how much of the data was real. About 1 hour on a free T4.

## 0. What the training uses

| Part | Source | Real? |
|---|---|---|
| Policies | HIPAA Privacy and Breach rules, 42 CFR Part 2, 45 CFR Part 92, FDA clinical decision support guidance, Sri Lanka PDPA 2022 | real |
| Answers | MedQuAD (NIH), NVIDIA Aegis 2.0 | real |
| Answers for the real clauses | written by Qwen2.5-7B, kept only if a judge agrees, optionally checked by a person | machine-generated |

Full list with licences: `docs/reports/progress/c3/data-sources.md`. The hand-written rule bank is not used.

## 1. On Colab

1. **Make the zip.** Redo this whenever the code changes (for example after the rule-removal training rows were
   added), otherwise Colab trains with old code. On your machine, from the repository root (Windows 10/11 has `tar`):
   ```powershell
   cd components
   tar -a -c -f c3-policy-scanner.zip --exclude=.venv --exclude=models --exclude=var --exclude=__pycache__ --exclude=data/raw c3-policy-scanner
   ```
2. **Upload** `c3-policy-scanner.zip` to Google Drive at `MyDrive/c3/`.
3. **Open** `components/c3-policy-scanner/training/notebooks/c3_train_colab.ipynb` in Colab
   (upload it, or File > Open notebook > Upload).
4. **Pick a GPU:** Runtime > Change runtime type > **T4 GPU**.
5. **Run the cells in order.** Change only the Settings cell if you need to
   (`PER_DOC` is how many clauses per policy document get answers; 250 is a good start).
6. **Optional human review** (cell 6): download `review.csv`, open it in Excel or Sheets, write `yes` or `no`
   in `human_ok` for any row you checked, upload it back as `review_done.csv`, run the next cell.
   Even 100 checked rows is useful, and the report will say how many were checked.
7. **Training cell.** It stops on its own if the data checks find leakage between train and test.
8. **Save.** The last cells copy `models/` and `results/` to `MyDrive/c3/run1/`.

If Colab disconnects, reopen the notebook, rerun Settings, Get the code and Install, then the generation cell.
Generation resumes from `generated_answers.jsonl`; training starts again from the beginning (about 25 minutes).

## 2. Put the models back in the project

1. Download `run1/models/` from Drive.
2. Copy its contents into `components/c3-policy-scanner/models/` so you have
   `generator/`, `retriever/`, `manifest.json`, `trained_clause_hashes.json`.
3. Start the demo: `python -m c3.demo.launcher`. Each model file is checked against
   `manifest.json` at start, so a damaged copy is refused.

## 2b. Check the model before you trust it

A high score on machine-written test answers is not enough. The first public-data run scored S2 F1 0.97 yet
caught only 2 of 10 hand-written violations, because it had learned the *style* of the generated answers.
Three checks catch that:

1. **Per-source results.** In `results/evaluation.json`, read `by_source`. Real Aegis rows are the honest number.
   If `llm-generated` is near 1.00 with zero false alarms while the right clause is rarely retrieved, be suspicious.
2. **Rule dependence.** `RESULTS.md` has a line "X% of caught violations are let go when the broken rule is removed
   from the context". A model that reads the rules should be well above 70%.
3. **Hand-written cases.** `python tools/smoke_real_models.py` runs 20 answers a person wrote. Expect at least
   8 of 10 violations caught and at most 1 false alarm.

If a check fails, do not use the model for a demo. Keep the previous `models/` folder (rename it first, for
example `models_old/`) and send the three results to whoever is tuning the data.

## 3. Using your own machine instead

```bash
cd components/c3-policy-scanner
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[train,dev]"
python -m c3.training.public.fetch                         # download public sources (2 min)

# answers for the real clauses, pick ONE:
python -m c3.training.public.generate --backend ollama --model qwen2.5:7b --url http://localhost:11434
python -m c3.training.public.generate --backend hf --model Qwen/Qwen2.5-1.5B-Instruct   # weak, for testing the pipeline only

python -m c3.training.pipeline --data public --stage all   # train, calibrate, evaluate
python -m c3.training.public.datacard                      # refresh the data report
```

The first option uses the project's protected LLM through the SSH tunnel. A 1.5B model writes answers that are too
similar to be useful (the judge cannot separate them), so use it only to check that the pipeline runs.

## 4. Reading the results

* `results/RESULTS.md`: F1 with a 95% interval on S1 (policies seen in training), S2 (held-out documents) and
  S6 (instruction injection), next to the untrained model. Look at S2 first.
* `results/data_provenance.json`: share of real, machine-generated and human-checked data.
* `results/evaluation.json`: the same numbers split by data source (`by_source`), so real and generated data are never
  blended into one score.

## 5. Problems

| Symptom | Fix |
|---|---|
| `pip` says CERTIFICATE_VERIFY_FAILED on Windows | Avast inspects HTTPS. Set `PIP_CERT` to Avast's `wscert.pem` for pip only |
| `ImportError ... pyarrow ... Application Control` | Windows blocks a pyarrow DLL. C3 avoids `datasets` and `sentence-transformers` for this reason; reading parquet works |
| Out of memory on the T4 while generating | Lower `--batch` (default 16) or use `Qwen/Qwen2.5-3B-Instruct` |
| Data check fails with "held-out clause" | A validation or test clause reached a training row. Do not bypass it; report the clause id |
| Few answers kept | The judge is strict by design. Lower `--v-min` (default 0.6) a little, then review a sample by hand |
