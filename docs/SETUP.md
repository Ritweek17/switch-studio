# Switch Studio Setup

This guide connects a Switch Room to the local Switch Studio runtime. Switch
Room messages are the handoff surface; the bot writes documents and decisions
only under `runs/<run_id>/`. Shipping writes `final.md` locally and never posts
it elsewhere.

## Prerequisites

- Python 3.11 or later and Git.
- A Switch server with a connected messaging app and permission to register
  agents and create a Room.
- The local Switch Studio checkout available to Scout, Writer, Editor, and the
  infra bot.
- `pytest` is optional and development-only; the runtime uses the standard
  library.

## 1. Install and connect Switch

1. Install and open [Switch Console](https://docs.switchagents.ai/flintai/switch/getting-started/install-switch-console).
2. Follow the Console checklist to connect to the Switch server you will use.
   Record the **server address** from Console. This is the agent bridge base
   URL and is usually the same host as the Gateway. If your deployment routes
   the Gateway separately, use the server address here. `GET /health` should
   return a healthy response. Export it in the registration shell as
   `AGENT_BRIDGE_URL` before registering the infra bot.
3. Set up the agent provider that will run Scout, Writer, and Editor. The
   current Switch setup supports Codex, Claude Code, and OpenCode providers.
   See [Set up agent providers](https://docs.switchagents.ai/flintai/switch/getting-started/set-up-agent-providers).

Switch Console can run a local server or connect to an existing server. For a
remote host, keep the host and its credentials under your team's control.

## 2. Register Scout, Writer, and Editor

In Switch Console, open **Your Agents** and create one agent for each role:

| Agent | Suggested name | Description |
|---|---|---|
| Scout | `codex.scout.ritweek` | Reads a run snapshot and posts `studio/facts@1` with every fact tied to a commit. |
| Writer | `codex.writer.ritweek` | Writes a `studio/draft@1` using only facts already posted for the run. |
| Editor | `codex.editor.ritweek` | Runs the verifier, reviews the draft, and hands off to Writer or humans. |

Use names accepted by your Switch server; registered names must be unique and
use lowercase letters, digits, `.`, `-`, or `_`. Select the provider, give each
agent a working directory that can read this repository, and add its role
instructions. Keep the three role prompts aligned with `CONTRACT.md` §§4–7.
Register each agent once; an agent can be invited to more than one Room.

## 3. Create the `#studio` Room

1. In Switch Console, choose **Your Rooms → Create room**.
2. Select the messaging app where the team will work, name the Room `studio`,
   and describe it as the Switch Studio run room.
3. Add these Room instructions:

   > This Room runs Switch Studio. Read CONTRACT.md and GLOSSARY.md first.
   > Keep each agent post to a short summary followed by exactly one fenced
   > JSON block containing one contract document. Keep every run artifact
   > under runs/<run_id>/. Scout posts facts, Writer posts drafts, and Editor
   > posts reviews. Humans use `approve S3`, `approve all`, `reject S3: reason`,
   > `edit S3: replacement text [F1]`, and `ship`. Never post or publish the
   > final document outside the local run directory.

4. Create the Room. Add the four human participants in the connected messaging
   app. In the Room, invite the three registered agents using the names shown
   in Switch Console, for example:

   ```text
   !invite-agent @codex.scout.ritweek
   !invite-agent @codex.writer.ritweek
   !invite-agent @codex.editor.ritweek
   ```

   If an agent name is different, use the exact registered name. Confirm the
   Room appears under **Your Rooms** in Switch Console and that each agent is
   listed as a Room participant. See [Create a room](https://docs.switchagents.ai/flintai/switch/getting-started/create-a-room)
   and [Onboard your agents](https://docs.switchagents.ai/flintai/switch/getting-started/onboard-your-agents).

## 4. Register the infra bot

The infra bot is a separate, always-on Switch agent client. It connects to the
Room over a WebSocket and needs its own agent ID and API key; do not reuse a Scout,
Writer, or Editor credential.

1. In the Switch Gateway, open **API Keys → Create Registration Key** and
   create a key under the account that should own the infra bot. That account
   becomes the bot's owner. Keep this registration key private; it is used only
   to create the bot identity and cannot be used as the bot's agent API key.
2. Register an always-on agent named `switch-studio-infra.ritweek` through the
   low-level Switch registration endpoint, using the agent bridge base URL
   recorded in the Switch Console step above. For example:

   ```bash
   read -rsp 'Switch registration token: ' SWITCH_REGISTRATION_TOKEN
   printf '\n'
   curl -fsS "$AGENT_BRIDGE_URL/agents" \
     -H "Authorization: Bearer $SWITCH_REGISTRATION_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{
       "name": "switch-studio-infra.ritweek",
       "description": "Switch Studio filesystem and human-command bridge",
       "connector_type": "switch-studio-infra",
       "integration_profile": {
         "connection_model": "always_on",
         "message_exchange": true,
         "pre_invocation_mediation": [],
         "post_invocation_mediation": [],
         "event_reporting": [],
         "command_capabilities": {
           "reset": "unsupported",
           "compact": "unsupported",
           "interrupt": "unsupported"
         }
       },
       "tools": [],
       "models": [],
       "metadata": {}
     }'
   unset SWITCH_REGISTRATION_TOKEN
   ```

   The response contains `id` and `api_key`. Save both securely; the API key
   is returned only once. The bot uses that API key for subsequent calls, not
   the registration key. Use the low-level `POST /agents` endpoint because
   the infra bot is an always-on protocol client rather than a Console-managed
   Codex, Claude Code, or OpenCode session.
3. In `#studio`, invite the infra bot using its registered name:

   ```text
   !invite-agent @switch-studio-infra.ritweek
   ```

   Confirm it appears as a Room participant. Its owner must have Room access.
4. Copy the **server address** and `#studio` Room ID from Switch Console.

Register the infra bot and all three agents under an owner whose permissions
allow them to address one another. New agents default to answering their owner
and that owner's agents; if the owners differ, add explicit addressing rules
for the infra bot and the other agents in Switch settings. The bot uses the
current WebSocket transport; older SSE clients are deprecated. See [The agent
protocol](https://docs.switchagents.ai/switch-rooms/internals/agent-protocol)
and [Agent connection](https://docs.switchagents.ai/switch-rooms/internals/agent-connection).
If your Switch administrator uses a different registration flow, ask them to
provide the same three values: server address, agent ID, and that agent's API
key.

## 5. Configure `.env`

Copy the checked-in template, then fill in values for this checkout:

```bash
cp .env.example .env
```

Set the run values in `.env`:

| Variable | Value |
|---|---|
| `STUDIO_MODE` | `fixture` for an offline run, or `live` for a local repository snapshot. |
| `STUDIO_REPO_PATH` | Path to the local clone to analyze in live mode. |
| `STUDIO_SNAPSHOT_PATH` | Fixture `studio/snapshot@1` JSON path in fixture mode. |
| `STUDIO_RANGE` | Git revision range to analyze; use the same range for snapshot, facts, verifier, and grader. |
| `STUDIO_FORMAT` | `howiswitch` or `blog_section`. |
| `STUDIO_RUNS_DIR` | Scratch directory for run artifacts; keep the default `runs`. |

Set the Room values from Switch:

| Variable | Value |
|---|---|
| `SWITCH_BASE_URL` | Switch server address / agent bridge base URL from Console. |
| `SWITCH_TOKEN` | API key for the registered infra bot agent. |
| `SWITCH_ROOM` | Room name, usually `#studio`; the bot resolves it to an ID. |
| `SWITCH_AGENT_ID` | Agent ID returned when the infra bot was registered. Add this line to `.env`; it is not in the starter template. |
| `SWITCH_ROOM_ID` | Optional Room ID. Set it if automatic name lookup cannot resolve `#studio`. |
| `STUDIO_RUN_ID` | Optional active run ID. Otherwise pass `--run-id` or let the bot select the latest `run.json`. |
| `STUDIO_SCOUT_AGENT` | Addressable Scout name; default `codex.scout.ritweek`. |
| `STUDIO_WRITER_AGENT` | Addressable Writer name; default `codex.writer.ritweek`. |
| `STUDIO_EDITOR_AGENT` | Addressable Editor name; default `codex.editor.ritweek`. |
| `STUDIO_SCOUT_AGENT_ID`, `STUDIO_WRITER_AGENT_ID`, `STUDIO_EDITOR_AGENT_ID` | Optional agent IDs for sender identification when event payloads omit sender kind. |

For the registration command above, set `AGENT_BRIDGE_URL` in the terminal to
the agent bridge base URL. Keep `SWITCH_REGISTRATION_TOKEN` temporary; do not
put it in `.env`.

Set `OPENAI_API_KEY` only if your agent provider or agent workflow requires it.
Never commit `.env`, registration tokens, or Switch API keys.

## 6. Start a run and the infra bot

Set `STUDIO_MODE=live` and `STUDIO_REPO_PATH` in `.env`, then start the live
demo. It creates a fresh valid `run_id`, snapshots the local clone, writes
`studio/run@1`, and starts the bot for that run:

```bash
python3 demo/run_demo.py --live-only
```

To start the bot for a run that already has a `run.json`, use:

```bash
python3 -m infra.bot --run-id your-run-id
```

The bot connects using Switch's WebSocket agent protocol and stores its
connection ID, cursor, and generation under the active run so it can resume
after a restart. On startup, it checks the run artifacts and addresses the
next agent: Scout first, then Writer, then Editor. An Editor review with
`handoff: "writer"` triggers the next Writer revision; a review with
`handoff: "humans"` tells the Room it is ready for decisions.

The bot extracts only the one fenced JSON block from an agent post. For an
overflow envelope, it verifies the SHA-256 and loads the payload only from
`runs/<run_id>/`. Human commands are appended as schema-checked lines in
`runs/<run_id>/decisions.jsonl`.

When all pending sentences have been approved, rejected, or verified through
an edit, send `ship`. The bot replays decisions with `tools.apply`, verifies
the effective non-rejected draft, and calls `tools.render` to write
`runs/<run_id>/final.md`. It does not post the final document.

## 7. Run the judge demo

The one-click demo runs the saved offline fixture first, exercises the Room
command flow, and compares the one-shot baseline with the shipped draft using
`tools.grade`. It then snapshots the local repository and starts the live Room
bot so the judges can repeat the same commands against real commits:

```bash
python3 demo/run_demo.py
```

Use `--offline-only` to stop after the fixture score or `--live-only` to skip
the fixture stage. The live stage requires `tools.snapshot`, Switch credentials,
the registered agents, and a working `#studio` Room. Every generated document
and grade stays under its run directory.

## 8. Check the setup

```bash
python3 -m schemas.validate --examples
python3 -m infra.bot --help
```

The bot and CLI tools require Python 3.11+ and use only the standard library.
`pytest` is optional and used only for development.
