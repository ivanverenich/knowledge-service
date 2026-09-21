# Evaluation strategy

Evaluation is a module with versioned inputs and reproducible artifacts, not a notebook run after implementation.

## Dataset

Target approximately 200 verified Evaluation Cases, balanced across English and Ukrainian. Label each case with language of Question, language of expected Evidence, query class, answerability, difficulty, expected Documents/Chunks, expected claims, and Authorization Context. Maintain dedicated sets for:

- English to English;
- Ukrainian to Ukrainian;
- Ukrainian to English and English to Ukrainian;
- exact identifiers and rare terms;
- multi-hop questions;
- conversational follow-ups;
- unanswerable or insufficient-evidence questions;
- permission boundaries and revocations;
- prompt injection and citation attacks.

Synthetic generation proposes cases; a human verifies every retained case. A second bilingual reviewer calibrates 30–50 representative cases.

## Metrics

| Layer | Metrics |
|---|---|
| Retrieval | Recall@k, MRR, nDCG@k, per-language and cross-language slices |
| Reranking | delta in nDCG/Recall, latency, token/GPU cost |
| Answers | correctness rubric, faithfulness, completeness, abstention quality |
| Citations | citation validity, precision, claim support, source coverage |
| Security | unauthorized Candidates/Evidence/Citations; required value is zero |
| Operations | retrieval latency, time to first token, completion latency, error rate |
| Economics | embedding/index cost, tokens and estimated cost per Answer/Evaluation Run |

Establish thresholds only after measuring a named baseline. Thereafter, releases fail on ACL leakage or on agreed threshold/regression violations.

## Judge policy

Pin judge provider/model/version where possible. Version the rubric and structured schema. Store raw judge results as restricted evaluation artifacts. Calibrate automated scores against blinded human ratings and investigate disagreements. A judge score is evidence, not truth.

## Execution tiers

- Every commit: deterministic unit, property, contract, integration, and small offline retrieval tests.
- Scheduled/manual: managed-model evaluation with an explicit spend ceiling.
- Before release: full bilingual dataset, adversarial suite, latency/cost report, and a human review sample.
