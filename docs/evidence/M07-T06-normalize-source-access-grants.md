# M07-T06 — Normalize source Access Grants evidence

## Invariant

A Document's Access Grants name only everyone, for an item the Source marks
public, or the users and groups its principals resolve to through the
Source's `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization
Context uses. A principal that is malformed or unmapped grants nothing and is
reported; the result does not depend on the principals' order or repetition.
A record that fails to normalize keeps its last good version only for readers
both its old and new access name. No principal or mapping text reaches a log
or a repr.

## Expected failing check

Before the change, `uv run pytest tests/test_grant_normalization.py -q` fails
with `ModuleNotFoundError: No module named 'knowledge_service.ingestion.grant_normalization'`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Which readers a file gets when the Source names `group:engineering`, `group:contractors`, and `hr`, and the directory maps only `engineering` to `/engineering`; and what the summary reports: one reader, the Group `group_id_for(issuer, "/engineering")`; the summary lists the file once in `unresolved`, with `group:contractors` as `unknown` and `hr` as `malformed`, and the log says only `unresolved source principals: 2 in 1 documents`.
- Whether alice (in `/engineering`) and bob (in `/finance`) can retrieve that file after the pass: alice can, because her token's `/engineering` gives the same GroupId as the grant; bob cannot, because no grant names his Group.
- What alice can read if the file then stops parsing and the Source names only `user:carol@example.test`: nothing from that file: `narrow_access` keeps only readers both the old access (engineering) and the new one (carol) name, which is nobody, so alice loses it at once and carol waits until the file parses again; the summary counts it as `Change.ACCESS`.
- Which tests fail if the call to `narrow_access` is removed from synchronization: 3 in `tests/test_synchronization_integration.py`: both cases of `test_a_reader_removed_while_the_file_is_broken_loses_it_at_once` and `test_access_narrowed_while_broken_is_restored_by_a_good_version`.

## Design

