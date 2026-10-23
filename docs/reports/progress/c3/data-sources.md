# C3 data sources

Which public data C3 uses, under what licence, and what is real or machine-generated.
Rebuild everything with `python -m c3.training.public.fetch` (URLs, licences and SHA-256 hashes are recorded in `components/c3-policy-scanner/data/public_sources.json`).

## The short version

- **Policies are real.** US health-privacy law, a Sri Lankan Act and an FDA guidance document. Nothing about the policy side is invented.
- **Answers come from three places:** real NIH medical answers, real LLM responses that people or an LLM jury labelled, and answers an open LLM writes for the real clauses.
- **The machine-written part is the only synthetic part.** There is no public dataset of "clinical AI policy clause + AI answer + violation label", so it has to be produced. Each generated answer is kept only if a judge agrees with its intended label, and a person can review the sheet (`review.py`). The hand-written rule bank is no longer used for training.

## Sources

| Source | Kind | Licence | Used for | Real? |
|---|---|---|---|---|
| [HIPAA Privacy Rule, 45 CFR Part 164 Subpart E](https://www.ecfr.gov/api/versioner/v1/full/2024-12-31/title-45.xml?part=164&subpart=E) | policy | US federal law, public domain | policy clauses: uses and disclosures of health information | real |
| [HIPAA Breach Notification Rule, 45 CFR Part 164 Subpart D](https://www.ecfr.gov/api/versioner/v1/full/2024-12-31/title-45.xml?part=164&subpart=D) | policy | US federal law, public domain | policy clauses: incident and breach reporting | real |
| [Confidentiality of Substance Use Disorder Records, 42 CFR Part 2](https://www.ecfr.gov/api/versioner/v1/full/2024-12-31/title-42.xml?part=2) | policy | US federal law, public domain | policy clauses: disclosure of sensitive records | real |
| [Nondiscrimination in Health Programs, 45 CFR Part 92 (incl. 92.210 patient care decision support tools)](https://www.ecfr.gov/api/versioner/v1/full/2024-12-31/title-45.xml?part=92) | policy | US federal law, public domain | policy clauses: use of AI decision support tools | real |
| [FDA guidance: Clinical Decision Support Software (2022)](https://www.fda.gov/media/109618/download) | policy | US government work, public domain | policy clauses: what AI decision support may and may not do | real |
| [Sri Lanka Personal Data Protection Act, No. 9 of 2022](https://www.parliament.lk/uploads/acts/gbills/english/6242.pdf) | policy | Official government publication | policy clauses: held-out national data protection law | real |
| [MedQuAD medical question answering (NIH sources)](#) | answers | see dataset card (NIH public content) | real medical answers: compliant bodies and clinical questions | real |
| [NVIDIA Aegis AI Content Safety Dataset 2.0](#) | labelled responses | CC BY 4.0 | real LLM responses with human or jury safety labels and violated categories | real |
| [DynaBench test (DynaGuard)](#) | policy following | MIT | external policy-following benchmark, never trained on | real |

Not downloaded: the WHO AI-for-health guidance (blocked by a bot check), GDPR and the EU AI Act (EUR-Lex returns a challenge page). They can be added by hand as Markdown or PDF.

## How the policy side is split

| Role | Organisation | Documents |
|---|---|---|
| train | pub-train | HIPAA Privacy Rule, HIPAA Breach Rule, 42 CFR Part 2 |
| validation | pub-val | 45 CFR Part 92 (includes AI decision support tools) |
| held-out test | pub-test | Sri Lanka PDPA 2022, FDA CDS guidance (different country and style) |
| general conduct | pub-conduct | nine short rules written for the Aegis safety categories |

Held-out means whole documents. No clause of a validation or test document appears in any training row, and the run fails if one does.

## What the last run trained on

| Source | Items |
|---|---|
| aegis2 | 1273 |
| llm-generated | 1331 |
| medquad | 295 |

- Real text: **54%**
- Machine-generated: **46%**
- Machine-generated and checked by a person: **0%**
- Hand-written templates: **0%**

## Honest limits

- Generated answers are written by a language model told to break or follow a clause. Their labels are right by construction and filtered by a judge, but they are still machine text.
- Aegis responses are general chat safety data, not clinical. They teach the model to read a rule and cite it, and they supply real labels, but they are not hospital-specific.
- Legal text is not written for AI assistants, so some clauses are hard to break in an answer.
- Everything here still needs real answers from the protected LLM, labelled by people, before any research claim (blueprint 8.4).
