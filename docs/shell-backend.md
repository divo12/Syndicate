# In-container shell backend — M1-T1 / P07b

`ContainerShell(work_dir, capture_limit_bytes=8_000_000)` implements P07's
`SandboxShell` protocol. Construct it only in the task container as the dedicated
nonroot execution UID. It refuses root/host use; there is no host fallback mode.
Initial cwd must resolve inside the workspace. Directory descriptors opened
without following symlinks prevent a background rename/symlink race at spawn.
This constrains the requested cwd, not arbitrary shell filesystem access;
protected files must be absent/unreadable through the outer sandbox policy.

Each command starts its own process group with anonymous Linux `memfd` capture
buffers, never named output files or durable trace copies. Foreground receipts
retain raw text in memory and immediately release capture descriptors; close and
cancellation release background buffers. Receipts expose no capture-file paths.
Background jobs retain a deadline after returning their PID; close is idempotent
and prohibits further execution. The runner sends transient typed facts directly
to Neatlogs. There is no local outbox, export fallback or post-close file recovery.

The Harbor adapter **must stop and confirm the entire dedicated UID** before
verifier injection, including descendants that used `setsid` to escape groups.
Backend group cleanup alone is not that security boundary. The UID stop also
handles abrupt runtime death before backend cleanup can execute.

Compatibility changes: a hard inherited Linux file-size limit bounds each
regular file written by commands (including capture files); reaching the capture
limit marks output incomplete. Record the configured limit in runtime baseline
settings. Foreground completion also kills leftover group members; asynchronous
work must use the explicit background request. No live output callback is added.
Raw negative return codes retain signal facts; timeout remains a distinct status.
Background receipt is an incomplete startup snapshot; later buffers are discarded
on close. Missing evidence remains incomplete, never reconstructed from local files.
