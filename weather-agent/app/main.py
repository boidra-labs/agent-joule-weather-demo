# Load .env before anything else so AICORE_* vars are available to bootstrap
from dotenv import load_dotenv
load_dotenv()

# CRITICAL: Initialize telemetry and AI Core BEFORE importing AI frameworks.
# OpenTelemetry must wrap httpx/LangChain at import time, otherwise LLM calls
# are not traced. AI Core env vars must be set before LiteLLM resolves the
# `sap/<model>` provider mapping.
from app.bootstrap import configure_aicore, configure_telemetry  # noqa: E402
configure_aicore()
configure_telemetry()

import logging  # noqa: E402
import os  # noqa: E402

import click  # noqa: E402
import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import (  # noqa: E402
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from a2a.types.a2a_pb2 import (  # noqa: E402
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

from app.agent_executor import WeatherAgentExecutor  # noqa: E402
from app.auth import XSUAAAuthMiddleware  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9000"))


def build_app(host: str = HOST, port: int = PORT) -> Starlette:
    public_url = os.environ.get("AGENT_PUBLIC_URL", f"http://{host}:{port}/")

    skill = AgentSkill(
        id="get_weather_forecast",
        name="5-day weather forecast",
        description="Returns the daily weather forecast (up to 5 days) for a city. Defaults to Wroclaw, Poland.",
        tags=["weather", "forecast", "demo"],
        examples=[
            "What is the weather in Wroclaw for the next 5 days?",
            "Will it rain in Krakow this week?",
            "Give me a 3-day forecast for Berlin, Germany",
        ],
    )
    agent_card = AgentCard(
        name="weather_agent",
        description="Weather Assistant: 5-day weather forecasts for any city (default: Wroclaw, Poland).",
        supported_interfaces=[AgentInterface(url=public_url)],
        version="0.1.0",
        default_input_modes=["text", "text/plain"],
        default_output_modes=["text", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )

    task_store = InMemoryTaskStore()
    executor = WeatherAgentExecutor()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
    )

    routes = [
        *create_agent_card_routes(agent_card),
        *create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True),
    ]
    app = Starlette(routes=routes)
    # Local dev only: AUTH_DISABLED=true skips XSUAA (no VCAP_SERVICES binding locally).
    # Never set this on CF.
    if os.environ.get("AUTH_DISABLED", "").lower() == "true":
        logger.warning("XSUAA auth DISABLED (AUTH_DISABLED=true) — local development only")
    else:
        app.add_middleware(XSUAAAuthMiddleware)
    return app


@click.command()
@click.option("--host", default=HOST)
@click.option("--port", default=PORT, type=int)
def main(host: str, port: int) -> None:
    app = build_app(host, port)
    logger.info("Starting A2A server at http://%s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
