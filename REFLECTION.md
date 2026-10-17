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
