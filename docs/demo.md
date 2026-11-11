# How the demo video was made

The hackathon demo video shows one recorded session of Hermes Agent v0.23.1 with this plugin
installed. This page says what is staged in it, what the plugin did and what the agent did, how
often the agent takes up an offer, and which Hermes settings the recording used. It also lets
you repeat the session.

## What the video shows

1. `/first-aid` lists the kit.
2. A prompt asks for a summary of three note files. Three reads fail in one turn. Hermes core
   warns the agent about the failures, and first aid adds a note that suggests the `redirect`
   practice. The agent loads the practice, searches for the files, reads them, logs the practice
   with `first_aid_log`, and answers.
3. `/compress` compacts the conversation.
4. With the next question, first aid adds a note that suggests `center`. The agent loads it,
   logs it, and answers.
5. `/first-aid` shows the report: both offers and both logbook lines.

## What is staged

- **The failure.** The prompt names the folder `notes/`, which does not exist. The files are in
  `meeting-notes/`. So three reads fail, which is where Hermes core's loop guardrail warns about
  one tool failing three times in a turn.
- **The opt-in.** The prompt ends with "If first aid offers a practice along the way, use it and
  log how it went." Without that sentence the agent loaded no practice in the test runs (see
  the counts below).
- **The compaction.** `/compress` is run as a command on a short conversation, not triggered by
  the context filling up.
- **The typing.** A script sent the keystrokes into the terminal at a fixed typing speed. Hermes
  and the agent ran for real.

## What the plugin does, and what it does not

The plugin picks the moment and the practice, puts one note in front of the agent, keeps the
logbook and prints the report. In this session:

- On one of the three failed reads, the tool result carries the plugin's note:
  "read_file failed 3 times this turn. The agent-almanac-first-aid plugin suggests the redirect
  practice: reframe the approach instead of varying the call. Load it with
  skill_view("agent-almanac-first-aid:redirect"), then log how it went with first_aid_log."
- After the compaction, the next user message carries: "[first aid, from the
  agent-almanac-first-aid plugin, not the user] Your context was compacted since the last
  message. Try center: re-ground in the task first. Load it with
  skill_view("agent-almanac-first-aid:center"), then log how it went with first_aid_log."

Neither note is shown on the chat screen; they go to the agent. On screen, the third failed read
is tagged `[error]` where the first two say `[File not found: …]`. That tag is the trace of
Hermes core's own warning, which is appended to that result. It appears with the plugin switched
off as well.

The video does not show the practice rescuing the agent. The agent started its file search in
the same step in which it loaded `redirect`, and in test runs without the opt-in sentence it
found the files by itself. What the video shows is the mechanism: offer, load, log, report. The
logbook words ("helped", "partly") are the agent's own account. So is its on-screen sentence "I
stopped retrying": it had read three different files once each and had not retried any of them.

## How often the agent takes an offer

Counted in test runs with the same model and Hermes version, in copies of the demo profile:

| Prompt | Runs | Streak practice loaded and logged | `center` loaded after compaction |
|---|---|---|---|
| Three files in the wrong folder, without an opt-in sentence | 3 | 0 | 0 of 2 |
| The same prompt with the opt-in sentence used in the video | 4 | 4 | 4 of 4 |
| The same prompt with an earlier wording of the sentence ("If your first-aid kit offers a practice, use it and log how it went.") | 3 | 3 | 2 of 2 |
| A retry of one missing file, with that earlier wording | 3 | 3 | 2 of 3 |

In the first row the plugin's offer is recorded in the report of two runs; the third run ended
before its report, so the offer there is inferred. Where the last column counts fewer runs, a
run ended before the compaction. The sentence was reworded once, because with the earlier
wording the agent also loaded a practice that had not been offered.

With an earlier demo script (a retry of one missing file, no opt-in sentence), the agent loaded
`center` in 5 of 22 completed takes and never loaded `breathe`, which was offered every time.

