# Lectern

> *lectern* — the stand a teacher speaks from.

A self-hostable, fully open-source assistant that turns your source material — or just a topic —
into finished teaching artifacts: **slide decks, lecture notes and question papers**, plus lesson
plans, rubrics, worksheets, flashcards and grading help.

It runs **completely offline** on your own machine. No account, no API key, no data leaving your
computer.

## Install it

**Windows** — in PowerShell:

```powershell
irm https://raw.githubusercontent.com/maliijaz/lectern/main/install.ps1 | iex
```

**macOS or Linux** — in a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/maliijaz/lectern/main/install.sh | bash
```

That is the whole thing. It checks what you already have, installs whatever is missing
— [Python](https://python.org), [Node](https://nodejs.org), [Ollama](https://ollama.com)
— downloads the model, builds the interface, and opens your browser on it. It shows you
the plan and waits for you to press Enter before it touches anything.

**Run that same command again to start Lectern.** It notices it is already installed and
just launches. On Windows there is also a Start Menu entry; on macOS and Linux, a
`lectern-app` command.

| To do this | Add this |
|---|---|
| Get the latest version first | `-Update` / `--update` |
| Put it on a public link for a colleague | `-Share` / `--share` |
| Install somewhere else | `-Path D:\lectern` / `--path ~/apps/lectern` |
| Use a hosted model instead of a local one | `-SkipModel` / `--skip-model` |

Those scripts are [install.ps1](install.ps1) and [install.sh](install.sh). They are short,
they do nothing clever, and reading one before you run it is a reasonable thing to do.

---

## What it makes

| Give it | Get back |
|---|---|
| A chapter PDF, or just a topic | A `.pptx` deck with speaker notes, quiz slides and diagrams |
| The same source | Lecture notes with learning objectives, worked examples and misconception callouts |
| The same source + a marks total | A question paper built to a Bloom's-taxonomy blueprint, in multiple shuffled sets, with answer keys |
| A syllabus unit | A timed lesson plan with differentiation and checks for understanding |
| An assignment brief | A rubric whose descriptors describe observable work, not adverbs |
| Key terms | An Anki deck, a Quizlet import, or printable flashcards |
| A student's answer + a rubric | A first-pass marking proposal, with the evidence for every judgement |

Every artifact exports to many formats:

**PPTX · DOCX · PDF · Markdown · HTML · reveal.js · Moodle XML · GIFT · QTI 2.1 · Anki `.apkg`
· Quizlet TSV · Google Forms CSV · CSV · JSON**

A slide deck also exports a **narration pack** — the spoken script per slide, with audio when
Piper is installed — for flipped classrooms, accessibility, or a student who missed the lesson.

---

## How it works

The language model never writes a `.pptx` or a PDF. It produces **validated JSON** against a strict
schema, and deterministic renderers turn that one object into every output format:

```
source docs ──▶ parse ──▶ chunk ──▶ embed ──┐
                                            ├─▶ LLM ─▶ validated JSON ─▶ renderers ─▶ pptx / docx / pdf
