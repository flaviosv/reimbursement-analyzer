from pathlib import Path

import pytest
import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
ENV_SAMPLE_PATH = REPO_ROOT / ".env.sample"

# Read and parsed once at import time, mirroring test_compose_parity.py's
# own precedent — neither file changes mid-session.
_SERVICES = yaml.safe_load(COMPOSE_PATH.read_text())["services"]
_ENV_SAMPLE_DOTENV = dotenv_values(ENV_SAMPLE_PATH)
_ENV_SAMPLE_VARS = set(_ENV_SAMPLE_DOTENV)

# Maintained allowlist: the only vars each service's environment: block may
# carry — Docker-network-topology values no shared .env file can correctly
# hold, since they differ between "running in the Docker network" and
# "running on the host" (AD-038).
_TOPOLOGY_ALLOWLIST = {
    "api": {"KAFKA_BOOTSTRAP_SERVERS", "DATABASE_URL"},
    "publisher": {"KAFKA_BOOTSTRAP_SERVERS", "DATABASE_URL"},
    "reimbursement": {"KAFKA_BOOTSTRAP_SERVERS", "DATABASE_URL", "LANGFUSE_HOST"},
}

# Maintained list of every var each service's own load_dotenv()/os.getenv()
# call reads that must instead come from .env.sample (never a compose
# environment: entry) — kept here, not derived from source, mirroring
# test_compose_parity.py's own hardcoded-constant style.
_REQUIRED_DOTENV_VARS = {
    "GROQ_API_KEY",
    "EXTRACT_FIELDS_MODEL_NAME",
    "ANALYSIS_MODEL_NAME",
    "AI_TIMEOUT_SECONDS",
    "EXTRACT_FIELDS_TEMPERATURE",
    "ANALYSIS_TEMPERATURE",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "KAFKA_SECURITY_PROTOCOL",
    "KAFKA_SASL_MECHANISM",
    "KAFKA_SASL_USERNAME",
    "KAFKA_SASL_PASSWORD",
    "KAFKA_SSL_CA_LOCATION",
}


class DescribeDotenvConfigParity:
    @pytest.mark.parametrize("service", ["api", "publisher", "reimbursement"])
    def it_keeps_service_environment_block_to_only_its_topology_allowlist(self, service: str) -> None:
        actual = set(_SERVICES[service]["environment"])
        unexpected = actual - _TOPOLOGY_ALLOWLIST[service]
        assert not unexpected, (
            f"{service}'s environment: block carries unexpected non-topology var(s): {unexpected}"
        )

    def it_defines_every_required_dotenv_var_in_env_sample(self) -> None:
        missing = _REQUIRED_DOTENV_VARS - _ENV_SAMPLE_VARS
        assert not missing, f".env.sample is missing var(s) the code reads: {missing}"

    def it_keeps_langfuse_public_key_in_sync_with_the_compose_anchor(self) -> None:
        compose_value = _SERVICES["langfuse-web"]["environment"]["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"]
        assert _ENV_SAMPLE_DOTENV["LANGFUSE_PUBLIC_KEY"] == compose_value, (
            ".env.sample's LANGFUSE_PUBLIC_KEY is out of sync with docker-compose.yml's "
            f"x-langfuse-public-key anchor ({compose_value!r})"
        )