- **Principals.** A Source names readers as `user:<id>` or `group:<id>`; the kind comes first so a user and a group with one name never mix. Anything else is `malformed`. IDs are matched exactly, with no case folding, trimming, or Unicode normalization.
- **Directory.** `PrincipalDirectory(issuer, users, groups)` in `src/knowledge_service/ingestion/grant_normalization.py` maps a Source user ID to the provider's `sub` and a Source group ID to a full group path. It accepts only exact `str`, keeps a private read-only copy, refuses any `sub` or path a token could not carry (through `visible_name` and `plain_group_path`, made public in `identity/context.py`), and leaves its mappings out of the repr.
- **Shared IDs.** A mapped principal becomes `Subject.user(user_id_for(issuer, sub))` or `Subject.group(group_id_for(issuer, path))`, the functions the Authorization Context uses for tokens, so a Source group mapped to `/engineering` is the Group alice's token carries.
- **Fail closed.** `normalize_access` returns `NormalizedAccess(readers, unresolved)`: public stays public; mapped principals become distinct, sorted readers; everything else is an `UnresolvedPrincipal` (`malformed` or `unknown`) that grants nothing. The Document still publishes for the readers that resolved.
- **Synchronization.** `synchronize_source` requires `principal_directory` (None means only public items are readable), reports `UnresolvedAccess` per file in the summary, last report winning, and logs only counts. `source_readers` is gone.
- **Broken files.** For a record that fails to normalize, `narrow_access` keeps its last good version only for readers both its old and new access name (everyone counts as any reader), and records that in the authorization fingerprint so the next good version is compared against what is really stored.

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_grant_normalization.py -q` failed with `ModuleNotFoundError: No module named 'knowledge_service.ingestion.grant_normalization'`. After Step 4, `tests/test_deletions.py` failed to import the removed `source_readers` until Step 5.
- `uv run pytest tests/test_authorization_context.py -q`: 89 passed after Step 2, unchanged.
- `uv run pytest tests/test_grant_normalization.py tests/test_deletions.py tests/test_authorization_context.py -q`: 137 passed, including the two Hypothesis properties (order independence, and removing every unresolved principal changes no reader).
- `uv run pytest -m integration tests/test_synchronization_integration.py tests/test_document_title_integration.py -q`: 18 passed against PostgreSQL. Mapped principals become exactly the engineering and carol grants, the rest are reported; through `eligible_chunks_predicate` alice retrieves the file and bob does not.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2733 passed (+46); `make test-integration` 427 passed (+8); `make docs-check` passed.
- Deliberate break: without the `narrow_access` call in `synchronize_source`, 3 failed, 12 passed in `tests/test_synchronization_integration.py`: both cases of `test_a_reader_removed_while_the_file_is_broken_loses_it_at_once` (a removed group, and anyone after the file was made private, still read the last good version) and `test_access_narrowed_while_broken_is_restored_by_a_good_version`. Ruff flagged the unused import. Restored: 15 passed. The prediction was right.
- Known limits: no stored Source configuration holds a directory yet, so callers pass it by hand (Source administration in M09-T02); `DirectorySource` gives every file in a folder the same access and ignores filesystem permissions; Confluence's site, space, and page permissions are combined in M08-T14; the unresolved count is logged only when a pass completes.

## Review

An independent adversarial review tried lookalike and malformed principals (case variants, Cyrillic and fullwidth letters, NFD, zero-width and control characters, colons inside IDs), user and group crossovers, hostile directories (non-mappings, `str` subclasses, later mutation), 3,000 Hypothesis cases against its own reference model, sync passes that remap, revoke, restore, and repeat records, and 41 code mutations. The normalizer never widened access. It found these problems, all fixed with tests that failed before the fix:

- A record quarantined in the same pass as its access changed kept its old grants: a removed group, or anyone after a public file was made private, still read the last good version. Now `narrow_access` keeps only readers both old and new access name (`test_a_reader_removed_while_the_file_is_broken_loses_it_at_once`, `test_access_narrowed_while_broken_is_restored_by_a_good_version`).
- The directory accepted paths and subjects no token can carry (`/a~/b`, a zero-width space, NUL, over 255 characters), giving grants that match nobody. Now it uses the token's own rules (`test_a_directory_with_a_partial_or_padded_name_is_refused`, six new cases).
- The directory's repr printed every mapping. Now they are left out (`test_the_directory_repr_names_no_one`).
- A `str` subclass key or value could compare, hash, or format as another name. Now only exact `str` is accepted (two new cases of the directory test).
- `principal_directory` defaulted to None, so one forgotten argument hid every private Document. Now every caller passes it.
- A record offered twice in one pass could be reported with access it no longer had. Now the last report wins (`test_a_record_offered_twice_is_reported_as_last_seen`).
- Three mutations survived: splitting at the last colon, trimming a trailing slash from paths, and a sort key without the ID (`test_an_id_with_a_colon_is_matched_whole`, `test_a_path_with_a_trailing_slash_is_another_group`, `test_readers_come_back_in_one_order_within_a_kind`).

Documented, not fixed: the unresolved count is lost if a pass fails part-way, because it is logged at the end.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **The token's own IDs and name rules.** A second ID scheme for Source principals would need a table and could drift from the tokens. Using `user_id_for` and `group_id_for` makes a Source group and a token group equal by construction, and refusing names a token cannot carry means no grant ever points at nobody without anyone noticing.
- **Unresolved principals do not block publication.** Leaving one unmapped name out only narrows who reads the Document, which fails closed, while refusing the whole Document would hide it from everyone the Source did name. The summary reports the gap so it can be mapped.
- **Broken files only shrink.** The stored text is the old version; showing it to readers the Source just added would give them text that was never meant for them. Removing readers at once and adding them only with a version that parses never shows anyone more than both versions allowed.
- **Left for later.** A stored directory per Source (M09-T02), Confluence permissions (M08-T14), and the reader in every vector and lexical query (M07-T07, M07-T08).

**Falsified assumption.** I assumed that a quarantined record is safe to leave untouched because its last good version was already authorized. The review shows that its access can change in the same pass: a group the Source removed kept reading the old text, and a public file made private stayed public. So "keeping the last good version keeps authorization correct" is wrong; the access has to be narrowed even when the content cannot be updated.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It no longer knows the `user:`/`group:` format, which names map to which Keycloak identities, how IDs are derived, or what counts as malformed; it passes a `SourceAccess` and a directory and gets readers and a report back.
2. A directory change can alter a file's readers while its content stays the same, and so can a Source changing access on an unchanged file; skipping grants for unchanged content would keep removed readers. `test_mapping_a_principal_later_opens_the_document_on_the_next_pass` shows access changing with unchanged content, and the reconciliation tests on access-only changes would also fail.
3. `group:Engineering` is `unknown`: it grants nothing, the summary lists it for that file, and the log counts it. Matching without case would make `Engineering` and `engineering` one group, and in a Source where they are different groups that would let one group's members read the other's files.
4. A file is for engineering only; it breaks, and the Source makes it public because a cleaned version will be published. Applied fully, everyone would read the old engineering-only text, which was never meant for them.
5. It gets two readers: carol's UserId and the `/finance` GroupId (if `finance` is mapped). carol and bob, who is in `/finance`, can retrieve it; alice cannot.
