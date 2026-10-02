# Weather Agent — A2A + LangGraph + SAP Joule (template)

Skeleton A2A agent with a single tool: **5-day weather forecast** for a city
(default **Wroclaw, Poland**). Built to be copied as a template for new agents.

```
SAP Joule ──A2A v0.3──▶ weather-agent ──LiteLLM──▶ SAP AI Core (gpt-4.1)
 (BTP Destination        │
  WEATHER_AGENT)         ├─▶ Open-Meteo API (geocoding + forecast, no key)
                         └─▶ OpenTelemetry → OTLP backend / console
```

| Layer | Choice |
|---|---|
| Agent | LangGraph `StateGraph` (`WeatherAgentState`) + `ToolNode` |
| LLM | LiteLLM `sap/<model>` → SAP AI Core |
| Protocol | `a2a-sdk` latest, `enable_v0_3_compat=True` (Joule speaks v0.3) |
| Auth | XSUAA JWT middleware |
| Telemetry | `sap-cloud-sdk` `auto_instrument()` + custom spans/metrics, plain OTel SDK fallback |

## Layout

```
app/
  main.py            A2A server, agent card, routes
  agent.py           LangGraph state + graph + get_weather_forecast tool  ← edit here
  agent_executor.py  A2A ↔ agent bridge
  bootstrap.py       VCAP_SERVICES → AICORE_* env, memory, telemetry init
  telemetry.py       OTel setup + shared metrics
  auth.py            XSUAA JWT middleware
joule/               Joule BYOA capability (DTA schema 3.27.0)
  da.sapdas.yaml
  a2a/capability.sapdas.yaml        namespace JOULE.EXT, system alias WEATHER_AGENT
  a2a/capability_context.yaml
  a2a/functions/weather_agent.yaml
  a2a/scenarios/weather_forecast/weather_forecast.yaml
manifest.yml         Cloud Foundry descriptor (Docker image from GHCR)
xs-security.json     XSUAA config
```

## Run locally

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # fill AICORE_* values
python -m app.main --port 9000
```

```bash
curl http://localhost:9000/.well-known/agent-card.json
# A2A v0.3 (what Joule sends)
curl -X POST http://localhost:9000/ -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"message":{"role":"user","messageId":"m1","parts":[{"kind":"text","text":"Weather in Wroclaw for the next 5 days?"}]}}}'
```

`AUTH_DISABLED=true` in `.env` skips XSUAA locally — never set it on CF.

## Telemetry

| Setting | Effect |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` (+ `_HEADERS`) | Full export via `auto_instrument()` (LiteLLM, LangChain, httpx) |
| `OTEL_CONSOLE_EXPORTER=true` (no endpoint) | Spans printed to stdout — good for demos |
| neither | Tracing off |

Custom spans: `agent.invoke` (linked to caller `traceparent`), `graph.node.model`,
`tool.get_weather_forecast` → `open_meteo.geocode` / `open_meteo.forecast`.
Metrics: `agent.requests`, `agent.tool.calls`, `agent.tool.duration`.

## Deploy with MTA (recommended)

`mta.yaml` creates everything in one step: SAP AI Core, XSUAA (+ service key), the Destination
service, the app from the GHCR image, and the subaccount destination **`WEATHER_AGENT`** that Joule
calls (OAuth2ClientCredentials from the XSUAA key, URL = the app's route).

```bash
cp weather-agent.mtaext.example weather-agent.mtaext   # fill in the GHCR token (+ OTel key); git-ignored
mbt build -t mta_archives
cf login -a <cf-api-url> --sso && cf target -o <org> -s <space>
cf deploy mta_archives/weather-agent_0.1.0.mtar -e weather-agent.mtaext
```

The route is `<org>-<space>-weather-agent.<domain>` and the XSUAA app is `weather-agent-<space>`, so
several spaces can host it side by side. To deploy a new image, change the tag in the `.mtaext`.
Remove everything with `cf undeploy weather-agent --delete-services`.

## Deploy to Cloud Foundry (manual)

```bash
docker build -t ghcr.io/boidra-labs/weather-agent:0.1.0 .
docker push ghcr.io/boidra-labs/weather-agent:0.1.0
# or: bash scripts/create-github-repo.sh   (creates private repo + image under boidra-labs)

cf login -a <cf-api-url> --sso
cf target -o <org> -s <space>
cf create-service aicore extended weather-agent-aicore
cf create-service xsuaa application weather-agent-xsuaa -c xs-security.json
cf push -f manifest.yml --docker-username <github-username> --no-start
cf set-env weather-agent CF_DOCKER_PASSWORD <ghcr-pat>
cf set-env weather-agent OTEL_EXPORTER_OTLP_HEADERS "x-honeycomb-team=<KEY>"
cf start weather-agent
```

Update the `route` / `AGENT_PUBLIC_URL` in `manifest.yml` to match your CF domain.

## Wire to SAP Joule (BYOA)

1. BTP Destination named **`WEATHER_AGENT`** (case-sensitive) → URL of the deployed agent,
   authentication carrying an XSUAA token for `weather-agent-xsuaa` (e.g. OAuth2ClientCredentials).
2. Joule subscription + `das-service-canary` (plan `designer`) instance + service key;
   roles `capability_developer`, `capability_release_admin`, `end_user`.
3. Deploy:

```bash
npm install -g @sap/joule-cli
joule login -a <authurl> --sso-passcode
joule deploy --compile ./joule
joule launch weather_agent
```

Names that must stay in sync: `da.sapdas.yaml name` = `capability metadata.name` = `weather_agent`;
`system_aliases` key = function `system_alias` = Destination name = `WEATHER_AGENT`.

## Using as a template

1. Rename `weather-agent` / `weather_agent` / `WEATHER_AGENT` across the repo.
2. Replace `get_weather_forecast` in `app/agent.py`, update `TOOLS` and `SYSTEM_PROMPT`.
3. Update the agent card skill in `app/main.py` and descriptions in `joule/`.
