# Team context offline comparison

Baseline: `243aed9fcda2f558d2eeea49992dba5481b71595`.
Fixture SHA-256: `faa8fcf404b6c7ef35d660092e486aa3fd2ef3ddee5d1940d6553c398c365e6b`.

Synthetic sanitized examples; no historical token replay or billing data.

| Fixture | Context | Wake bytes before → after | Wake reduction | Full → summary read bytes | With selected reads | Read reduction | All detail with summary |
|---|---|---:|---:|---:|---:|---:|---:|
| coding | fresh | 20,863 → 10,865 | 47.92% | 17,110 → 8,070 | 14,012 | 18.11% | 23,182 |
| coding | retained | 19,923 → 10,379 | 47.9% | 17,110 → 8,070 | 14,012 | 18.11% | 23,182 |
| independent-review | fresh | 28,127 → 12,449 | 55.74% | 17,289 → 8,877 | 14,828 | 14.23% | 24,495 |
| independent-review | retained | 27,181 → 11,963 | 55.99% | 17,289 → 8,877 | 14,828 | 14.23% | 24,495 |
| Truman-management | fresh | 29,360 → 17,090 | 41.79% | 20,968 → 10,641 | 16,586 | 20.9% | 28,826 |
| Truman-management | retained | 27,837 → 16,604 | 40.35% | 20,968 → 10,641 | 16,586 | 20.9% | 28,826 |
| Trudy-coordination | fresh | 26,030 → 15,511 | 40.41% | 19,032 → 9,338 | 15,280 | 19.71% | 25,990 |
| Trudy-coordination | retained | 24,508 → 15,025 | 38.69% | 19,032 → 9,338 | 15,280 | 19.71% | 25,990 |

Each targeted scenario adds two calls to recover the complete selected task and evidence record. This measures starting a selected item, not completing all scoped work. The JSON report records each retrieval and duplicate-content metric.

Managed guidance: 19,728 → 27,765 UTF-8 bytes. This increase is separate from the wake measurements; its actual loading frequency matters.

The following sensitivity calculation includes a full copy of managed guidance once per wake. It is an assumption about loading frequency, not a measurement of model history or caching.

| Fixture | Context | Wake + guidance bytes before → after | Duplicate wake string bytes before → after | Duplicate read string bytes before → after | All-detail extra calls |
|---|---|---:|---:|---:|---:|
| coding | fresh | 40,591 → 38,630 | 3,160 → 440 | 5,620 → 440 | 7 |
| coding | retained | 39,651 → 38,144 | 3,160 → 440 | 5,620 → 440 | 7 |
| independent-review | fresh | 47,858 → 40,217 | 3,751 → 1,271 | 5,856 → 1,271 | 9 |
| independent-review | retained | 46,912 → 39,731 | 3,751 → 1,271 | 5,856 → 1,271 | 9 |
| Truman-management | fresh | 49,089 → 44,856 | 3,390 → 1,270 | 6,875 → 1,040 | 9 |
| Truman-management | retained | 47,566 → 44,370 | 3,390 → 1,270 | 6,875 → 1,040 | 9 |
| Trudy-coordination | fresh | 45,758 → 43,276 | 3,390 → 966 | 6,260 → 736 | 8 |
| Trudy-coordination | retained | 44,236 → 42,790 | 3,390 → 966 | 6,260 → 736 | 8 |

- Wake sizes include the ordinary provider prompt; they exclude MCP schemas, system prompts and managed guidance loading.
- Reads are compact serialized fixture records, excluding tool envelopes and request overhead. Full mode remains the baseline contract.
- Targeted reads select task-0 and evidence-0 only, not completion of the entire dispatch; selected reads cost two extra calls versus full.
- All-detail column retrieves every scoped task, inbox, blocker, review, project and evidence record; this exposes worst-case rehydration overhead.
- Duplicate content counts repeated leaf-string bytes of at least 40 characters, excluding JSON keys, with each duplicate counted once.
- Managed guidance bytes are reported separately; loading them, rebuilding contexts, retries, and rework must be measured in a live pilot.
- 50% wake and 35% targeted-read reductions are ambitious measurement targets, not functional acceptance gates or billing estimates.
