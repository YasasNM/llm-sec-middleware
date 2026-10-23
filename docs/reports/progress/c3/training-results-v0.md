# C3 training results, v0

**Status:** first end-to-end run on synthetic data. It shows that the pipeline works and gives a
first model. It is not a research result. Reproduce with
`python -m c3.training.pipeline --stage all` in `components/c3-policy-scanner/`.

## What the data is

- 30 hand-written rules in six categories (`src/c3/training/rules_bank.py`), each written in
  several wordings, with violating answers, compliant look-alikes and a "permit" rewrite.
- 5 training organisations (3 documents each), 2 validation organisations, 4 held-out test
  organisations. Six rules appear only in validation and test.
- Documents are Markdown read back through the real parser, so clause ids match the product.
- Split by generation batch. Automatic checks fail the run on leakage: no held-out clause text in
  any training row or triplet, no batch in two splits, no duplicate rows, targets parse.
- Training and test answers use different lead-ins, tails and phrasing. Validation uses a third
  set. Labels are correct by construction (silver), but the language is templated.

## Retriever (recall of the gold clause in the top 5)

| Split | Untuned MiniLM | Fine-tuned |
|---|---|---|
| Validation, training organisations | 0.54 | 0.95 |
| S1, seen policies | 0.59 | 0.89 |
| Validation, unseen organisations | 0.73 | 0.89 |
| S2, unseen policies | 0.73 | 0.87 |

Recall is a ceiling on the whole system: a violation whose clause is not retrieved is missed.

## Generator (flan-t5-small, 77M parameters)

| Set | System | F1 | 95% interval | Recall | False alarm rate | Top-1 clause | Valid citations |
|---|---|---|---|---|---|---|---|
| S1 seen | C3 trained | 0.95 | 0.92 to 0.98 | 1.00 | 0.08 | 0.85 | 1.00 |
| S1 seen | B3 untrained | 0.59 | 0.53 to 0.65 | 0.90 | 0.95 | 0.77 | 0.58 |
| S2 unseen | C3 trained | 0.81 | 0.75 to 0.87 | 0.78 | 0.10 | 0.61 | 1.00 |
| S2 unseen | B3 untrained | 0.59 | 0.54 to 0.64 | 0.97 | 0.96 | 0.72 | 0.68 |
| S6 injection | C3 trained | 0.81 | 0.75 to 0.86 | 0.88 | 0.23 | 0.68 | 1.00 |
| S6 injection | B3 untrained | 0.60 | 0.54 to 0.66 | 0.96 | 0.98 | 0.55 | 0.47 |

- Training clearly beats the untrained model on every set (paired McNemar, p below 0.001).
- **S2 is 0.14 below S1.** The blueprint aims for within 0.10, so there is no generalisation claim.
  S1 is easy because it reuses training phrasing. Look at S2 and S6.
- Order consistency (same answer, shuffled clause labels, same verdict): 0.99 on S1.
- Parse rate 1.00. Quote grounded 0.93 to 1.00. Repair rate 0.00 to 0.07.
- The keyword baseline (B0) chose a threshold that flags everything, so it carries no signal.

### How the first run failed, and what changed

| Run | S1 F1 | S2 F1 | Cause and fix |
|---|---|---|---|
| Plain loss, 4 epochs | 0.66 | 0.65 | The verdict is one token in about 40, so its loss was diluted. Fix: weight that token. |
| Verdict-weighted loss | 0.995 | 0.69 | The model memorised three violating sentences per rule. Fix: lead-ins, tails, synonym swaps. |
| More varied training text | 0.95 | 0.77 | Validation used training phrasing, so thresholds were too eager. Fix: separate validation phrasing. |
| Separate validation phrasing | 0.95 | **0.81** | Current. |

## Calibration

Temperature 6.9 and thresholds of 0.12 for contexts with a high or medium severity clause.
Temperature scaling lowered the loss but raised the expected calibration error slightly
(0.11 to 0.14). Thresholds come from two validation organisations only.

## Speed

| Measure | Result | Target |
|---|---|---|
| Update latency, file save, p95 | 0.42 s | 2 s |
| Update latency, upload, p95 | 6 ms | 10 s for 100 clauses |
| Pass path, CPU 4 threads, p95 | 0.09 s | 0.6 s |
| Violation path, CPU 4 threads, p95, model only | 0.81 s | 1.0 s |
| Decoding about 45 tokens, p95 | 0.64 s | 0.55 s for 30 tokens |
| int8 dynamic quantisation | slower than fp32 | |

Update latency used the stand-in embedder, a 5-clause document and a retrieval probe, so it is a
lower bound. The violation path has little margin. The first lever is to drop the generated quote.
These are dev-laptop numbers, not the demo machine, so Gate G1 is not signed off.

## Hand-written check on the real models

20 answers written for this check against a policy that is not from the rule bank:
all 20 verdicts correct (10 of 10 violations withheld, 0 of 10 false alarms), but only 7 of 10
violations cited the right clause. Several violations had a probability close to the threshold.
In a separate run through the HTTP app, one employer-disclosure answer was missed.

## What is needed next

1. Real answers from the protected LLM, labelled by people (blueprint 8.4). This is what turns
   the numbers above into a result.
2. A stronger baseline than B0, and the S3 and S4 sets.
3. Train without the generated quote and re-check Gate G1 on the demo machine.
4. Presidio for names, and the gateway integration test.

---

## Public-data run on Colab (first attempt): what it showed

This run used real policies (HIPAA, 42 CFR Part 2, 45 CFR Part 92, Sri Lanka PDPA, FDA) with 2,899 items:
1,273 Aegis, 295 MedQuAD and 1,331 answers written by Qwen2.5-7B (46%). None were human-checked.

| Check | Result |
|---|---|
| S2 F1 (held-out Sri Lankan Act and FDA), machine-written answers | 0.97, no false alarms |
| Real Aegis responses, S1 F1 | 0.87, false alarm rate 0.25 |
| Gold clause in the top 5 on S2 | 0.20 |
| Right clause cited on S2 | 0.08 |
| 20 hand-written cases | 11 of 20 right, only 2 of 10 violations caught |

**Reading.** The 0.97 is not a sign that the model understands the rules. It is almost perfect on machine-written
answers while the right rule is retrieved only 20% of the time, so it is separating "careless-sounding" from
"careful-sounding" text. The real Aegis number (0.87) and the hand-written cases are the honest picture.

**Fix added.** Training now includes *rule-removal* rows: the same violating answer with the rule it breaks taken out of
the context, labelled pass. The text is identical, so only the rules can explain the target. The evaluation also reports
*rule dependence*, the share of caught violations that are let go when the broken rule is removed.

**Next.** Re-zip the code, rerun the Colab training, and check the three results in section 2b of the runbook.
