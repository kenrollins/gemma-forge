"""V2 memory layer — structured tips, per-retrieval outcome tracking,
similarity-based retrieval.

Kept separate from ``gemma_forge/harness/memory_store.py`` (which owns
V1 lessons/attempts/runs) so the V1 and V2 code paths can coexist
during the transition and either can be backed out cleanly. Phase E
of the V2 plan established the schema (``stig.tips``,
``stig.tip_retrievals``); Phase F (this package) writes tips; Phase G
reads them; Phase H curates them.
"""