topic + audience ───────────────────────────┘      (Pydantic)                        moodle / qti / anki …
```

Three consequences make the product what it is:

- **You can edit the result and re-export instantly.** Fix one bullet or swap a distractor in the
  browser, then regenerate every format without another model run.
- **Output formatting is never mangled**, because the model is never asked to produce markup.
  Schema-constrained decoding plus a validation repair loop handles the rest.
- **Everything is testable.** The renderers are pure functions; the test suite runs with no model,
  no GPU and no network.

### Exams get a blueprint, not a pile of questions

Before a single question is written, the marks are distributed across topics × Bloom's levels ×
difficulty, and the question format is chosen to suit the cognitive level — you cannot assess
*evaluate* with a multiple-choice item, so it doesn't try. The finished paper is then **checked back
against that plan** and repaired where it drifted. It also flags the classic flaws: duplicate
questions, "all of the above", and a correct option conspicuously longer than its distractors.

### The answer key is checked independently

Structural validation is not enough, and there is a real example behind that claim. A generated
paper here asked *"which is the primary site of the light-dependent reactions?"* and marked
**Chloroplast stroma** correct over **Thylakoid membrane** — while the rationale attached to that
very option read "the stroma is where the light-independent reactions occur". The model contradicted
itself inside one question. The paper passed every structural check, because structurally it was
perfect.

A wrong answer key is the most harmful thing this product could produce. It does not look wrong; it
gets printed, handed to thirty students and marked against.

So every machine-markable question is **answered again from scratch**, in a fresh context that never
sees which option was marked. Disagreements are flagged on the question itself, in the answer key,
and at the top of the artifact page. Verified against that exact question: it was caught, with no
false alarms on correct questions, in seven seconds. Turn it off with `verify_answers: false` if you
would rather have the minute back.

---

## Requirements

The installer handles every one of these. This list is here so you know what went onto
your machine, not so you can install them yourself.

- **Python 3.11+**
- **Node 20+** (for the web UI)
- **[Ollama](https://ollama.com)** with a model pulled — `ollama pull qwen3:8b` is a good default
- **An NVIDIA GPU is strongly recommended** but not required — see below

Instead of Ollama you can point it at any OpenAI-compatible endpoint (llama.cpp, vLLM, LM Studio,
or a hosted service) from the Settings page.

## Using your graphics card

This is the difference between the product being pleasant and being painful, and it is easy to get
silently wrong — so the app handles it for you.

**The trap.** Ollama does not refuse a context window that will not fit in VRAM. It quietly moves
some of the model's layers onto the CPU, and generation becomes several times slower with every
core pinned. Nothing warns you. Measured here on an 8 GB RTX 4060 with qwen3:8b:

| Context | Footprint | On the GPU |
|---|---|---|
| 4K | 5.58 GB | 100% |
| 8K | 6.19 GB | 100% |
| 12K | 7.20 GB | 87% — spills |
| 16K | 7.81 GB | 80% — spills badly |

**What the app does.** `LECTERN_LLM_NUM_CTX` is treated as a *ceiling*. On startup the app reads your
card's VRAM and the model's real size, picks the largest context that keeps the whole model on the
GPU, and says so in plain language. It also reads Ollama's own report of where the model actually
ended up, and warns at the top of every page if any of it is on the CPU. Set
`LECTERN_LLM_AUTO_CONTEXT=false` to use your number exactly — the right choice on a 24 GB card.

**Embeddings too.** `LECTERN_EMBED_DEVICE=auto` puts document indexing on the GPU when there is room,
which measured **6.6× faster** here (5.9 → 39 passages/sec). It falls back to the CPU automatically
when the language model has already claimed the VRAM, rather than failing mid-ingest.

The installer puts in the CUDA build of PyTorch when it finds an NVIDIA card. Check what the
app sees at any time:

```bash
lectern status
```

**No GPU?** Everything still works. Use a smaller model — `ollama pull qwen3:4b` or
`gemma3:4b` — and expect a question paper to take considerably longer.

---

## Deploying it

Two very different things can be called "deploying this", and it is worth being clear which
you want.

### Self-hosted, on a machine with a GPU — the real thing

This is what the product is for: everything works, nothing leaves the building, no account
anywhere. A school laptop with an 8 GB card is enough. It is the [one command at the top of
this page](#install-it); there is nothing else to set up.

### A public URL, from your own machine — the whole product, free

The catch with every free hosting tier is that none of them has a GPU, so none of them can
run your model. Your machine already does. A [Cloudflare quick
tunnel](https://trycloudflare.com) puts it on a public HTTPS address with no account, no
card and no port forwarding:

```powershell
irm https://raw.githubusercontent.com/maliijaz/lectern/main/install.ps1 | iex -Share
```

```bash
curl -fsSL https://raw.githubusercontent.com/maliijaz/lectern/main/install.sh | bash -s -- --share
```

That starts the app, prints an access key and opens the tunnel. Everything
works — your GPU, your uploaded documents, your library kept on disk — for as long as you
leave it running. The URL dies when you stop it, and changes each time.

Because the URL is genuinely public and the app has no login, share mode refuses to run
without `LECTERN_ACCESS_KEY` and generates one if you have not set it. Send people the
URL with `?key=...` on the end; it is swapped for a cookie on first load, so the secret
does not linger in history or `Referer` headers. Set the same variable on any hosted
deploy.

### A hosted demo, on a free tier — with real limits

Worth knowing before you go looking: **free Docker hosting has largely stopped being free
in 2026.** Hugging Face now needs a paid plan for Docker Spaces, Fly and Northflank ask
for a card, and Koyeb's free instance is in flux since the Mistral acquisition. Render's
free web service still exists, but its **Blueprint flow asks for a card** even for free
services — so use the manual path instead, which does not:

```
render.com  ->  New > Web Service  ->  connect this repo
                Language: Docker
                Instance Type: Free
                Health Check Path: /health
                Environment: paste the block below, then add LECTERN_LLM_API_KEY
