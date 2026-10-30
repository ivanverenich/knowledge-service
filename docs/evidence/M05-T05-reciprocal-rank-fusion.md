# M05-T05 — Reciprocal Rank Fusion evidence

Completed: 2026-10-30

## Invariant

Fusion returns each Chunk that any branch found exactly once, and no other
Chunk. Its score is the sum of `1 / (k + rank)` over the branches that
found it, and it lists each branch's part in branch order. Fused
Candidates are ordered by exact score, highest first, then by Chunk ID.
Bad input is refused with `InvalidFusionInput` before anything is
returned, and no Document text appears in an error message or a repr.

## Expected failing check

Before the change, `tests/test_reciprocal_rank_fusion.py` fails to import
`knowledge_service.retrieval.fusion`.

## Predictions

Before running anything, write what you expect:

- Dense search ranks Chunk 1 first, and lexical search does not find it.
  Chunk 2 is fourth in both lists. Which comes first with k=60? With k=1?
- One Chunk is first in dense search only, another first in lexical search
  only. What are their scores, and which comes first?
- Lexical search found nothing. What does fusion return?
- Lexical search lists the same Chunk twice. What should fusion do?
- `1/63 + 1/140` and `1/84 + 1/90` are equal. Do Python floats agree?

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

- With k=60, Chunk 2 comes first: fourth place in both lists gives
  1/64 + 1/64 = 2/64, about 0.031, more than first place in one list,
  1/61, about 0.016. With k=1, Chunk 1 comes first: first place gives 1/2,
  while fourth place twice gives only 1/5 + 1/5 = 2/5.
- Both get 1/61, so they tie exactly. The one with the lower Chunk ID
  comes first, the same tie rule every branch uses.
- Fusion returns the dense Candidates in their own order, each with one
  dense contribution and nothing from lexical search. A missing branch
  adds nothing; it is not an error.
- Fusion should refuse it with `InvalidFusionInput`. Counting it twice
  would give it two contributions from one search and push it above
  Chunks it does not deserve to beat, and a branch that repeats a Chunk
  has broken its own contract.
- No. As exact fractions both sums are 29/1260, but as floats the first
  is 0.023015873015873014 and the second 0.023015873015873017. Sorting by
  floats would put the second pair first, whatever their Chunk IDs.

### Prediction check

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

All five predictions held, and none was wrong. That is expected, not a
good guess: they were drafted after the prototype had run. The Step 4
command printed:

```text
k=60
  1. Chunk 2: 0.0312 = dense #4 +0.0156, lexical #4 +0.0156
  2. Chunk 1: 0.0164 = dense #1 +0.0164
  3. Chunk 21: 0.0164 = lexical #1 +0.0164
k=1
  1. Chunk 1: 0.5000 = dense #1 +0.5000
  2. Chunk 21: 0.5000 = lexical #1 +0.5000
  3. Chunk 2: 0.4000 = dense #4 +0.2000, lexical #4 +0.2000
```

| Prediction | What happened |
|---|---|
| k=60: Chunk 2 first; k=1: Chunk 1 first | Yes: 0.0312 against 0.0164 at k=60, and 0.5000 against 0.4000 at k=1 |
| First place in one list each: an exact tie, lower Chunk ID first | Yes: Chunks 1 and 21 both score 1/61 at k=60 and 1/2 at k=1, and Chunk 1 comes first |
| No lexical Candidates: the dense list in its own order | Yes: `test_a_missing_branch_adds_nothing` and `test_one_branch_alone_keeps_its_own_order` |
| A Chunk listed twice by one branch is refused | Yes: `test_a_chunk_one_branch_lists_twice_is_refused` raises `InvalidFusionInput` |
| Floats disagree on `1/63 + 1/140` and `1/84 + 1/90` | Yes: 0.023015873015873014 against 0.023015873015873017, though both are 29/1260 |

## Design

Changed files:

- `src/knowledge_service/retrieval/fusion.py` (new):
  - `DEFAULT_RRF_K = 60`
  - `RetrievalBranch` (`dense`, `lexical`), a `StrEnum` whose order is the
    order contributions are listed in
  - `FusionError` and `InvalidFusionInput`
  - `BranchContribution`: the branch, its rank there, and
    `1 / (k + rank)` as a float
  - `FusedCandidate`: the fused `rank`, the provenance fields, `score`,
    and `contributions`, with text fields out of repr; it meets the
    `Candidate` contract, so a fused list can be read like any branch's
  - `fuse_ranked_candidates(ranked, *, k=DEFAULT_RRF_K)`
- `tests/test_reciprocal_rank_fusion.py` (new): 51 unit tests, no
  database.
- `docs/architecture.md`: the `retrieval` row names
  `fuse_ranked_candidates`.

How it works:

