# Model configuration — M0-T2-S1 / P02

`load_model_config(Path(...))` reads an explicit UTF-8 env file containing
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_BASE_URL`, and `AZURE_OPENAI_DEPLOYMENT`.
It never reads ambient credentials, mutates the environment, or contacts a provider.
Shell quotes, optional `export`, and comments are supported; interpolation,
duplicate selected keys, malformed values, and missing credentials fail closed.
Unrelated variables are ignored. Errors omit source values and file paths.

`ModelConfig` and nested `ModelSettings` are frozen Pydantic objects. The key is
`SecretStr`, omitted from repr and serialization; explicit `get_secret_value()`
is reserved for the eventual provider adapter. `canonical_json()` and
`settings_hash` belong to the nonsecret settings and exclude credentials.

D-14 / BASE-03: Azure OpenAI, Responses, and `gpt-5.4-mini` are the only accepted
provider/API/model choices. Every product role shares this configuration.
Unknown fields, deployment aliases and model mismatches are rejected. Endpoint
identity is the exact supplied HTTPS URL without userinfo, query or fragment.
Different endpoint spellings deliberately produce different hashes.

This is offline configuration validation, not proof of deployment mapping.
OPEN-01 / M1 must verify the actual returned model, API/tool compatibility,
supported request settings and limits before any measured run or H0 freeze.
No sampling settings, limits, alias verification or fallback are invented here.
