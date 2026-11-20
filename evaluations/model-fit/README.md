# Model sizes: what each model needs, measured on the card

`OllamaModelSpec.memory_gb` and `context_gb_per_8k` in
`teachers_teammate/domain/model_catalog.py` say how much a model needs at 8,192 tokens and how much
it grows per further 8,192. They were estimates, or figures from an 8 GB NVIDIA card. This folder
holds the measurements that replace them: `sizes.jsonl`, one JSON object per line, append-only.

Numbers only — no text, no model output — as `evaluations/README.md` asks. The file appears with
the first measurement; there is no empty one, because an empty file says nothing and a missing one
says "not measured yet".

## The record

```json
{"format": 3, "measured_at": "2026-11-13T19:04:11Z", "machine": "rx9070xt-server",
 "device": "gpu", "ollama_version": "0.40.15", "tool_commit": "abc1234",
 "tag": "gemma4:e4b", "digest": "sha256:…", "num_ctx": 16384,
 "answer_tokens": 172, "generation_seconds": 2.4,
 "options": {"temperature": 1.0, "num_ctx": 16384}, "load_seconds": 4.2,
 "ollama": {"context_length": 16384, "size_bytes": 6012345678, "size_vram_bytes": 6012345678},
 "card": {"agent_sha256": "…", "source": "sysfs", "total_bytes": 17095983104,
          "idle_bytes": 715128832, "peak_bytes": 7301234688, "end_bytes": 7299137536},
 "ram": {"total_bytes": 33285996544, "idle_available_bytes": 30100000000,
         "min_available_bytes": 29200000000}}
```

| Field | Meaning |
| --- | --- |
| `format` | The layout of the line (3 since the machine and the device). A reader refuses a number it does not know instead of guessing. |
| `measured_at` | When the request ran, UTC. |
| `machine` | The id of the machine in `evaluations/machines/` (`--machine`); see the table below. |
| `device` | What the model ran on: `gpu` for the card, `cpu` for the processor (`--cpu-only`). A size measured on the card says nothing about the processor, so the two are never pooled. |
| `ollama_version` | The server's version. A different version is a different backend, so a record only matches a run of the same version, and a report marks a record of another version. |
| `tool_commit` | The commit of this repository that made the record. |
| `tag`, `digest` | The model, and the build of it. A tag that is pushed again gets a new digest, and its old records no longer match. |
| `num_ctx` | The window the request asked for. |
| `answer_tokens`, `generation_seconds` | The request's answer (Ollama's `eval_count`) and the time the model took to write it (`eval_duration`, load excluded). The speed is their quotient, computed when read; an answer under 20 tokens gives none. |
| `options` | The options the request sent, i.e. the app's own proofreading request at that window. |
| `load_seconds` | `ChatStats.load_duration` of the request: what a reload costs. |
| `ollama.context_length` | The window Ollama really loaded (`/api/ps`). Smaller than `num_ctx` when the model's own maximum is smaller, and then the ladder stops. |
| `ollama.size_bytes`, `size_vram_bytes` | Ollama's own figures. `size_vram` below `size` means part of the model is in main memory. |
| `card.idle_bytes` | The card's use after the server was emptied and had settled, before the load. |
| `card.peak_bytes` | The highest sample while the window was open. |
| `card.end_bytes` | The sample at close, with the model still loaded. |
| `card.total_bytes` | The card's total, so a peak can be read as a share of it. |
| `ram.*` | Main memory: total, available when idle, and the lowest available during the window. |

`card` and `ram` are `null` for a record made with `--no-agent`.

**Units.** Every byte figure is a raw byte count, as the agent and Ollama report them. The reports
divide by 10⁹ for GB, the same unit as the catalog's sizes, so a figure can be compared with
`memory_gb` without a conversion.

**Nothing derived is stored.** The card's increase (`peak - idle`), the need
(`max(ollama size, card increase)`), the growth per 8k and the tier windows are computed when the
file is read. A change of the rule then costs no new measurement.

## The machines

A machine is not a device. `rx9070xt-server` is one machine, measured on its card and on its
processor; those are two devices of one machine, and the records keep them apart. The registry is
`evaluations/machines/`, one JSON file per machine, and `machine` is an id from it: a typo is
refused instead of becoming a second machine that nobody can compare with the first.

| Id | Card | Notes |
| --- | --- | --- |
| `rx9070xt-server` | AMD RX 9070 XT, 16 GB | The headless Linux server; ROCm, and the agent reads the card from `sysfs`. The reference measurement. |
| `rtx3070-laptop` | NVIDIA RTX 3070 Laptop, 8 GB | The Windows host of the development WSL; CUDA, and the agent runs on Windows, because WSL's `nvidia-smi` reports 0 MiB used. A normal desktop session is open, which is what `CARD_USABLE_SHARE` stands for. |

The processor of each machine is in its file, with the OS, the driver stack and the two Ollama
variables (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`): Ollama's API cannot report them, and a
difference between two backends is only readable with them.

The files also carry `aliases`, the labels the records used before the registry (`rx9070xt`,
`rtx-8gb-win`, `rx9070xt-cpu`, `rtx-laptop-cpu`). They are documentation, not ids: a command line
that names one is refused like any other unknown machine.

## Reading the file

```bash
bazel run //tools:verify_model_catalog -- fit --report
bazel run //tools:verify_model_catalog -- fit --report --compare rx9070xt-server rtx3070-laptop
bazel run //tools:verify_model_catalog -- fit --report --catalog
```

`--report` prints, per model, machine and device, the need at each window, Ollama's figure next to
the card's increase, the growth per 8k, and the largest window that still fits a card of a given
size (`--fit-limit`, default `8gb` and `16gb`). A record whose `ollama_version` is not the version
the machine runs now is marked as measured on another backend. `--compare` puts two machines side
by side and marks each figure that disagrees. `--catalog` prints the two values to enter by hand,
taking the larger of two machines that disagree.

A step that spills — Ollama's `size_vram` below `size`, or the card's peak within 2 % of its total
— is kept and flagged: it is a real figure, but its need is a lower bound, so it does not feed the
growth.

## Adding a measurement

```bash
bazel run //tools:verify_model_catalog -- fit --machine rx9070xt-server \
  --ollama-url http://HOST:11434 --gpu-agent http://HOST:11436 \
  --gpu-token-file ~/.config/gpu_agent.token --tag TAG
```

The command empties the server before every step, waits for the card to settle, opens a window,
sends one short request at the app's own proofreading options, reads `/api/ps`, closes the window
and appends the record. A step that is already stored for the same build and version is skipped;
`--again` measures it again. Commit the new lines with a message that names the machine and the
date.

For the speed of the processor alone, add `--cpu-only`: one window at 8,192 tokens with `num_gpu`
0, stored as the same machine's `cpu` device. The agent is still asked, and its card reading must
stay at the idle figure — the line warns `CARD USED` when it does not.

```bash
bazel run //tools:verify_model_catalog -- fit --cpu-only --machine rx9070xt-server \
  --ollama-url http://HOST:11434 --gpu-agent http://HOST:11436 --tag TAG
```
