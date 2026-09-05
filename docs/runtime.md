# P08a sandbox runtime

Build the installation layer from the original task main image, then retain its
immutable image ID for trials (the mutable local tag is only a build handle):

```sh
docker build --build-arg TASK_IMAGE=<original-image> -f runtime/Dockerfile -t syndicate-runtime .
docker image inspect syndicate-runtime --format '{{.Id}}'
```

The allowlisted build context contains package source and dependency pins, never
benchmark seeds/tests/solutions. Installed NexAU Git provenance and both runtime
versions are checked without model calls. OS packages are resolved at build time;
reuse the recorded resulting image ID for both arms, rather than rebuilding it.

The image creates dedicated UID 10001. P07 owns the concrete shell backend;
P08b will enforce no-new-privileges and whole-UID cleanup before verification.
No-model installation checks must label every container `ao.session=$AO_SESSION_ID`.
