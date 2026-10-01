"""AI Core + telemetry bootstrap.

Both calls must run BEFORE LangChain / LiteLLM are imported anywhere else,
so the AI Core LiteLLM provider is registered and OpenTelemetry wraps httpx
at import time.
"""

from __future__ import annotations

import json
import logging
import os

from sap_cloud_sdk.agent_memory import create_client as _create_memory_client
from sap_cloud_sdk.aicore import set_aicore_config

logger = logging.getLogger(__name__)


def _populate_aicore_env_from_vcap() -> None:
    """Bridge VCAP_SERVICES aicore binding → AICORE_* env vars.

    set_aicore_config() reads from mounted files or AICORE_* env vars.
    On CF, secrets arrive via VCAP_SERVICES, not mounted files, so we
    pre-populate the standard env vars before calling set_aicore_config().
    Skipped if AICORE_CLIENT_ID is already set (local dev via .env).
    """
    if os.environ.get("AICORE_CLIENT_ID"):
        return

    vcap = json.loads(os.environ.get("VCAP_SERVICES", "{}"))
    bindings = vcap.get("aicore", [])
    if not bindings:
        logger.warning("No aicore binding found in VCAP_SERVICES")
        return

    creds = bindings[0]["credentials"]
    os.environ["AICORE_CLIENT_ID"] = creds["clientid"]
    os.environ["AICORE_CLIENT_SECRET"] = creds["clientsecret"]
    os.environ["AICORE_AUTH_URL"] = creds["url"]
    os.environ["AICORE_BASE_URL"] = creds["serviceurls"]["AI_API_URL"]
    os.environ.setdefault("AICORE_RESOURCE_GROUP", "default")
    logger.info("Populated AICORE_* env vars from VCAP_SERVICES")


def configure_aicore() -> None:
    """Configure AI Core credentials and register LiteLLM SAP provider."""
    logging.getLogger("sap_cloud_sdk.aicore").setLevel(logging.INFO)
    _populate_aicore_env_from_vcap()
    set_aicore_config()


def configure_memory():
    """Create Agent Memory client from VCAP_SERVICES hana-agent-memory binding or env vars."""
    vcap = json.loads(os.environ.get("VCAP_SERVICES", "{}"))
    bindings = vcap.get("hana-agent-memory", [])
    if bindings:
        creds = bindings[0]["credentials"]
        uaa = creds.get("uaa", {})
        os.environ.setdefault("CLOUD_SDK_CFG_HANA_AGENT_MEMORY_DEFAULT_URL", creds["url"])
        os.environ.setdefault(
            "CLOUD_SDK_CFG_HANA_AGENT_MEMORY_DEFAULT_UAA",
            json.dumps({
                "clientid": uaa["clientid"],
                "clientsecret": uaa["clientsecret"],
                "url": uaa["url"],
            }),
        )
    try:
        client = _create_memory_client()
        logger.info("Agent Memory client initialized")
        return client
    except Exception:
        logger.warning("Agent Memory not available — running without memory")
        return None


def configure_telemetry() -> None:
    """Initialize OpenTelemetry auto-instrumentation for LiteLLM, LangChain,
    Anthropic, OpenAI, and httpx. Reads OTLP endpoint from a bound
    cloud-logging service or OTEL_EXPORTER_OTLP_* env vars.
    Falls back to the plain OTel SDK if auto_instrument() fails."""
    from app.telemetry import init_telemetry

    init_telemetry()
