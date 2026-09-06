# Controller runtime

Syndicate and NexAU run on the controller with Python 3.13.7. Harbor provisions
the task environment in E2B; the controller sends shell commands to that sandbox.
The sandbox contains task software, not the agent runtime or model credentials.

Install the pinned controller dependencies from the repository root:

```sh
uv venv --python 3.13.7
uv pip sync requirements.lock
uv pip install --no-deps --no-build-isolation -e .
.venv/bin/python -c 'from syndicate.runtime_contracts import installed_runtime; print(installed_runtime().model_dump_json())'
```

The offline identity check verifies Harbor 0.22.0, E2B 2.26.0, and NexAU 0.3.9
installed from commit `35ee1861546db3cb280a6e17e38a74060d7c96c3`. It does not
create a sandbox or call a model. Set `E2B_API_KEY` only in the controller
environment for live sandbox creation; never copy it into a task environment.

Task images and E2B templates belong to the benchmark environment setup. There
is no Syndicate agent installation layer to build into those images. The runtime
must stop agent-owned commands before verification and release the sandbox at
the end of the trial, including failure paths.
