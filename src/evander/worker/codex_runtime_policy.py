"""Worker-owned authentication policy for brokered native Codex processes."""

CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/"

# Mounted read-only at /etc/codex for the lifetime of the job. Unlike ordinary
# config flags, native requirements also constrain resumed and child threads.
RUNTIME_REQUIREMENTS_TOML = f'''allowed_login_methods = ["chatgpt"]
cli_auth_credentials_store = "ephemeral"
chatgpt_base_url = "{CHATGPT_BASE_URL}"
model_provider = "openai"
'''

RUNTIME_REQUIRED_VALUES = {
    "allowedLoginMethods": ["chatgpt"],
    "cliAuthCredentialsStore": "ephemeral",
    "chatgptBaseUrl": CHATGPT_BASE_URL,
    "modelProvider": "openai",
}

RUNTIME_CONFIG_OVERRIDES = [
    'model_provider="openai"',
    'forced_login_method="chatgpt"',
    'cli_auth_credentials_store="ephemeral"',
    f'chatgpt_base_url="{CHATGPT_BASE_URL}"',
    'openai_base_url="https://api.openai.com/v1"',
    'experimental_thread_store={type="local"}',
]


def verify_runtime_requirements(result):
    requirements = result.get("requirements") or {}
    if any(requirements.get(key) != value for key, value in RUNTIME_REQUIRED_VALUES.items()):
        raise ValueError("Native Codex is missing the worker authentication policy")
