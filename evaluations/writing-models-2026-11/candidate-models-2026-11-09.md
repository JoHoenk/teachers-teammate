# Writing-model candidates: what was looked at, what was dropped, and why

Research date: 2026-11-09. Written for the developers who decide which models go into the next
comparison run (gate 1 and gate 2, see `docs/development.md`, *Choosing writing models*).

**Scope.** Models that proofread learner text (German, English, French) and answer the app's
review format, run through Ollama on a 16 GB graphics card or less. Reading (OCR) models are not
covered. Sizes are the download sizes on the Ollama library pages of 2026-11-09 (a Hugging Face
build: the repository's file list). Memory needs at the app's 8192-token window are not listed
for the new models because nobody has measured them yet; as a rule of thumb they are the download
size plus 1.5 to 2 GB.

**No published result ranks these models on correcting German or French learner text.** What the
list rests on is the model's family, size, release date and, for the models already run, our own
measurements. The test run decides.

## How the candidates were chosen

1. **General instruction-following models only.** The app asks for a JSON answer with
   explanations and thinking switched off.
2. **The newest generation of each family** (instruction of 2026-11-09). An older generation is
   not "bad": it is left out because its successor exists. Models we measured stay in the
   comparison as the baseline.
3. **Fits the tier.** Computers without a graphics card up to about 5 GB, 8 GB cards up to about
   7 GB, 16 GB cards up to about 14 GB (the app counts 85 % of the card; larger models spill onto
   the processor and get slow, see `domain/model_catalog.py`).
4. **Available**: an official Ollama tag, or a Hugging Face GGUF build that Ollama can pull.
5. **Searched by family name** (Gemma, Qwen, Mistral, Granite, Nemotron, LFM, Phi, Aya, EuroLLM,
   Apertus, Meta) and by the 20 newest entries of the Ollama library, not by a systematic sweep of
   all of it. Categories not searched are listed at the end.

## 1. Already measured (2026-11-07, 8 GB card, official tags)

Mean F0.5 over German, English and French in gate 2,
at temperature 0.2 unless noted. Gate 1 is the structured-output check (15 requests). The *Smoke*
column is the per-setup smoke check, replayed on the saved answers: five of gate 1's fixtures, one run each, and a
setup is rejected only when the same fixture fails twice in a row.

| Model | Gate 1 | Smoke | Gate 2 mean F0.5 | Status | Reason |
| --- | --- | --- | --- | --- | --- |
| `gemma4:12b` | 15/15 | PASS | 0.628 | **Baseline for 16 GB** | Best quality; does not fit an 8 GB card (13 tokens/s) |
| `gemma4:e4b` | 15/15 | PASS | 0.612 | **Baseline for 8 GB (current default)** | Within 0.02 of the 12B at about a third of the time |
| `qwen3.5:9b` | 14/15 | **FAIL** (collapse) | 0.527 (0.440 at 0.7) | Baseline, weaker | Leaves fewer correct sentences alone; wrong or vague French explanations in the AI reading |
| `qwen3:4b` | 14/15 | PASS | 0.486 (at 0.7) | Current no-card model | Only measured model of its size; replacement candidate below |
| `ministral-3:14b` | 15/15 | PASS | 0.434 | **Back in the screen (2026-11-11)** | Was dropped for the scores below; no model is left out for scores or speed before all are rated. Adds `**bold**` marks in 17 of 27 texts (ignored in scoring), French weak (0.56), about 4 times as slow as `gemma4:12b` here |
| `ministral-3:8b` | 14/15 | **FAIL** (placement) | 0.411 | **Back in the screen (2026-11-11)** | Was dropped for the lowest German and English scores of the family; same marks |
| `gpt-oss:20b` | 11/15 | **FAIL** (runaway) | not run | **Dropped** | Reasoning model: 27,000 characters of hidden reasoning, answer cut off at the window limit on a full page; one crash of the Ollama server on Windows (a CUDA error, not an answer problem) |
| `unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL` | 11/15 | not replayed | not run | **Dropped** | Ships none of the official tag's sampling settings; 4 failed runs |

## 2. To be tested (the screen)

One standard build per model, gate 1 at temperature 0.2 first; models that fail gate 1 are not
carried into gate 2. The *Tag to pull* column is the argument for `ollama pull`. The *Smoke*
column is filled by `verify_model_catalog smoke` before the eighteen runs: a setup that fails it is
not sent to gate 1, and one that passes it is continued from its own answers.

| Tier | Model | Tag to pull | Smoke | Download | Why it is in | Open points |
| --- | --- | --- | --- | --- | --- | --- |
| no card | Qwen 3.5 4B | `qwen3.5:4b` | — | 3.3 GB | Successor of the measured `qwen3:4b`; same family as the default Qwen | Qwen 3.5 9B scored below Gemma 4 |
| no card | Gemma 4 E2B | `gemma4:e2b` | — | 4.6 GB | Gemma 4 was the strongest family measured | Smallest member; quality unknown |
| no card | Ministral 3 3B | `ministral-3:3b` | — | 3.0 GB | Mistral's newest small model | The larger Ministrals were weak: low priority |
| no card | Granite 4.1 3B (IBM) | `granite4.1:3b` | — | 2.1 GB | New family, small | German/French support **not verified** |
| no card | LFM 2.5 8B (Liquid AI) | `lfm2.5:8b` | — | 5.2 GB | Mixture-of-experts with about 1B active parameters (from the tag `8b-a1b`; the page does not say), so it may be fast without a card | Language support and licence **not stated** on the page |
| 8 GB | EuroLLM 9B (Dec 2025) | `hf.co/mradermacher/EuroLLM-9B-Instruct-2512-GGUF:Q4_K_M` | — | 5.6 GB | Trained in Europe for all EU languages | Community build: check the sampling settings and the licence on the model card (Apache 2.0 is stated for the 22B only) |
| 8 GB | Granite 4.1 8B | `granite4.1:8b` | — | 5.3 GB | Official tag, new family | As for the 3B |
| 8 GB | Apertus 1.5 8B (Swiss) | `hf.co/Colby/apertus-v1.5-8b-text-Q4_K_M-GGUF` | — | 5.1 GB | Focused on German, French, Italian, English; Apache 2.0 | Only community builds; Ollama may not load it |
| 8 GB | Nemotron 3.5 Bolt E4B (NVIDIA) | `nemotron-3.5-bolt:e4b` | — | 7.9 GB | Newest small NVIDIA model | Built for coding and tool use; just fits: low priority |
| 16 GB | **EuroLLM 22B** | `hf.co/bartowski/utter-project_EuroLLM-22B-Instruct-2512-GGUF:IQ4_XS` | — | 12.3 GB | German, French, English are core languages; Apache 2.0 according to one catalog; the usual 4-bit `Q4_K_M` (13.7 GB) is too tight for 16 GB, `IQ4_XS` should fit | Community build; a smaller quantisation than usual; 32k context |
| 16 GB | LFM 2.5 24B | `lfm2.5:24b` | — | 15 GB | Mixture-of-experts with about 2B active parameters (from the tag `24b-a2b`), so a spill onto the processor costs little | As for the 8B |
| 16 GB (after the screen) | Gemma 4 12B at 8 bit, Gemma 4 E4B at 8 bit, Qwen 3.5 9B at 8 bit | `gemma4:12b-it-q8_0`, `gemma4:e4b-it-q8_0`, `qwen3.5:9b-q8_0` | — | 13 / 9.0 / 10 GB | Use the room a 16 GB card has: compression levels of the models that win | Compare only for the top two or three, and only on an enlarged test set |
| stretch | Gemma 4 26B-A4B (QAT) | `gemma4:26b-a4b-it-qat` | — | 16 GB | Mixture-of-experts, about 4B active | Does not fully fit 16 GB; needs a lot of main memory for the rest |
| stretch | Qwen 3.6 35B-A3B | `qwen3.6:35b-a3b` | — | 23 GB | Newest Qwen with a size that works at all | Spills heavily; needs a lot of main memory |

Pulled onto the test server `gpu-server.example` (AMD Radeon RX 9070 XT with 16 GB, 31 GiB of main
memory, Linux, Ollama 0.40.15) so far (2026-11-09): chunk 1, the five no-card models; chunk 2 is
being pulled. The server already held `gemma4:e4b`, `gpt-oss:20b`, `qwen3.5:27b`, two Unsloth builds of
`Qwen3.5-27B`, `Ministral-3-14B` (IQ4_NL), `nemotron-3-nano:30b` and two reading models.

## 3. Dropped, and why

### Measured and weak

`gpt-oss:20b` and the Unsloth Qwen build: see section 1. (`ministral-3:8b` and `ministral-3:14b` were dropped here on 2026-11-09 and are back in the screen since 2026-11-11.)
An earlier deep dive on local proofreading models (a private working note, not committed) also found `mistral:latest`
(Mistral-7B-Instruct-v0.3) to leave almost no correct sentence alone (correct-text F1 0.022 in a
17-model study), TowerInstruct-7B weak in the same study (0.251), and Yi-1.5-9B at 0.047.

### An older generation exists

Left out on the instruction to use the newest generation, not because they were measured badly:
`gemma3` (12B, 27B), `gemma2`, `gemma3n`, `qwen3` (all sizes except the measured `qwen3:4b`),
`qwen2.5`, `mistral-nemo`, `mistral-small3.2` (24B, 15 GB), `phi4` and `phi4-mini` (2025), `llama3.x`
(no 2026 Llama release found), `aya-expanse` and `aya` (2024; Cohere's non-commercial licence).

One consequence: Qwen 3.5 has no size between 9B and 27B, so the newest Qwen at 16 GB is the 9B
at 8 bit. `qwen3:14b`, which the deep dive suggested for French, is the older generation.

### Too large for 16 GB as an ordinary (dense) model

A model that does not fit the card runs on the processor and gets very slow; the app measures
13 tokens/s for a model with 73 % on the card and 6.6 for 66 %.

| Model | Download |
| --- | --- |
| `qwen3.8:27b` (newest Qwen) | 18 GB |
| `qwen3.6:27b`, `qwen3.5:27b` | 17 to 18 GB |
| `gemma4:31b` | 19 to 20 GB |
| `muse-glimmer:30b` (Meta, Apache 2.0, dense; trained on 100+ languages, German and French not confirmed) | 18 GB |
| `granite4.1:30b` | 17 GB |
| `nemotron-3-nano:30b` | 24 GB |
| `mistral-large-4`, `mistral-medium-3.5` (128B), `muse-spark-open` (816B), `nemotron-3.5-super` (120B) | far beyond any single card |

`qwen3.8:27b` and `muse-glimmer:30b` are the newest and probably the strongest here. If a
24 GB machine or a Mac with plenty of memory is ever in scope, they are the first to try.

### Not for this task

| Model | Reason |
| --- | --- |
| `mimo-v2.6`, `nemotron-3.5-lightning`, `nemotron-cascade-2`, `flint` | Built for code and agents |
| `autotrust-jev`, `d1`, `d3`, `pplx-decider`, `nimble-v3`, `kev` | Decision and scoring models: they return a rating, not a corrected text |
| `lfm3` (1.7B), `lfm3-omni`, `lfm3-vl`, `lfm3-audio`, `phonon` | Agent, audio, speech or vision models |
| `embeddinggemma-2` | Embeddings |
| `translategemma` | Translation, based on Gemma 3 |
| `nemotron-3-nano:4b` | Agent model; no language information on the page |

### Compression formats that do not help

`nvfp4` is a 4-bit format that Nvidia's newest cards (RTX 50-series) are built to accelerate, and
`mlx` tags are for Apple computers; neither is stated on an Ollama page and both are general
knowledge, not checked here. Neither test machine benefits (an NVIDIA RTX 3070 laptop and an AMD
Radeon RX 9070 XT server), so they are not in the list.

### Not available yet

| Model | Status |
| --- | --- |
| Soofi S 30B-A3B (German/English, sovereign) | Paper of July 2026; weights "will be released" |
| Apertus 1.5 on Ollama | Official Hugging Face page is gated and lists no Ollama support; only community GGUF builds |
| EuroLLM 22B on Ollama | No official Ollama tag; community GGUF builds only |
| `Tiny Aya` (3.35B, Feb 2026) | Not in the Ollama library; non-commercial licence |

## 4. Not researched yet

1. **Grammar-correction fine-tunes.** Trained for exactly this task but returning corrected text
   only, so they fit text mode or a two-step design. The deep dive lists OmniGEC (German, English),
   `tartuNLP/leo-hessianai-7b-p1-llama-errors-p2-GEC` (German), `adeljebali/llama3.1-gec-strict`
   (French, no published scores) and `grammarly/medit-xl`. That list dates from before 2026 and has
   not been rechecked.
2. **Language-specific fine-tunes**, for French in particular, where the evidence is thinnest:
   Chocolatine, Lucie, CroissantLLM; for German the successors of SauerkrautLM, DiscoLM, Teuken.
3. **Translation models** (Tower+ and similar).
4. **Reading (OCR) models** for the 16 GB tier; the catalog's lists for 8 GB and 16 GB are equal
   there on purpose until a larger vision model is validated.

## 5. Things stated here that are not verified

* **Granite 4.1:** its German and French support was not checked on any source. Do not rely on it.
* **LFM 2.5, Nemotron 3.5 Bolt, Granite:** licences and supported languages were not read; the
  Ollama pages do not say. Check before any of them could ship.
* **EuroLLM and Apertus licences** (Apache 2.0) come from third-party catalogs and the Hugging Face
  page; confirm on the model cards.
* **EuroEval** (the European leaderboard) could not be read; no EuroEval figure is used.
* **Roundups** (morphllm, localaimaster, convly, taskade and similar) disagree with each other on
  dates and names. They were used to find names, never as evidence of quality.
* **Memory needs** of every new model are estimates until measured with `ollama ps` and
  `nvidia-smi`.
* **`IQ4_XS`** for EuroLLM 22B is an unusual quantisation; if it behaves oddly, test the 4-bit
  `Q4_K_M` on a machine with room for it before blaming the model.

## Sources

Ollama library pages, read 2026-11-09:
[gemma4](https://ollama.com/library/gemma4/tags), [qwen3.5](https://ollama.com/library/qwen3.5/tags),
[qwen3.6](https://ollama.com/library/qwen3.6/tags), [qwen3.8](https://ollama.com/library/qwen3.8/tags),
[qwen3](https://ollama.com/library/qwen3/tags), [qwen3-vl](https://ollama.com/library/qwen3-vl/tags),
[ministral-3](https://ollama.com/library/ministral-3/tags),
[mistral-small3.2](https://ollama.com/library/mistral-small3.2/tags),
[granite4.1](https://ollama.com/library/granite4.1/tags), [lfm2.5](https://ollama.com/library/lfm2.5/tags),
[nemotron-3.5-bolt](https://ollama.com/library/nemotron-3.5-bolt/tags),
[nemotron-3-nano](https://ollama.com/library/nemotron-3-nano/tags),
[muse-glimmer](https://ollama.com/library/muse-glimmer/tags), the
[newest entries](https://ollama.com/search?o=newest) and the
[gemma](https://ollama.com/search?q=gemma), [phi](https://ollama.com/search?q=phi),
[aya](https://ollama.com/search?q=aya), [mistral](https://ollama.com/search?q=mistral) and
[lfm](https://ollama.com/search?q=lfm) searches.

Papers and model pages:
[EuroLLM-22B technical report](https://arxiv.org/abs/2602.05879),
[EuroLLM-22B announcement](https://informatics.ed.ac.uk/news/latest-news/europe-takes-another-step-toward-ai-sovereignty-with-the-launch-of-eurollm-22b),
[bartowski GGUF builds of EuroLLM-22B](https://huggingface.co/bartowski/utter-project_EuroLLM-22B-Instruct-2512-GGUF),
[mradermacher GGUF builds of EuroLLM-9B](https://huggingface.co/mradermacher/EuroLLM-9B-Instruct-2512-GGUF),
[Apertus v1.5 8B](https://huggingface.co/swiss-ai/Apertus-v1.5-8B),
[Soofi S paper](https://arxiv.org/abs/2607.09424),
[Muse Glimmer architecture notes](https://www.sebastianraschka.com/blog/2026/muse-glimmer-30b-architecture-notes.html),
[Releasebot: Ollama updates](https://releasebot.io/updates/ollama).

Our own earlier material: an earlier deep dive on local proofreading models (a private working
note, not committed), and `docs/development.md`, *Choosing writing
models*.
