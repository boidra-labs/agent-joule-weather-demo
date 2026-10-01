"""Weather Agent — LangGraph StateGraph with a single 5-day forecast tool.

Template notes — to extend:
1. Add @tool functions under the --- Tools --- section and append them to TOOLS
2. Describe the new tools in SYSTEM_PROMPT
3. Add fields to WeatherAgentState if the graph needs extra state

Weather data: Open-Meteo (https://open-meteo.com) — free, no API key required.
Memory is optional — the agent works without it if configure_memory() returns None.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Annotated, Any, AsyncGenerator, Literal

import httpx
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_litellm import ChatLiteLLM
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from opentelemetry import trace
from typing_extensions import TypedDict

from app.telemetry import agent_requests, tool_calls, tool_duration

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

# AI Core model alias — `sap/` prefix is resolved via AICORE_* env vars
LLM_MODEL = f"sap/{os.environ.get('AICORE_MODEL', 'gpt-4.1')}"
AGENT_ID = "weather-agent"

DEFAULT_LOCATION = os.environ.get("DEFAULT_LOCATION", "Wroclaw, Poland")
FORECAST_DAYS = 5

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 10.0

SYSTEM_PROMPT = f"""You are Weather Assistant, a concise and friendly weather expert.

Tools:
- get_weather_forecast(location, days): daily forecast (up to {FORECAST_DAYS} days) for a city.

Rules:
1. For any weather question, call get_weather_forecast. Never invent weather data.
2. If the user does not name a location, use the default: {DEFAULT_LOCATION}.
3. Pass the location as "City, Country" when the country is known (e.g. "Wroclaw, Poland").
4. If the tool returns an error (e.g. location not found), explain briefly and ask the user
   to clarify the location.
5. Output format (Markdown):
   - One heading line: "5-day forecast for <resolved location>"
   - A table with columns: Date | Conditions | Min °C | Max °C | Precipitation (mm, % chance) | Max wind (km/h)
   - One or two sentences summarising the trend and any practical tip (umbrella, warm jacket, ...).