```

Render reads the `Dockerfile` directly here; [`render.yaml`](render.yaml) is still the
reference for what to set, and still works if you ever have a card on file. The one thing
you must add by hand is a free [Groq](https://console.groq.com) key as
`LECTERN_LLM_API_KEY`.

```ini
LECTERN_LLM_PROVIDER=openai_compat
LECTERN_LLM_BASE_URL=https://api.groq.com/openai/v1
LECTERN_LLM_MODEL=openai/gpt-oss-20b
LECTERN_WORKER_CONCURRENCY=1
LECTERN_EMBED_DEVICE=cpu
LECTERN_OCR_ENABLED=false
LECTERN_MAX_UPLOAD_MB=10
LECTERN_DATA_DIR=/data
LECTERN_DATABASE_URL=sqlite+aiosqlite:////data/lectern.db
LECTERN_CORS_ORIGINS=
LECTERN_ACCESS_KEY=pick-something
```

Set **Docker Build Arg** `EXTRAS` to an empty string, or the build pulls PyTorch and will
not fit the free plan.

**What you give up, and you should know before you send anyone the link:**

| | Your machine, tunnelled | Free hosting tier |
|---|---|---|
| Generation from a topic | yes | yes |
| Every export format | yes | yes |
| Upload PDFs and Word files | yes | **no** — PyTorch does not fit in 512 MB |
| Search your own documents | yes | **no** — same reason |
| Charts, diagrams, narration audio | yes | **no** |
| Your work is kept | yes | **no** — the disk is wiped on every restart |
| Privacy | total | prompts go to whichever endpoint you chose |
| Runs when your machine is off | **no** | yes |
| First request after idling | instant | about a minute while it wakes |

The app does not pretend otherwise: the unavailable features are greyed out with the reason
and the command that would enable them, because the capability check is the same one that
runs locally.

**If the demo is for other people to use**, set `LECTERN_LLM_API_KEY` to a key you are
willing to have spent, and know what a free key actually buys. Groq's free tier allows 30
requests a minute but only **8,000 tokens a minute and 200,000 a day** — and tokens are the
limit that bites first. One question paper is about twenty calls, so it will be throttled
partway through. It still finishes: a rate-limited call reads the reset time off the
response and waits exactly that long rather than guessing. Expect a paper to take minutes,
and about ten of them a day from one key.

---

## Command line

The CLI runs the same code in-process, so it works with the server stopped. It is the right tool for
batch work — ingesting a term's chapters overnight, or generating differentiated worksheets in a loop.

```bash
lectern status                                   # config, model connection, what is installed
lectern docs add ./chapter3.pdf --subject Biology --grade "Grade 10"
lectern docs search "how does rubisco fix carbon"

lectern generate slides  --topic "Photosynthesis" --slides 15 --theme chalkboard --out ./out
lectern generate notes   --doc 6687a695 --depth detailed --out ./out
lectern generate exam    --doc 6687a695 --marks 50 --duration 90 --variants 3 --out ./out
lectern generate worksheet  --topic "Quadratic equations" --questions 15
lectern generate flashcards --doc 6687a695 --cards 40

