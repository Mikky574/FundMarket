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
