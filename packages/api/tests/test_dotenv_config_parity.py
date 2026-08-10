from pathlib import Path

import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
ENV_SAMPLE_PATH = REPO_ROOT / ".env.sample"

# Read and parsed once at import time, mirroring test_compose_parity.py's
# own precedent — neither file changes mid-session.
_SERVICES = yaml.safe_load(COMPOSE_PATH.read_text())["services"]
_ENV_SAMPLE_VARS = set(dotenv_values(ENV_SAMPLE_PATH))

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
    "LANGFUSE_SECRET_KEY",
    "KAFKA_SECURITY_PROTOCOL",
    "KAFKA_SASL_MECHANISM",
    "KAFKA_SASL_USERNAME",
    "KAFKA_SASL_PASSWORD",
    "KAFKA_SSL_CA_LOCATION",
}


class DescribeDotenvConfigParity:
    def it_keeps_apis_environment_block_to_only_its_topology_allowlist(self) -> None:
        actual = set(_SERVICES["api"]["environment"])
        unexpected = actual - _TOPOLOGY_ALLOWLIST["api"]
        assert not unexpected, f"api's environment: block carries unexpected non-topology var(s): {unexpected}"

    def it_keeps_publishers_environment_block_to_only_its_topology_allowlist(self) -> None:
        actual = set(_SERVICES["publisher"]["environment"])
        unexpected = actual - _TOPOLOGY_ALLOWLIST["publisher"]
        assert not unexpected, (
            f"publisher's environment: block carries unexpected non-topology var(s): {unexpected}"
        )

    def it_keeps_reimbursements_environment_block_to_only_its_topology_allowlist(self) -> None:
        actual = set(_SERVICES["reimbursement"]["environment"])
        unexpected = actual - _TOPOLOGY_ALLOWLIST["reimbursement"]
        assert not unexpected, (
            f"reimbursement's environment: block carries unexpected non-topology var(s): {unexpected}"
        )

    def it_defines_every_required_dotenv_var_in_env_sample(self) -> None:
        missing = _REQUIRED_DOTENV_VARS - _ENV_SAMPLE_VARS
        assert not missing, f".env.sample is missing var(s) the code reads: {missing}"
