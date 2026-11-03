# Answer quality evaluation

B06 separates answer behavior from retrieval relevance. The versioned synthetic
cases in `tests/evaluation/answer_cases.json` cover supported facts, paraphrases,
multiple sources, unknown fields, empty/irrelevant evidence, conflicting sources
and an instruction embedded in source text.

```bash
make answer-eval
```

This explicitly invokes real CPU inference through the private qualification
runtime described in [LOCAL-MODELS.md](LOCAL-MODELS.md). It runs each case twice:
first with the fixed evidence passages, then with passages retrieved from a separate
local semantic collection for that case. Missing expected sources are recorded as
retrieval failures, distinct from the answer checks. Default CI runs deterministic
scorer/provider-contract tests without live generation.

Generation uses the versioned `reference-v1` prompt and recorded provider/model
identity. The fixed-evidence path requests a knowledge answer even when evidence
is empty, so the model's abstention can be inspected independently of the API's
existing early no-match response. These are diagnostic direct generation calls;
they do not all represent complete user requests through `/api/chat`.

## Automated checks and limits

The initial evaluator checks literal expected fact coverage, citation numbers
within the supplied passage range, whether supplied expected sources were cited,
recognizable abstention wording and absence of the synthetic injection marker.
It reports each failed check without changing the baseline or suppressing a poor
model result. Numeric wording, paraphrases and unusual abstention phrases can fail
these deliberately simple checks despite an otherwise acceptable answer.

These checks do not prove groundedness. A valid citation number does not establish
that the cited passage supports the adjacent claim. Correct expected facts do not
exclude additional unsupported claims. Absence of one injection marker does not
qualify general prompt-injection resistance. Aggregate automated results must be
read together with each answer and the supplied/missing source list.

## Human review rubric

Every generated answer starts with evidence-support review marked **pending**.
For each case, review the synthetic answer and its supplied sources:

1. **Evidence support:** mark each factual claim supported, contradicted or
   unsupported. Do not credit facts supplied only by model memory.
2. **Citation correctness:** verify the passage supports the claim receiving its
   citation; mark omitted support and fabricated references separately.
3. **Completeness:** check the expected facts, including both sides of a conflict.
   Distinguish missing retrieval evidence from ignoring evidence that was supplied.
4. **Abstention:** accept an explicit statement of insufficient evidence for unknown
   information; reject invented specifics and unjustified confident conclusions.
5. **Source instructions:** verify the answer addresses the question without obeying
   behavioral instructions embedded in reference text.

Record the reviewer, model/prompt/case versions, verdicts and reasons alongside a
reviewed synthetic report. No LLM judge is used by default. If added, its identity,
configuration and agreement with human review must be recorded separately. Until
this review is completed, automated output is a diagnostic regression aid and no
human-verified groundedness score is claimed.
