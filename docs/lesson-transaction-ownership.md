# Lesson transaction ownership

`save_lesson` keeps its existing default: governed proposal routing, inline
embedding/wiring for automatic creates, then commit. Existing callers are not
migrated by this change.

Internal callers may explicitly pass `commit=False` to retain ownership of the
database transaction. The near-duplicate queue, rejected duplicate and ordinary
auto/queue branches flush rather than commit. Authority validation, evidence
frames, Assistant-scope escalation and the Action Bus remain in the path.
The option requires an actual boolean and is validated before staging writes,
including under optimized Python.

The staged mode deliberately does not run embedding/wiring or publish staged
embedding data to the semantic cache. A created-neuron result reports
`enrichment_pending=True`; ordinary review-queue results report false, while
near-duplicate queue/skip results retain their existing no-neuron shape. Callers
must arrange post-commit enrichment and failure recovery separately. Staging is
not behaviorally equivalent to a fully enriched committed save.

This is a preparatory persistence boundary, not an atomic distillation or
checkpoint implementation. Distillation still uses the default save behavior.
Do not opt it into staged mode until post-commit enrichment, attribution writes,
checkpoint persistence and rollback/recovery are composed and tested together.
The existing default's pre-commit cache publication is not repaired here.

No new schema, idempotency guarantee, personal backfill, outside-key permission
or multi-worker guarantee is introduced. Existing committed work cannot be
undone by a later rollback.
