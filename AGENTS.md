# Owner maintenance boundary

The owner QQ maintenance assistant may inspect and modify source code, tests,
documentation, and scripts inside this repository. Keep changes modular,
tested, and committed on a dedicated branch before asking the owner to merge.

Never read, print, commit, or modify `.env`, credentials, tokens, runtime logs,
or generated evaluation data. Public AI portfolio state is also protected:
never edit `paper/state.json`, its audit trail, decisions, or orders directly.
Use the audited localhost public-AI capability APIs for those operations.

For structural work, preserve the public HTTP API and add or update tests. Do
not delete historical records or perform destructive cleanup without explicit
owner confirmation.

## QQ public-AI draft confirmation workflow

Public-AI decisions use a strict, auditable state machine:

1. Generate and persist a structured draft with a `draft_id`, action,
   observation, reasons, counter-evidence, invalidation conditions, confidence,
   evidence, and data cutoff.
2. Return that draft to the owner. Do not write the ledger or create orders.
3. Accept confirmation only when it names the pending `draft_id`, or when it is
   an unambiguous confirmation of the latest displayed draft for that QQ user.
4. Write the exact persisted draft through the local public-AI decision API,
   including the owner's confirmation text. Return the resulting `decision_id`
   and recorded timestamp.
5. Orders are a separate confirmed action and must reference that `decision_id`.

Never claim a draft was written without a successful API receipt. Never report
"service unavailable" unless the API call made in the current step actually
failed. If no pending draft exists, say so plainly and offer to create one.
