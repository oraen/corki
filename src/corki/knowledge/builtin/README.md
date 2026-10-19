# Built-in Corki knowledge

`corki_docs.md` contains the source-reviewed user and Agent operations manual.
Only the original document is distributed. Never include credentials, private conversations or incident logs.

Manager startup and Agent tool registration install the source in
`$CORKI_HOME/manager/knowledge/.system/corki_docs.md`. Installation never calls a model.
New or changed documents are disabled and pending. Enable in Manager to run the
normal `corki-text-C&E` skill with the configured default model; successful extraction enables the document.

Unchanged documents retain IDs, C&E indexes, in-flight processing and disabled state.
Disabling and re-enabling a ready document reuses its index without model calls.
Legacy `builtin-reviewed:` mechanical indexes are invalidated once. They are not rebuilt offline.

Source replacement is atomic and metadata invalidation is transactional. User documents
and settings are untouched. A changed document must be enabled again to regenerate its index.

Tests: `python -m pytest tests/unit/test_builtin_knowledge.py tests/integration/test_knowledge_runtime.py`
