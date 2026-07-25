# Legacy manifests (pre-R12 naming)

These 23 manifests were written before `run_demo.py` named retained manifests by `run_id`.
Under the old scheme the filename carried the timestamp of the run that *triggered the
write*, not the run whose results the file contains — so a verifier indexing by filename
read the wrong result. Concretely, the file once named `manifest-20260724T033415.json`
contains `run_id=run-20260724T032049` with `segment_save_pp=5.0`, while the disclosed
committed run of that batch was `run-20260724T033415` at **+11.7pp**.

They are retained, not deleted: they are the full k=5 distribution behind the earlier
published estimate, and reading their CONTENTS reproduces the disclosed median of **+11.7pp**
and range **[+5.0, +18.3]** exactly. They have been **renamed here to match their internal
`run_id`**, so filename and content now agree. Nothing reads these files programmatically.

`run_demo.py` writes new manifests named by `run_id` directly into the parent directory.
Three files remain in the parent under their original timestamp names because they predate
the `run_id` field entirely and so have nothing to rename to.