1. It checks `k`, then that every key of `ranked` is a `RetrievalBranch`.
2. It walks the branches in enum order, not mapping order. For each branch
   it checks that the Candidates are a tuple or a list, that each has a
   `ChunkId`, that ranks are 1, 2, 3 in list order, and that no Chunk
   repeats.
3. Each Candidate adds `Fraction(1, k + rank)` to its Chunk. The first
   branch to find a Chunk supplies its provenance; a later branch must
   describe it identically, or fusion refuses.
4. It sorts by exact score, highest first, then by Chunk ID, numbers the
   result from 1, and rounds each score to a float once.

Why ranks and not scores: a cosine distance (lower is better) and a
`ts_rank` (higher is better, and dependent on the route) are on scales
that mean nothing to each other. Reciprocal Rank Fusion needs only
positions, so no calibration is needed between the branches. This is why
M05-T04 left both measures out of `Candidate`.

Why k = 60 by default: it is the constant of the original paper, which
found it worked well across collections. It stays an argument because
M05-T07 will measure it on this project's Questions. A small k makes a
top position dominate; a large k makes being found by both branches
dominate (Step 4).

Why exact fractions: two different pairs of ranks can give exactly equal
sums whose floats differ in the last digit. With k=60, `1/63 + 1/140` and
`1/84 + 1/90` are both 29/1260, but the floats are
0.023015873015873014 and 0.023015873015873017. Sorting by floats would
order such Chunks by rounding, not by the Chunk ID tie rule every branch
uses. The visible `score` is the exact sum rounded once, so adding the
rounded contributions can differ from it in the last digit.

Why refuse instead of repair: a Chunk one branch lists twice would get two
contributions from one search and rise unfairly, and a Chunk two branches
describe differently means one of them read different data. Either is a
broken branch, so fusion raises `InvalidFusionInput` instead of guessing.
A missing or empty branch is not an error: it adds nothing.

Scope left to M05-T06: running the branches, deadlines, what a failed
branch means, a limit on the fused list, and diagnostics. Fusion drops
each branch's own fit (distance, lexical score, highlights); M05-T06
decides which to carry along.

The adversarial review of the prototype found three problems, each fixed
with a test that fails on the unfixed code: the documented promise that
`score` equals the sum of the rounded contributions was false in the last
digit (the docs now say "exact sum rounded once", and a test checks
both); `None` branch values, `None` Candidates, and wrong Chunk ID types
crashed with untyped errors, and a `DocumentId` used as a Chunk ID let one
Chunk come back twice (all now `InvalidFusionInput`); and the rank error
printed the rank value, which leaked text a broken branch passed as a rank
(the message now names only the branch and the position, and the leak
test also checks notes and chained errors). Its suite-gap mutants, which
accepted float ranks, integral float `k`, text in exception notes, or text
in a chained cause, each fail the final suite.

## Verification

Commands, run one after another, with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL`
set for the database suites:

| Command | Result |
|---|---|
| `uv run pytest tests/test_reciprocal_rank_fusion.py -q` | 51 passed |
| `make format-check`, `make lint`, `make typecheck` | 174 files formatted; all checks passed; 0 errors |
| `make test-integration` | 284 passed, 1044 deselected |
| `make check` | 1325 passed, 3 deselected; coverage 95%, `fusion.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

No migration and no database change.

What the tests prove, at `fuse_ranked_candidates`:

| Spec item | Tests |
|---|---|
| Overlap | `test_a_chunk_both_branches_found_is_returned_once_with_both_parts`: Chunk 3 at dense 3 and lexical 1 comes back once, first, with both contributions and the exact sum |
| Disjoint lists | `test_disjoint_lists_interleave_and_equal_scores_go_by_chunk_id` |
| Missing branches | `test_a_missing_branch_adds_nothing` (absent and empty), `test_one_branch_alone_keeps_its_own_order`, `test_no_candidates_is_an_empty_result` |
| Ties | `test_disjoint_lists_interleave_and_equal_scores_go_by_chunk_id`, `test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ` (the highest-risk edge) |
| Duplicated identities | across branches: merged once (overlap test); within a branch: `test_a_chunk_one_branch_lists_twice_is_refused`; described differently: `test_a_chunk_two_branches_describe_differently_is_refused` (9 fields) |
| Configurable k | `test_k_decides_whether_one_top_rank_beats_two_middle_ranks`, `test_the_default_k_is_the_papers_60`, `test_a_bad_k_is_refused` |
| Transparent contributions | the overlap test, `test_the_order_of_branches_in_the_mapping_changes_nothing`, and the random test, which checks every contribution's branch and rank |
| Stable Chunk identity | `test_a_candidate_without_a_chunk_id_is_refused`, `test_a_fused_candidate_keeps_the_provenance_of_its_chunk` |
| Typed failure | `InvalidFusionInput` from every refusal test, including `test_candidates_that_are_not_a_tuple_or_a_list_are_refused` and `test_ranks_that_do_not_count_from_one_in_order_are_refused` |
| No sensitive text | `test_errors_and_reprs_never_show_document_text`: a planted marker never appears in a repr, an error message, its args or notes, and no error is chained |
| Contract | `test_a_fused_candidate_meets_the_candidate_contract` (pyright) |
| Order | `test_random_lists_fuse_by_exact_score_then_chunk_id`: 500 seeded cases, random k from 1 to 100; the result is exactly the union of the inputs, ranked 1 to n, ordered by exact score then Chunk ID, each score the exact sum rounded once |