lectern list                                     # what you have made
lectern export <artifact-id> -f moodle_xml -f qti --out ./out
lectern formats exam                             # what a paper can be exported as
```

---

## Configuration

Copy `.env.example` to `.env`. Everything in it can also be changed at runtime from the Settings
page, where UI values take precedence.

The settings worth knowing:

| Setting | What it does |
|---|---|
| `LECTERN_LLM_PROVIDER` | `ollama`, `openai_compat`, or `fake` (a deterministic stub used by the tests) |
| `LECTERN_LLM_MODEL` | The model to use. Bigger is better for exams specifically |
| `LECTERN_LLM_NUM_CTX` | Context window *ceiling*. Reduced automatically to fit your GPU |
| `LECTERN_LLM_AUTO_CONTEXT` | Turn off to use `LECTERN_LLM_NUM_CTX` exactly as written |
| `LECTERN_LLM_THINKING` | Chain-of-thought on reasoning models. Off by default: ~2.4x faster |
| `LECTERN_EMBED_DEVICE` | `auto`, `cpu`, `cuda` or `mps`. `auto` uses the GPU when there is room |
| `LECTERN_OCR_ENABLED` | Read scanned PDFs. Slower, but the only way to use a photographed handout |
| `LECTERN_CHUNK_TOKENS` | Passage size for retrieval. Only affects documents added afterwards |
| `LECTERN_WORKER_CONCURRENCY` | How many generations run at once |

---

## Choosing a model

Leaderboards measure chat quality and coding. Neither is what breaks here — what breaks is
schema compliance, factual accuracy on answer keys, and whether the model can count to three.
So the repository ships a harness that scores any installed model on those directly:

```bash
cd backend
python -m app.tests.eval_models              # every model Ollama has
python -m app.tests.eval_models qwen3:8b     # just one
```

It reports, per model: how often it produced a schema-valid question batch, how often it did
so with no repair round, whether it obeyed "write exactly N questions", how often it marked
the genuinely correct option on checkable science and maths, and the median call time — with
a zero score for any model that does not fit your GPU.

Measured here on an 8 GB RTX 4060, eight cases each:

| Model | Size | Context | On GPU | Schema | Counting | Facts | Median | Score |
|---|---|---|---|---|---|---|---|---|
| **`qwen3:8b`** | 5.2 GB | 8,192 | yes | 8/8 | 8/8 | 8/8 | 11.1s | **0.96** |
| `qwen3.5:4b-q8_0` | 5.3 GB | 8,192 | yes | 7/8 | 6/8 | 7/7 | 8.9s | 0.93 |
| `qwen3.5:4b` | 3.4 GB | 16,384 | yes | 6/8 | **1/8** | 5/5 | 4.0s | 0.91 |
| `granite4.2:8b` | 5.3 GB | 8,192 | **no** | **0/8** | 0/8 | — | 174.4s | 0.00 |

Two results worth drawing out, because neither is visible on a leaderboard:

- **Counting is where small models fail.** `qwen3.5:4b` is a newer generation and four times
  faster, but it obeyed "write exactly N questions" once in eight tries. The blueprint engine
  asks for a precise number per cell, so under-production silently leaves topics unassessed —
  a 4B model is fast at producing a paper that does not cover the syllabus.
- **Nominal size does not tell you whether a model fits.** `granite4.2:8b` is 5.3 GB, no bigger
  than the winner, but it is a hybrid Mamba/transformer and needs more memory per token. Ollama
  put 8% of it on the CPU, and it went from unusable-slow to failing every schema call. The app
  detects this after loading and drops the context until it fits.

Run it yourself before switching — the right model depends on your card, and a model that
writes a wrong answer key faster is not an upgrade.

### Switching model

```bash
ollama pull <model>
```

Then pick it in **Settings**, or set `LECTERN_LLM_MODEL` in `.env`. The context window resizes
itself to whatever keeps the new model wholly on your GPU, so there is nothing else to tune.

Models larger than your card cannot be fixed by configuration. On 8 GB, that rules out the
12B class outright and makes the 9B class marginal. If you want a 9B, the vision weights in
Ollama's build push it over the line — use a text-only GGUF from Hugging Face instead, which
Ollama can pull directly:

```bash
ollama pull hf.co/unsloth/Qwen3.5-9B-GGUF:IQ4_XS
```

Nominal size is not the whole story either: a hybrid Mamba model needs more memory per token
than a transformer of the same size, so two 5.3 GB models can behave differently. The app
measures where the model actually landed after loading and steps the context down if any of
it went to the CPU, rather than trusting the estimate.

## Optional extras

The app starts and degrades feature by feature rather than failing. The Settings page shows exactly
what is missing and the command to install it.

```bash
pip install -e "backend[ingest]"    # Docling + ChromaDB + embeddings — PDF/DOCX upload and search
pip install -e "backend[media]"     # matplotlib + Pillow — charts and figures
npm install -g @mermaid-js/mermaid-cli   # diagrams on slides
```

> **If you have a GPU, do not run those commands bare.** Docling requires `torch>=2.2.2` with
> no upper bound, so pip will cheerfully replace a working CUDA build with the latest CPU-only
> wheel — no error, no warning, and indexing silently drops to a sixth of its speed. Pin it:
>
> ```bash
> pip install torch --index-url https://download.pytorch.org/whl/cu121
> python -c "import torch; print(f'torch=={torch.__version__}')" > constraints.txt
> pip install -e "backend[ingest]" -c constraints.txt \
>     --extra-index-url https://download.pytorch.org/whl/cu121
> python -c "import torch; print(torch.cuda.is_available())"   # must print True
> ```
>
> The installer does all of this for you, including the check at the end.

Without `[ingest]` you can still generate from a topic; you just cannot upload a PDF.

---

## Project layout

```
backend/app/
  schemas/      Pydantic models — the contract between the model and every renderer
  llm/          Provider abstraction (Ollama, OpenAI-compatible, fake) + structured output
  ingest/       parse → chunk → embed → retrieve, with page-level provenance
  generators/   One module per artifact kind, plus the exam blueprint engine
  render/       Pure functions to pptx, docx, pdf, markdown, html, Moodle, QTI, Anki
  services/     Generation and export orchestration
  jobs/         In-process async worker pool with SSE progress
  api/v1/       FastAPI routes
  cli/          The `lectern` command
