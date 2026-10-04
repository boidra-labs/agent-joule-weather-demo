# Joule A2A — Preparation & Automation Guide

How the `joule/` folder (SAP Joule BYOA Design Time Artifacts) for **weather-agent** is generated automatically in GitHub Actions, and what needs to be prepared before it can be deployed to SAP Joule.

---

## 1. Summary

- The `joule/` folder is **generated, not hand-written**. It is rendered from a template with placeholders.
- Data lives in **`joule.env`** (defaults). **GitHub repository variables** or **workflow inputs** can override it.
- The pipeline runs during deployment: `build-and-push.yml` builds the Docker image, then calls `generate-joule.yml`.
- The workflow renders and validates `joule/`, uploads it as a build artifact, and can optionally commit it back to the branch and deploy it to Joule.

```
joule.env  ─┐
            ├─► scripts/render-joule.sh ─► joule/  ─► artifact ─► (optional) joule deploy
joule-template/ ─┘        (envsubst)
```

---

## 2. Files

| File | Purpose | Edit? |
|---|---|---|
| `joule-template/` | The 5 Joule YAMLs with `${JOULE_*}` placeholders. File/folder names use `__AGENT_NAME__` / `__SCENARIO_NAME__`. | Only to change structure |
| `joule.env` | Values filled into the template | **Yes — main place for agent data** |
| `scripts/render-joule.sh` | Renders template → `joule/`, validates input and output | Rarely |
| `.github/workflows/generate-joule.yml` | Render / validate / artifact / commit / deploy | Rarely |
| `.github/workflows/build-and-push.yml` | Deploy pipeline; job `joule` calls the workflow above after the image build | Rarely |
| `joule/` | **Generated output** — do not edit by hand | No |

Generated structure:

```
joule/
├── da.sapdas.yaml
└── a2a/
    ├── capability.sapdas.yaml
    ├── capability_context.yaml
    ├── functions/weather_agent.yaml
    └── scenarios/weather_forecast/weather_forecast.yaml
```

---

## 3. Placeholders (`joule.env`)

| Variable | Current value | Used in | Rule |
|---|---|---|---|
| `JOULE_AGENT_NAME` | `weather_agent` | `da.sapdas.yaml` name, capability `metadata.name`, function file name, scenario `target.name` | snake_case |
| `JOULE_DISPLAY_NAME` | `Weather Assistant` | capability `display_name` | no `"` |
| `JOULE_DESTINATION` | `WEATHER_AGENT` | `system_aliases` key + `destination`, function `system_alias` | **must equal the BTP Destination name (case-sensitive)** |
| `JOULE_VERSION` | `auto` | capability `metadata.version` | `auto` = git tag `vX.Y.Z`, otherwise `1.0.0`; or `X.Y.Z` |
| `JOULE_DESCRIPTION` | Weather forecast description | capability `description` | single line |
| `JOULE_SCENARIO_NAME` | `weather_forecast` | scenario folder + file name | snake_case |
| `JOULE_SCENARIO_DESCRIPTION` | "Get the weather forecast…" | scenario `description` (used by Joule for routing) | no `"` |
| `JOULE_RESPONSE_DESCRIPTION` | "Weather forecast from…" | scenario `response_context` | no `"` |

`namespace` is fixed to `joule.ext` (lowercase, required for custom / BYOA agents). Joule rejects any other value, including `JOULE.EXT`, as the reserved `sap` namespace (Tenant Administration error 5014).

**Precedence:** workflow input → repository variable (`vars.JOULE_*`) → `joule.env`.
Repo variables are supported for `JOULE_VERSION`, `JOULE_AGENT_NAME`, `JOULE_DISPLAY_NAME`, `JOULE_DESTINATION` and `JOULE_DESCRIPTION`.

> **Note:** the script calls `envsubst` with an explicit list of `JOULE_*` variables. This keeps Joule's own `$capability_context.*` and `$target_result.*` expressions intact. Never replace it with a bare `envsubst`.

---

## 4. Validation done by the script

- All variables non-empty
- `JOULE_AGENT_NAME` / `JOULE_SCENARIO_NAME` are snake_case
- `JOULE_DESTINATION` contains only `A-Z a-z 0-9 _ -`
- Version is `X.Y.Z`
- No double quotes in quoted fields
- No leftover `${JOULE_*}` / `__NAME__` placeholders in the output
- All generated YAML parses (`yq`, preinstalled on GitHub Ubuntu runners; skipped locally if missing)

---

## 5. BTP preparation (one-time, done by a subaccount admin)

