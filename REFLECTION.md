# Reflection: how Hotel Spot works, and how we kept it fast and cheap

## The process in one paragraph

You type something like "cheapest hotel in Toronto on Nov 2 for 2 people". Hotel Spot works out the city, dates and guests, finds the cheapest hotels near downtown, and has an AI voice agent **phone the 3 cheapest at the same time** to ask if they have a room and what the lowest nightly rate is. It never books. When the calls end, you get **one email** with the cheapest hotel that has a room, its price, the call transcript and the call recording. If none of the 3 has a room, it calls the next 3.

Our rule throughout: **never spend LLM tokens on work plain code can do.** An LLM is only used for the live phone conversation and as a last-resort fallback.

## Step by step

### 1. Read the prompt
**What happens:** we pull out the city, check-in/check-out dates and number of guests.

**How we kept it fast and cheap:**
- **Regex first, 0 tokens, under a millisecond.** It handles "tonight", "tomorrow", "Nov 2", "Nov 2-4", "for 3 nights", "for two people", and so on.
- An LLM (Backboard) is only called when no city can be found, for example "the capital of Canada". That costs about 160 tokens and rarely happens.
- Sensible defaults when the prompt is silent: tomorrow, 1 night, 1 guest.

### 2. Find hotels
**What happens:** SerpApi (Google Hotels) finds the cheapest listings, then a second lookup gets each one's phone number and address.

