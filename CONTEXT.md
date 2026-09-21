# Organizational Knowledge

This context describes how source-owned knowledge becomes permission-aware evidence for a user answer. It defines business language only; implementation choices live elsewhere.

## People and access

**User**:
A person who asks questions and whose identity determines which knowledge may be used.
_Avoid_: Account, requester, principal

**Group**:
A source or identity-provider grouping used when deciding whether a User may read a Document.
_Avoid_: Role, team

**Application Role**:
A permission to operate the knowledge service, independent of permission to read source content.
_Avoid_: Group, document permission

**Access Grant**:
A normalized statement that a User or Group may read a Document.
_Avoid_: ACL entry, role

**Authorization Context**:
The verified User identity and Groups carried through one request or workflow.
_Avoid_: User metadata, auth payload

## Knowledge lifecycle

**Source**:
An independently configured origin of organizational content, such as a directory or Confluence site.
_Avoid_: Provider, database

**Source Record**:
A versioned item returned by a Source before its content and access rules are normalized.
_Avoid_: Raw document, page

**Document**:
The latest normalized, searchable representation of one stable source item.
_Avoid_: File, page, record

**Document Version**:
The source version and content fingerprint observed for a Document during synchronization.
_Avoid_: Revision, snapshot

**Content Version**:
The independently tracked version of a Document's text and structure.
_Avoid_: ACL version

**Authorization Version**:
The independently tracked version of a Document's Access Grants.
_Avoid_: Content version

**Chunk**:
A provenance-preserving section of a Document that may be retrieved as evidence.
_Avoid_: Passage, segment

**Synchronization Run**:
An auditable attempt to reconcile one Source with the searchable knowledge state.
_Avoid_: Import, crawl, ingestion job

**Synchronization Checkpoint**:
A source-specific cursor from which a later Synchronization Run can safely resume.
_Avoid_: Offset, page token

**Tombstone**:
A durable statement that a formerly available Document must no longer be retrieved.
_Avoid_: Soft delete, missing record

## Questions and evidence

**Question**:
The User's current request for organizational knowledge, interpreted with bounded Conversation context.
_Avoid_: Prompt, query

**Conversation**:
A bounded sequence of User questions and generated Answers with a retention policy.
_Avoid_: Chat, session

**Candidate**:
A permission-filtered Chunk considered by retrieval before final evidence selection.
_Avoid_: Result, hit

**Evidence**:
A selected Chunk supplied to answer generation with stable provenance.
_Avoid_: Context, source

**Answer**:
A response supported by Evidence, or an explicit abstention when Evidence is inadequate.
_Avoid_: Completion, output

**Citation**:
A validated reference from an Answer claim to Evidence and its originating Document.
_Avoid_: Link, source

**Abstention**:
An Answer that explicitly states available Evidence cannot support a reliable response.
_Avoid_: Refusal, failure

## Evaluation

**Evaluation Case**:
A versioned Question, Authorization Context, expected evidence, answerability label, and scoring metadata.
_Avoid_: Test question, benchmark item

**Evaluation Run**:
A reproducible execution of a system configuration over an Evaluation Dataset.
_Avoid_: Benchmark, test run

**Evaluation Dataset**:
A versioned collection of verified Evaluation Cases with documented lineage and splits.
_Avoid_: Golden set, test data

**Feedback Signal**:
A User-provided positive or negative rating that requires review before any evaluation or training use.
_Avoid_: Label, ground truth