Deliberate break, undone afterwards: with `part = 1 / (k + rank)` instead
of `Fraction(1, k + rank)`, two tests failed,
`test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ`
and `test_random_lists_fuse_by_exact_score_then_chunk_id`; 49 passed, and
pyright reported 2 errors. After restoring, the file matched its earlier
content byte for byte and 51 passed. Other mutations each failed at least
one test: no Chunk ID tie-break, no duplicate check, no provenance check,
contributions in mapping order, `k` ignored, no branch-type check,
unchecked ranks, `bool` accepted as `k`, and Document text in an error
message.

Security and privacy: fusion reads nothing from the database and takes
no reader. Its output is always exactly the Chunks the branches returned,
so it cannot add a Chunk the reader may not see; the permission check
stays in each branch's own SQL statement, before ranking and `LIMIT`.
Error messages name the branch and the position, never a value from the
input, and no error carries a cause or context. `FusedCandidate` keeps
text fields out of repr. Nothing is logged.

Reviews: the Standards review found nothing blocking. Ruff, the
formatter, pyright strict, and the 51 tests pass, and the module follows
the module layout of ADR 0013, the glossary, the error-class pattern of
the other retrieval modules, the `type(...) is not int` idiom, and
`repr=False` on text fields. Its non-blocking notes: `isinstance(...,
tuple | list)` accepts tuple and list subclasses where `filters.py` uses
exact types; ADR 0013's table still describes `retrieval` as vector search
only, which was already out of date before this task; one helper in the
random test builds `BranchContribution` objects only to compare their
branch and rank; and the comment on `DEFAULT_RRF_K` could cite the paper.
The Spec review found every element met, with nothing blocking and no
M05-T06 scope. It ran 18 mutations of `fusion.py`; all but one failed a
test. The survivor sorts by the float of the exact sum, which differs
only when two different exact sums round to the same float, as with a
huge `k`. Its non-blocking notes: the protection that matters is adding
in `Fraction`, while comparing as fractions matters only in that rare
case; contributions hold only the RRF part, not each branch's own
measure, which belongs to M05-T06 diagnostics; the fused list has no
limit or `truncated`, which M05-T06 must define; and the claim that
fusion never returns a Chunk no branch returned is tested only inside the
random test, so a small named test would make it easier to find.

Known limits:

- A provenance field whose own `==` raises would escape untyped; real
  Candidates hold only IDs, ints, and strings.
- With a huge `k`, such as 10**17, different exact scores can round to the
  same float; the shown scores then tie while the order follows the exact
  values.
- Fusion drops each branch's own fit: distance, lexical score, and
  highlights.
- Fusion has no limit of its own; its output is at most the sum of the
  branches' limits.
- RRF trusts positions only, so a Question one branch answers much better,
  such as an exact error code, can be pulled down by the other branch's
  order.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `fuse_ranked_candidates` hides.** A caller passes each branch's
   ranked Candidates and maybe `k`. It never thinks about matching the
   same Chunk across branches, adding points without rounding, breaking
   ties by Chunk ID, the order of contributions, a branch that found
   nothing, or checking that each branch kept its contract. It gets one
   ranked list it can read like any branch's, with the reason for every
   position.
2. **Floats instead of fractions.** The promise that equal scores go by
   Chunk ID breaks. A reader would almost never notice, because exactly
   equal sums from different ranks are rare and the two Chunks sit next
   to each other; the order would just depend on rounding.
   `test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ`
   and the random test catch it, and pyright flags the type.
3. **Merging a repeated Chunk.** If one search lists a Chunk twice and
   fusion keeps both entries, that Chunk gets two contributions from one
   search, as if two searches agreed. It would beat Chunks that were
   really found by both branches, and the repeat also pushes every
   following Chunk of that list one place down, lowering their points.
4. **Choosing k.** Run the M05-T07 bilingual set with several values, such
   as 1, 10, 30, 60, and 100, and compare recall@k and MRR of the fused
   list, and of each branch alone, separately for English, Ukrainian, and
   mixed Questions. Pick another k if it wins clearly on the slices that
   matter, without losing on the others.
5. **When RRF hurts.** For `E-1042`, lexical search finds the right Chunk
   first, while vector search may rank it 40th and put loosely related
   Chunks first. A Chunk at dense 2 and lexical 5 then beats the lexical
   winner, so the fused list is worse than lexical search alone. Measuring
   identifier Questions separately would show it.
