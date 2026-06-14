# TABIB Tier 3 Plan — WhatsApp-to-Band Bridge

## Context

Tier 2 established the full Band SDK API surface available to the pipeline.
This document captures the architecture for Tier 3: a stateful WhatsApp bridge
that routes each patient conversation through a dedicated Band room containing
the three TABIB agents.

---

## Tier 2 Findings: Band SDK API Surface

### REST clients (via `AsyncRestClient`)

| Namespace | Key methods |
|---|---|
| `rest.agent_api_chats` | `create_agent_chat(ChatRoomRequest)`, `get_agent_chat(chat_id)`, `list_agent_chats()` |
| `rest.agent_api_messages` | `create_agent_chat_message(chat_id, ChatMessageRequest)`, `list_agent_messages(chat_id, status, page)`, `get_agent_next_message(chat_id)`, `mark_agent_message_processing`, `mark_agent_message_processed`, `mark_agent_message_failed` |
| `rest.human_api_chats` | `create_my_chat_room()`, `get_my_chat_room(chat_id)`, `list_my_chats()` |
| `rest.human_api_messages` | `send_my_chat_message(chat_id, ...)`, `list_my_chat_messages(chat_id)` |

### Key types

```python
ChatRoomRequest(task_id=None)          # creates a room; title auto-generated from first message

ChatMessageRequest(
    content="@TabibIntake please assess this patient",
    mentions=[ChatMessageRequestMentionsItem(id=agent_id, username="TabibIntake")]
)
```

### WebSocket (via `BandLink`)

```python
link = BandLink(agent_id=..., api_key=...)
await link.connect()
await link.subscribe_room(room_id)        # joins chat_room: + room_participants: topics
await link.subscribe_agent_rooms(agent_id)

async for event in link:
    if isinstance(event, MessageEvent):
        msg = event.payload  # .id, .content, .sender_id, .sender_type, .metadata
```

Message lifecycle: `mark_processing` → execute → `mark_processed` / `mark_failed`

---

## Proposed Tier 3 Architecture

### Core idea

Each WhatsApp phone number maps to a single persistent Band room. When a patient
messages in, the bridge posts their text into that room with an @mention to the
Intake Agent. The pipeline runs (TIA → TDA → TTA), and the bridge polls for the
Triage Agent's final response, detected by the `"Decision:"` keyword. That
response is forwarded back to the patient over WhatsApp.

### Room lifecycle

```
First message from +919849828813
    → check room_registry for existing room_id
    → if none: create_agent_chat() → new room_id
    → store {phone: room_id} in room_registry (dict / sqlite / redis)
    → add TIA, TDA, TTA as participants (see open question below)

Subsequent messages
    → look up room_id from room_registry
    → post message directly into existing room
```

### Message flow

```
[WhatsApp] patient message
    ↓  webhook.py receives POST /webhook
    ↓  look up or create Band room for phone number
    ↓  rest.agent_api_messages.create_agent_chat_message(
           chat_id=room_id,
           request=ChatMessageRequest(
               content=f"@TabibIntake {patient_message}",
               mentions=[{id: TIA_agent_id, username: "TabibIntake"}]
           )
       )
    ↓  poll for completion (see below)
    ↓  send final response back via Twilio
```

### Completion detection (multi-turn polling)

The Triage Agent always includes `"Decision:"` in its output. The bridge uses
this as the terminal signal:

```python
async def poll_for_response(room_id, timeout=60, interval=3):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        messages = await rest.agent_api_messages.list_agent_messages(chat_id=room_id)
        for msg in messages.data:
            if msg.id in seen_ids:
                continue
            seen_ids.add(msg.id)
            if msg.sender_type == "Agent" and "Decision:" in (msg.content or ""):
                return msg.content   # final triage response
        await asyncio.sleep(interval)
    raise TimeoutError("No Decision response within timeout")
```

### Room registry

```python
# Minimal: in-memory dict (lost on restart)
room_registry: dict[str, str] = {}   # phone_number → band_room_id

# Production: persist to sqlite or redis
# key: "room:{e164_phone}"  value: band_room_id
```

### Files to create / modify

| File | Change |
|---|---|
| `src/band_bridge.py` | New — `BandBridge` class: room registry, `get_or_create_room()`, `post_and_poll()` |
| `src/webhook.py` | Replace `run_tabib_pipeline()` call with `await bridge.post_and_poll()` |
| `src/band_coordinator.py` | Retire (logic moves into BandBridge + Band agents) |

---

## Open Question for Tomorrow

**How do new rooms get TIA / TDA / TTA added as participants?**

When `create_agent_chat()` creates a fresh room, it is empty. The three Band
agents need to be participants before @mentions work. Options to investigate:

1. **`ParticipantRequest` via REST** — the SDK exports `ParticipantRequest`;
   check whether `rest.agent_api_participants.add_participant(chat_id, ParticipantRequest(...))` exists and accepts agent IDs.

2. **Auto-join on @mention** — Band may auto-add an agent to a room the first
   time it is @mentioned. Test: post a message into a fresh room with @TabibIntake
   and observe whether the agent subscribes itself.

3. **Room template / invite on creation** — `ChatRoomRequest` currently only
   exposes `task_id`; there may be undocumented fields (e.g. `participants`,
   `agent_ids`) that pre-populate membership.

4. **Manual invite endpoint** — check `thenvoi_rest` for a
   `human_api_participants` or `agent_api_participants` namespace that exposes
   an invite/add method.

Resolve by inspecting `thenvoi_rest` participant client methods and running a
live test room tomorrow.
