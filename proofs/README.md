# Accepted proofs

One pair of files per proved step: `<problem>/<step>.lean` is the Lean text exactly as the model sent it, and `<problem>/<step>.json` records which run and attempt produced it, the model that answered, the toolchain, the axioms it depends on and the fingerprint of the statement it proves.

Files arrive here only from the `Prove` workflow, after all five gate stages. Being here is not a permanent honour: `ztproof recheck` runs the whole gate again on every file, in CI, on every push. A proof whose pinned statement has since changed is reported as stale.

It is empty until the first accepted run. We would rather show an empty shelf than a decorated one.