**How we kept it fast and cheap:**
- **No LLM at all.** Sorting, filtering and phone formatting are plain code.
- **Everything runs in parallel:** the hotel search, the rental search and the phone-number lookups. A real Toronto search took about 9 s for 9 SerpApi requests.
- **We only look up a short list** (the cheapest 5 hotels + 2 rentals), so we don't pay for details on listings we'd never call.
- **We filter before calling:** within 15 km of downtown (and 20 km of the city border, the organizers' rule), with a phone and a street address, no duplicates. Distance also counts in the ranking ($2 per km per night), so a cheap hotel far out doesn't beat a slightly pricier central one.
- In development, search results are cached so repeated tests don't burn credits.

### 3. Check past calls (memory)
**What happens:** for each hotel we look up notes from earlier runs, like "menu: press 1 for reservations; put on hold", and give them to the agent.

**How we kept it fast and cheap:**
- **0 LLM tokens.** The notes are written by our own code after each run (one short line per call) and read back with a memory search. No LLM summarises anything.
- The 3 lookups run in parallel (about 1 s) with a 3-second timeout, and notes are capped at 160 characters so they don't bloat the agent's prompt.
- Saving the notes happens **after** the email is sent, so it never slows down the result.

### 4. Prepare the calls
**What happens:** each call gets the agent's instructions plus that hotel's details (name, dates, guests, past-call notes).

**How we kept it fast and cheap:**
- **A template fill, 0 tokens.** No LLM writes the instructions.
- **A short prompt:** about 238 tokens, plus about 110 tokens of call-handling rules. Per-call facts go in as variables, so the instructions are identical for all 3 calls and ElevenLabs can cache them.
- **The first sentence asks the real question straight away**: "what's the cheapest room you have available from Monday, November 2 to Tuesday, November 3?" Fewer back-and-forth turns means a shorter call and fewer tokens.

### 5. Make the calls
**What happens:** the ElevenLabs voice agent phones all 3 hotels at once, gets through phone menus and hold music, and asks for the cheapest room and rate.

**How we kept it fast and cheap:**
- **All 3 calls at the same time.** Total call time is the slowest call, not the three added together.
- **The conversation runs on ElevenLabs' own fast LLM, not Backboard.** We measured Backboard at 1.1–1.5 s per reply, far too slow for a natural phone conversation, so Backboard only runs before and after the calls.
- **Calls end early when they should:** voicemail is detected and hung up on, and every call has a hard time limit (180 s), so one stuck call can't hold up the run.
- **Most of the agent's context is cached.** On the first real run the agent's actual usage (what ElevenLabs bills and shows on its dashboard) was **1,901 tokens for 3 calls, about $0.045**. Behind that, the agent re-read about 89k tokens of context across all its turns, but 81% of it came from cache, which is much cheaper.

### 6. Read the answer
**What happens:** for each call we work out "room or no room" and the rate.

**How we kept it fast and cheap:** we try the cheapest, most reliable source first and stop as soon as we have an answer.
1. The agent records the answer itself with a tool call (structured data, 0 tokens).
2. If that didn't reach us, we read the same tool call from ElevenLabs' record of the conversation (0 tokens).
3. Otherwise a regex reads the transcript (0 tokens).
4. Only if all of that fails do we ask Backboard, at about 200 tokens per call.

On a normal run this whole step uses **0 tokens**.

### 7. Call 3 more, only if needed
**What happens:** if none of the first 3 hotels confirmed a room with a rate, we search the next 3 cheapest (skipping ones we already called) and call them all at once.

**How we kept it fast and cheap:**
- It only runs when it has to. If any hotel says yes with a price, we stop.
- It only checks the **new** hotels' answers, so we never pay for the first round's fallbacks twice.

### 8. Send the email
**What happens:** one email with the cheapest hotel that has a room: name, address, phone, price for the stay, the call transcript and the call recording as an MP3. The other hotels get one line each.

**How we kept it fast and cheap:**
- **0 tokens.** The email is built with plain code.
- Only the winning hotel's transcript and recording are included, which keeps the email small (about 1 MB per minute of call).

## The token bill for a normal run

| Step | Tokens |
|---|---|
| Read the prompt | 0 (regex) |
| Find hotels | 0 (no LLM) |
| Check past calls | 0 (no LLM) |
| Prepare the calls | 0 (template) |
| The calls themselves | ~1,900 for all 3 calls (first real run: 1,901, about $0.045) |
| Read the answers | 0 when the agent's tool call works |
| Send the email | 0 |
| **Backboard total** | **0 on a normal run.** Worst case about 160 + 200 per unclear call, well under our 1,200 budget |

## What we learned along the way

- **Measure before choosing a model.** All the Backboard models we tried replied in 1.1–1.5 s, so speed didn't decide it. We picked `gemini-2.5-flash-lite` because it used the fewest tokens for the same answer.
- **Check what "tokens" means before reporting it.** ElevenLabs reports usage in overlapping categories plus cached context. Adding them all up first showed 143k tokens. Matching the dashboard's definition gave the honest number: 1,901.
- **Don't trust a "200 OK".** Backboard returns billing and model errors as a normal success with `status: FAILED` inside. We check for it so an error message is never treated as an answer.
- **Have more than one way to get the answer.** On one real run the agent's tool result went to a teammate's laptop instead of ours. Because we can also read the answer from the conversation record and the transcript, the result was still correct.
- **Shorter calls save the most.** Our prompt is only a small part of what the agent reads each turn, so cutting a few turns from a call saves far more than trimming the prompt.

## Voice and telephony (Jordan: WS3 and team lead)

### My responsibilities
- **Team lead:** the plan and module boundaries (architecture, one doc per workstream, integration and demo runbook), the git workflow (one branch per person, merge commits only, shared files changed only in small contract PRs), the workstream assignment, and the repo setup (`.gitignore`, `.env.example`).
- **WS3, the phone calls:** the ElevenLabs agent and Twilio number, `voice_agent.py` (`place_call` and `conversation_details`), the WS3 section of `config.py`, the `record_availability` tool definition (`agents/booking_tool.json`), `docs/ws3-voice.md`, and 24 voice tests (`tests/test_voice.py` plus the shared contract test).

### What I built
- **`place_call(plan)`** starts one outbound call and blocks until it's over. The pipeline runs 3 of these at once, so it shares no state between calls. It sends that call's prompt, opener and variables, polls the conversation every 2 s, retries while the transcript is still empty, and returns one `Agent:` / `Hotel:` line per turn.
- **Outcome in a fixed order, all 0 tokens:** voicemail (the voicemail tool fired) → no answer (the hotel never spoke) → the agent's own tool result → the same tool call read from the conversation record (Zach's fix, for when the tool's request reached another laptop) → Isaac's transcript regex. A person who answered without a clear yes is reported as "unavailable" with the answer left unknown, so the email never claims a hotel is sold out when we didn't hear it.
- **Agent setup in code where possible:** the prompt and call rules are sent with every call, so the behaviour is in git and tested, not only in the dashboard. The live dashboard settings are recorded in `docs/ws3-voice.md`.

### What live calls taught us
Every fix below came from a real call transcript, not a guess.

| What went wrong on a call | Fix |
|---|---|
| The agent started a 9-second opener the moment the line connected, talking over the hotel's phone menu | **Listen first:** stay silent on pickup and say the opener after the hotel speaks |
| "Press 1" was heard as "trust one", and the agent picked front desk over reservations | Speech-recognition keywords (press, reservations, front desk, available, sold out, …) and menu rules (reservations first, press 0 if unclear) |
| Staff said "one moment" and the agent asked "Are you still there?" after 10 s | Wait 25 s before speaking into silence, and a rule to stay silent through holds |
| The agent's text had `[slow]`, `[happy]`, `[speaking]` tags in it | Turned off the voice's expressive mode, which asks the LLM to write emotion tags |
| It jumped in on half sentences ("I think-") | Patient turn-taking (replies went from about 0.8 s to about 1.0 s) |
| A franchise answered with its brand name, and a misheard "hi" became "bye" | Rules: a different or brand name isn't a wrong number; a lone "bye" at pickup is probably misheard |
| It said goodbye but never hung up; later, endings took about 15 s with two goodbyes | Repeat back only the price ("So $43 a night?"), then one "Thank you!" through the hang-up tool |
| A phone system asked for an access code and got a spoken explanation | Press 0 for codes and extensions, hang up if nothing changes |
| The agent was told to call `record_availability`, but the tool was never attached to it | Created and attached it through the API, then checked it end to end: 200 with the right secret, 401 without |

### Numbers
- **Reply time** (from the hotel stopping to the agent's voice starting): median **0.77 s**, 90th percentile 1.04 s, and about **1.0 s** after switching to patient turns. Speech recognition took 0.04 s, the LLM's first token 0.15 s and the voice's first audio 0.08 s. The other ~0.5 s is turn detection plus the phone network, so a faster LLM would barely help.
- **The `record_availability` round trip** (ElevenLabs → tunnel → laptop): **0.12 s**.
- **The first 11 real-hotel calls**, made before the fixes above: **3 answered, 4 voicemail, 4 hung up.** All four hang-ups trace to problems in the table (the "still there?" interruption, tags, talking over the hotel, the access code). We haven't re-measured since the fixes.
- ElevenLabs now scores every call on three criteria (got availability, ended promptly, never booked), so the success rate can be tracked without reading transcripts.

### Setup lessons
- **Telephony takes the longest:** ElevenLabs doesn't sell numbers, so it's a Twilio number imported into ElevenLabs. Twilio's trial can only call verified numbers and plays a "press a key" message first, so we upgraded. Canada had to be enabled in Twilio's geo permissions, or every call failed with "not authorized".
- **The tunnel is a single point of failure:** the tool needs a public URL to the laptop running the app. Our ngrok was too old for the account (3.3.1 vs a 3.20 minimum), the token was pasted wrong once, and two laptops on the same domain sent a result to the wrong machine. Rule for the demo: exactly one laptop runs the tunnel.

### Critique: what I'd do differently
- **The prompt grew from 238 to about 628 tokens**, because every live failure added a rule. Calls became far more robust, but the prompt is now about twice our budget. Next step: merge the overlapping rules (holds, menus and "I'm automated" appear in both the base prompt and the call rules), or move rare cases into ElevenLabs workflow nodes that only load when needed.
- **Test against real phone systems earlier.** Teammates playing the hotel passed every test. Real receptionists, phone menus and voicemail exposed different failures in the first 11 calls.
- **Keep the agent's configuration in the repo from day one.** For most of the day the tool and settings lived only in the ElevenLabs dashboard, which is how the missing tool went unnoticed. Creating and updating the agent through the API from a checked-in file would have caught it.
- **We stayed honest even though hotels dislike AI calls.** If asked, the agent says it's an automated assistant, and it leaves politely if they don't want to talk to one. Instead of hiding what it is, we worked on sounding relaxed and getting to the point in the first sentence; whether that wins more answers still needs a measured batch of real calls.
- **Voicemail is the biggest limit we can't fix in the conversation itself:** about a third of real calls hit it. Calling the next 3 hotels when none of the first 3 confirm (now in the pipeline) is the right lever.