6. Answer in the language the user writes in.
"""

# WMO weather interpretation codes → human-readable text
WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _geocode(client: httpx.Client, location: str) -> dict[str, Any] | None:
    """Resolve "City, Country" to coordinates. Open-Meteo matches the city name only,
    so the country part is used to pick the best candidate."""
    city, _, country_hint = (p.strip() for p in location.partition(","))
    resp = client.get(
        GEOCODING_URL,
        params={"name": city, "count": 10, "language": "en", "format": "json"},
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    if country_hint:
        hint = country_hint.lower()
        for r in results:
            if hint in (r.get("country", "").lower(), r.get("country_code", "").lower()):
                return r
    return results[0]


@tool
def get_weather_forecast(location: str = DEFAULT_LOCATION, days: int = FORECAST_DAYS) -> dict[str, Any]:
    """Get the daily weather forecast (up to 5 days, starting today) for a location.

    Args:
        location: City and optional country, e.g. "Wroclaw, Poland". Defaults to Wroclaw, Poland.
        days: Number of days to forecast (1-5). Defaults to 5.
    """
    days = max(1, min(int(days), FORECAST_DAYS))
    start = time.perf_counter()
    outcome = "success"

    with tracer.start_as_current_span("tool.get_weather_forecast") as span:
        span.set_attribute("tool.name", "get_weather_forecast")
        span.set_attribute("weather.location.query", location[:200])
        span.set_attribute("weather.days", days)
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                with tracer.start_as_current_span("open_meteo.geocode"):
                    place = _geocode(client, location)
                if place is None:
                    outcome = "not_found"
                    span.set_attribute("outcome", outcome)
                    return {"error": f"Location '{location}' not found."}

                resolved = ", ".join(
                    p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p
                )
                span.set_attribute("weather.location.resolved", resolved)
                span.set_attribute("weather.latitude", place["latitude"])
                span.set_attribute("weather.longitude", place["longitude"])

                with tracer.start_as_current_span("open_meteo.forecast"):
                    resp = client.get(
                        FORECAST_URL,
                        params={
                            "latitude": place["latitude"],
                            "longitude": place["longitude"],
                            "daily": ",".join([
                                "weather_code",
                                "temperature_2m_min",
                                "temperature_2m_max",
                                "precipitation_sum",
                                "precipitation_probability_max",
                                "wind_speed_10m_max",
                            ]),
                            "timezone": "auto",
                            "forecast_days": days,
                        },
                    )
                    resp.raise_for_status()
                    daily = resp.json()["daily"]

            forecast = [
                {
                    "date": daily["time"][i],
                    "conditions": WMO_CODES.get(daily["weather_code"][i], "Unknown"),
                    "temp_min_c": daily["temperature_2m_min"][i],
                    "temp_max_c": daily["temperature_2m_max"][i],
                    "precipitation_mm": daily["precipitation_sum"][i],
                    "precipitation_probability_pct": daily["precipitation_probability_max"][i],
                    "wind_max_kmh": daily["wind_speed_10m_max"][i],
                }
                for i in range(len(daily["time"]))
            ]
            span.set_attribute("outcome", outcome)
            return {"location": resolved, "timezone": place.get("timezone"), "forecast": forecast}

        except Exception as exc:
            outcome = "error"
            logger.exception("get_weather_forecast failed")
            span.set_attribute("outcome", outcome)
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            return {"error": f"Weather service unavailable: {exc}"}
        finally:
            attrs = {"tool": "get_weather_forecast", "outcome": outcome}
            tool_calls.add(1, attrs)
            tool_duration.record((time.perf_counter() - start) * 1000, attrs)


TOOLS = [get_weather_forecast]


# ---------------------------------------------------------------------------
# Memory helpers — used when a hana-agent-memory service is bound
# ---------------------------------------------------------------------------

def _load_history(memory_client, context_id: str) -> list:
    """Load conversation history from Agent Memory as LangChain messages."""
    if not memory_client:
        return []
    try:
        from sap_cloud_sdk.agent_memory import MessageRole
        messages = memory_client.list_messages(
            agent_id=AGENT_ID,
            invoker_id=context_id,
            message_group=context_id,
            limit=20,
        )
        history = []
        for m in messages:
            if m.role == MessageRole.USER:
                history.append(HumanMessage(content=m.content))
            elif m.role == MessageRole.ASSISTANT:
                history.append(AIMessage(content=m.content))
        return history
    except Exception:
        logger.warning("Failed to load conversation history from memory")
        return []


def _persist_turn(memory_client, context_id: str, query: str, response: str) -> None:
    """Persist user query + agent response to Agent Memory."""
    if not memory_client:
        return
    try:
        from sap_cloud_sdk.agent_memory import MessageRole
        memory_client.add_message(
            agent_id=AGENT_ID, invoker_id=context_id,
            message_group=context_id, role=MessageRole.USER, content=query,
        )
        memory_client.add_message(
            agent_id=AGENT_ID, invoker_id=context_id,
            message_group=context_id, role=MessageRole.ASSISTANT, content=response,
        )
    except Exception:
        logger.warning("Failed to persist conversation turn to memory")


# ---------------------------------------------------------------------------
# Agent graph
# ---------------------------------------------------------------------------

class WeatherAgentState(TypedDict):
    """Graph state. `messages` is append-only via the add_messages reducer."""

    messages: Annotated[list[AnyMessage], add_messages]
    context_id: str


@dataclass
class AgentResponse:
    status: Literal["input_required", "completed", "error"]
    message: str


class WeatherAgent:
    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(self, memory_client=None) -> None:
        self.llm = ChatLiteLLM(model=LLM_MODEL).bind_tools(TOOLS)
        self.graph = self._build_graph()
        self.memory = memory_client

    def _build_graph(self):
        async def call_model(state: WeatherAgentState):
            with tracer.start_as_current_span("graph.node.model"):
                response = await self.llm.ainvoke(state["messages"])
            return {"messages": [response]}

        def route_after_model(state: WeatherAgentState) -> Literal["tools", "__end__"]:
            last = state["messages"][-1]
            return "tools" if getattr(last, "tool_calls", None) else END

        builder = StateGraph(WeatherAgentState)
        builder.add_node("model", call_model)
        builder.add_node("tools", ToolNode(TOOLS))
        builder.add_edge(START, "model")
        builder.add_conditional_edges("model", route_after_model)
        builder.add_edge("tools", "model")
        return builder.compile()

    def _initial_state(self, query: str, context_id: str) -> WeatherAgentState:
        history = _load_history(self.memory, context_id)
        return {
            "messages": [SystemMessage(content=SYSTEM_PROMPT), *history, HumanMessage(content=query)],
            "context_id": context_id,
        }

    async def stream(
        self, query: str, context_id: str, parent_context=None
    ) -> AsyncGenerator[dict, None]:
        ctx_kwargs = {"context": parent_context} if parent_context is not None else {}

        with tracer.start_as_current_span("agent.invoke", **ctx_kwargs) as span:
            span.set_attribute("a2a.context_id", context_id)
            span.set_attribute("gen_ai.request.model", LLM_MODEL)
            agent_requests.add(1, {"agent": AGENT_ID})

            yield {"is_task_complete": False, "require_user_input": False, "content": "Checking the forecast..."}

            try:
                result = await self.graph.ainvoke(self._initial_state(query, context_id))
                last = result["messages"][-1]
                response = last.content

                if getattr(last, "usage_metadata", None):
                    span.set_attribute("gen_ai.usage.input_tokens", last.usage_metadata.get("input_tokens", 0))
                    span.set_attribute("gen_ai.usage.output_tokens", last.usage_metadata.get("output_tokens", 0))
                    span.set_attribute("gen_ai.usage.total_tokens", last.usage_metadata.get("total_tokens", 0))

                _persist_turn(self.memory, context_id, query, response)
                yield {"is_task_complete": True, "require_user_input": False, "content": response}

            except Exception as e:
                logger.exception("Agent stream error")
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                yield {"is_task_complete": True, "require_user_input": False, "content": f"Error: {e}"}

    def invoke(self, query: str, context_id: str) -> AgentResponse:
        try:
            result = asyncio.run(self.graph.ainvoke(self._initial_state(query, context_id)))
            return AgentResponse(status="completed", message=result["messages"][-1].content)
        except Exception as e:
            logger.exception("Agent invoke error")
            return AgentResponse(status="error", message=f"Error: {e}")