frontend/src/   React + Vite + Tailwind
templates/      Typst document templates

install.ps1     The installer. The only supported way in.
install.sh      Same, for macOS and Linux.
tasks.ps1       What the installer calls underneath, plus the developer commands.
tasks.sh        Same, for macOS and Linux.
Dockerfile      Used for hosted deployments and by CI. Not an install path.
```

---

## Working on it

Everything above is for using Lectern. This part is for changing it.

```bash
git clone https://github.com/maliijaz/lectern
cd lectern
./tasks.sh setup          # .\tasks.ps1 setup on Windows
./tasks.sh dev            # API on :8000, web UI on :5173, both hot-reloading
./tasks.sh test           # the suite
./tasks.sh lint           # ruff, then the frontend typecheck
```

`tasks.sh` and `tasks.ps1` are the same commands on either platform, and they are what the
installer calls underneath. They are not a second way to install it — they assume a
checkout you are editing.

218 tests covering schema repair rules, the blueprint engine, the independent answer-key check,
GPU context sizing against measured VRAM boundaries, transient-failure retry, every renderer, the
ingestion pipeline, and the full generate → edit → export → download path over HTTP. They use a
stub provider, so the suite needs no model, no GPU and no network, and runs in about seven seconds.

---

## A note on the grading assistant

Machine marking is offered as a **first pass, never a verdict**. It marks only against a rubric or
mark scheme you supply, it quotes the student's own words as evidence for every judgement, and it
forces a teacher review whenever confidence is low, evidence is thin, or the score lands near a grade
boundary. Nothing reaches a student without you confirming it.

---

## License

MIT — see [LICENSE](LICENSE).

Built entirely on open-source components: [Docling](https://github.com/docling-project/docling),
[Ollama](https://ollama.com), [ChromaDB](https://www.trychroma.com),
[sentence-transformers](https://sbert.net), [python-pptx](https://python-pptx.readthedocs.io),
[python-docx](https://python-docx.readthedocs.io), [Typst](https://typst.app),
[genanki](https://github.com/kerrickstaley/genanki), [FastAPI](https://fastapi.tiangolo.com) and
[React](https://react.dev).