So with this model, the agent took the plugin's offers in all ten test runs where the user had
asked for that, and in none of the three where the user had not. The plugin never forces a
practice: it offers, and the agent decides.

## Setup of the recording

- Hermes Agent v0.23.1, the model shown in the video's status bar, and a profile that holds
  only this plugin at the catalog's pinned commit. The profile was created without Hermes's
  bundled skills (`hermes profile create --no-skills`), so its banner lists one skill where a
  stock install lists about forty; its persona file is the Hermes default. A run with the
  bundled skills present was not tested.
- A fresh session store before the take, so the chat title carries no running number.
- Four Hermes settings differ from the defaults:

  | Setting | Value | Why |
  |---|---|---|
  | `display.status_bar.fields` | every default field except `compressions` | In v0.23.1, with the compression counter shown in the status bar, the screen is garbled after a compaction: stray `─` marks at the start of rows and scrambled typed text. With the counter hidden it is not. This happens with the plugin switched off too. [Hermes issue 120588](https://github.com/NousResearch/hermes-agent/issues/120588) reports another symptom of the same counter (a garbled timer) and names a width mismatch as the cause; that mechanism was not verified here. |
  | `display.suppress_warning_notifications` | `true` | Without it Hermes prints two rows "🔧 Auto-repaired tool name: …" each time the agent calls the logbook tool. They are Hermes's notice about the tool name the model used, not a plugin fault. The setting hides Hermes's other diagnostic notices as well; which ones was not checked. |
  | `auxiliary.background_review.enabled` | `false` | Hermes's background skill maintenance (the post-turn review and the curator) can write skills into the profile. During the test runs it wrote one that described the same behaviour as this plugin; the two takes recorded with it were discarded. |
  | `curator.enabled` | `false` | Same reason: nothing but the plugin should change the profile between takes. |

  If you repeat the session without the first two settings, expect the stray marks after
  `/compress` and the "Auto-repaired tool name" rows.

## Editing

- One take, recorded from the terminal window: the third take of this script. The first did not
  complete and the second was discarded (see the settings table).
- Three spans in which the screen waits for the agent are fast-forwarded, and each carries a
  marker with its speed factor. Everything else plays at recorded speed.
- Two zooms and an enlarged copy of the report lines were added, along with five captions, a
  title card and an end card. No output was changed.
- On the recording machine Hermes's own second counters ran about 8 to 10 percent fast because
  of a clock fault in the Linux subsystem, so the durations Hermes prints (for example "23.7s")
  carry that error. The recording itself is real time.

## Try it yourself

```bash
hermes plugins install agent-almanac-first-aid --enable
mkdir -p first-aid-demo/meeting-notes && cd first-aid-demo
printf '# Monday\n- Ship the beta on Friday.\n' > meeting-notes/mon.md
printf '# Tuesday\n- Beta blocked on the login bug.\n' > meeting-notes/tue.md
printf '# Wednesday\n- Login bug fixed; Friday still on.\n' > meeting-notes/wed.md
hermes chat
```

Then type, one after the other:

```text
/first-aid
Summarize the week in two lines from notes/mon.md, notes/tue.md and notes/wed.md. If first aid offers a practice along the way, use it and log how it went.
/compress
One more sentence: when does the beta ship?
/first-aid
```

The failure note needs the three failed reads inside one turn: Hermes core counts failures per
turn, and the plugin follows it. Typing "try again" in a new message starts the count again.
Whether the agent loads a practice is its own choice, so your run can differ from the video.

To get the screen of the video, add the two display settings to the profile's `config.yaml`
before you start:

```yaml
display:
  suppress_warning_notifications: true
  status_bar:
    fields: [model, context_detail, context_pct, cache_hit, latency, tps, bg_tasks, bg_processes, bg_subagents, goal, duration, prompt_elapsed, idle_since, focus, yolo, stash, battery, title]
```

All of this was tested on Hermes v0.23.1 only.