1. **Trust with SAP IAS:** BTP Cockpit → Security → Trust Configuration.
2. **Destination service instance**, plan `lite`.
3. **Joule subscription:** `das-application-canary`, plan `development`. Wait for `Subscribed`.
4. **Joule service instance:** `das-service-canary`, plan `designer`. Then create a **service key** and copy `uaa.url`, `uaa.clientid` and `uaa.clientsecret`.
5. **Role collection** with `capability_developer`, `capability_release_admin` and `end_user`. Assign it to the deploying user (the CI technical user too).
6. **BTP Destination** for the agent:

   | Field | Value |
   |---|---|
   | Name | `WEATHER_AGENT` (= `JOULE_DESTINATION`) |
   | Type | HTTP |
   | URL | `https://weather-agent.cfapps.eu10-005.hana.ondemand.com` |
   | Proxy Type | Internet |
   | Authentication | NoAuthentication (A2A passes JWT in headers) |

   **Check Connection** must return 200.

---

## 6. GitHub preparation

### Repository variables (Settings → Secrets and variables → Actions → Variables)

| Variable | Required | Purpose |
|---|---|---|
| `JOULE_AUTO_DEPLOY` | for auto-deploy | `true` = deploy to Joule after each image build |
| `JOULE_VERSION`, `JOULE_AGENT_NAME`, `JOULE_DISPLAY_NAME`, `JOULE_DESTINATION`, `JOULE_DESCRIPTION` | no | Override `joule.env` values |

### Secrets (only needed for deploy)

| Secret | Source |
|---|---|
| `JOULE_AUTH_URL` | service key `uaa.url` |
| `JOULE_API_URL` | Joule API URL from the service key |
| `JOULE_CLIENT_ID` | service key `uaa.clientid` |
| `JOULE_CLIENT_SECRET` | service key `uaa.clientsecret` |
| `JOULE_USERNAME` / `JOULE_PASSWORD` | technical user with the role collection from step 5 |

### Environment

- Create a GitHub environment named **`joule`**.
- Optionally add **required reviewers** so each deploy needs a manual approval.

> ⚠️ **To verify before the first deploy:** the option names used in the `joule login` step (`--authurl`, `--apiurl`, `--clientid`, `--clientsecret`, `--username`, `--password`) have not been confirmed against the CLI. Run `joule login --help` with your `@sap/joule-cli` version and adjust the step. The CLI also documents an env-based login (`JOULE_URL`, `JOULE_CLIENT_ID`, `JOULE_CLIENT_SECRET`), which may be a simpler alternative.

---

## 7. How to run

| Trigger | What happens |
|---|---|
| Push to `main` / tag `v*` | `build-and-push.yml` builds the image, then job `joule` renders and validates `joule/` and uploads the artifact. It deploys only if `JOULE_AUTO_DEPLOY=true`. |
| Push to `main` that changes `joule.env`, `joule-template/**`, the script or the workflow | `generate-joule.yml` runs standalone (render + validate + artifact) |
| Actions → *Generate Joule A2A files* → Run workflow | Manual. Inputs: `version`, `commit` (push `joule/` back with `[skip ci]`), `deploy` |
| Tag `v1.4.0` | Capability version becomes `1.4.0` automatically |

**Locally** (Git Bash / Linux / macOS, from `weather-agent/`):

```bash
bash scripts/render-joule.sh
```

You can override values for a single run:

```bash
JOULE_VERSION=2.0.0 JOULE_DESTINATION=WEATHER_AGENT_QA bash scripts/render-joule.sh
```

Manual deploy:

```bash
joule login -a <authurl> --sso-passcode
joule deploy --compile ./joule
joule list
joule launch weather_agent
```

---

## 8. Common changes

| Change | Do this |
|---|---|
| Rename agent / change description | Edit `joule.env` and push |
| Different destination per environment | Set the repo/environment variable `JOULE_DESTINATION` |
| Bump version | Push a tag `vX.Y.Z`, or set `JOULE_VERSION` |
| Add a second scenario | Copy `joule-template/a2a/scenarios/__SCENARIO_NAME__/` to a new placeholder (e.g. `__SCENARIO2_NAME__`). Add matching variables to `joule.env` and to `VARS` and the rename lines in `render-joule.sh` |
| Add a context variable | Edit `joule-template/a2a/capability_context.yaml` and the scenario template |

---

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Workflow fails with `... must be snake_case` / `... is empty` | Fix the value in `joule.env` or the repo variable |
| `unreplaced placeholders` | A new `${JOULE_X}` in the template is not listed in `VARS` in `render-joule.sh` |
| `joule deploy` schema error | `schema_version` too low (capability ≥ 3.27.0, DA ≥ 1.4.0) |
| `joule list` doesn't show `weather_agent` | Missing `capability_release_admin` role, or name mismatch |
| Agent visible in Joule but errors when used | BTP Destination name ≠ `JOULE_DESTINATION` (case-sensitive), or Check Connection ≠ 200 |
| `joule login` 401 | Wrong service key, or the user is not in the trusted IAS |
| Multi-turn context lost | Agent doesn't return `contextId` in the A2A response body |

---

## 10. Status

- ✅ Template rendering tested locally. The output is semantically identical to the original hand-written `joule/` folder.
- ✅ Overrides and validation errors tested locally.
- ⏳ Not yet run on GitHub Actions.
- ⏳ Deploy job untested. The `joule login` options need to be verified (see section 6).
