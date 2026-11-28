"""The computer types the suggestions distinguish, and how one is chosen from what was detected."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class HardwareProfile(StrEnum):
    """The computer types the model suggestions distinguish, from weakest to strongest."""

    CPU = "cpu"
    """No graphics card worth using: models run on the processor."""

    CARD_8GB = "8gb"
    """A graphics card with about 8 GB of memory."""

    CARD_16GB = "16gb"
    """A graphics card with 16 GB of memory or more."""


class HardwareProfileSetting(StrEnum):
    """What the ``hardware_profile`` setting holds: look at the computer, or a profile chosen."""

    AUTO = "auto"
    """Look at this computer, and at the Ollama server when it runs elsewhere."""

    CPU = HardwareProfile.CPU.value
    CARD_8GB = HardwareProfile.CARD_8GB.value
    CARD_16GB = HardwareProfile.CARD_16GB.value


#: The three hardware tiers the GUI distinguishes, ordered from weakest to strongest.
HARDWARE_PROFILES: tuple[HardwareProfile, ...] = tuple(HardwareProfile)


#: Values accepted for the ``hardware_profile`` setting (auto-detect plus the tiers).
PROFILE_SETTINGS: tuple[HardwareProfileSetting, ...] = tuple(HardwareProfileSetting)


#: Share of a graphics card's memory that models can use: the rest holds the operating
#: system's own windows and the driver's buffers (measured on an 8 GB card, where a model, its
#: context and the driver's buffers used 6.9 of 8.2 GB).
CARD_USABLE_SHARE = 0.85


#: Memory (GB) of the graphics card a hardware profile stands for when the real size is unknown
#: (a profile chosen by hand, a graphics card whose memory could not be read, a remote server).
NOMINAL_CARD_GB: dict[HardwareProfile, float] = {
    HardwareProfile.CPU: 0.0,
    HardwareProfile.CARD_8GB: 8.0,
    HardwareProfile.CARD_16GB: 16.0,
}


#: Largest model (GB) that is comfortable on the processor alone, without a graphics card.
#: "Comfortable" means a page of about 2,000 to 4,000 answer tokens in under five minutes on an
#: ordinary processor.  Measured with ``verify_model_catalog fit --cpu-only`` on a Ryzen 7 5800H
#: (the slower of two machines): ``gemma4:e4b`` (5.0 GB) 11 tokens/s, about 3 minutes a page;
#: ``qwen3.5:4b`` and ``ministral-3:3b`` (4.4 GB) 11 to 16, 4 to 5; ``qwen3.5:9b`` (7.2 GB) 5.7,
#: 11 minutes.  So the limit lies between 5.0 and 7.2 GB; the next measured model above 5.0 is
#: too slow, and a school computer is probably slower than the Ryzen, so the limit stays at the
#: largest model measured to be comfortable.
CPU_COMFORT_GB = 5.0


#: Main memory kept free for the operating system and the app: the larger of this many GB and
#: ``SYSTEM_RESERVE_SHARE`` of the total.  Swapping to disk is never counted as memory.
SYSTEM_RESERVE_MIN_GB = 8.0


SYSTEM_RESERVE_SHARE = 0.20


#: Share of a model that sits on the graphics card, from which on it still runs well: a third
#: slower at most, and below :data:`SLOWER_SHARE` "very slow" (measured on an 8 GB card: a share
#: of 0.97 gave 43 tokens per second, 0.73 gave 13, 0.66 gave 6.6; a page takes under a minute at
#: the top and about seven minutes at the bottom).  Between the two it is "slower".
FULL_SPEED_SHARE = 0.9


SLOWER_SHARE = 0.7


#: Free main memory (GB) a run keeps beyond what the model needs from it, so the computer stays
#: responsive.  Checked against the memory that is free *now*, not the total.
RUN_MEMORY_MARGIN_GB = 2.0


# ── hardware detection thresholds ─────────────────────────────────────────────

#: GPUs reporting >= this many GB count as "16 GB or more" (16 GB cards report ~15.5+ GiB).
GPU_16GB_THRESHOLD_GB = 15.0


#: GPUs reporting >= this many GB count as "about 8 GB" (8 GB cards report 7.6-8.0 GiB).
GPU_8GB_THRESHOLD_GB = 7.0


#: Share of unified memory macOS leaves available for GPU workloads by default.
APPLE_GPU_MEMORY_SHARE = 0.66


def system_reserve_gb(system_memory_gb: float) -> float:
    """Return the main memory (GB) kept free for the system: the larger of 8 GB and 20 %."""
    return max(SYSTEM_RESERVE_MIN_GB, SYSTEM_RESERVE_SHARE * system_memory_gb)


def card_budget_gb(profile: HardwareProfile) -> float:
    """Return the graphics memory (GB) models can use when the real size is not known.

    The size the profile stands for (:data:`NOMINAL_CARD_GB`), times
    :data:`CARD_USABLE_SHARE`.  A computer typed ``"cpu"`` has no card this figure could
    stand for, so it is 0 — a real card of such a computer arrives as a known ``card_gb``.
    """
    return CARD_USABLE_SHARE * NOMINAL_CARD_GB[profile]


def comfortable_model_gb(profile: HardwareProfile) -> float:
    """Return the largest model (GB) the app runs well on a computer of *profile*.

    On a card it is the card's usable memory at which :data:`FULL_SPEED_SHARE` of the model still
    sits on it; without a card it is :data:`CPU_COMFORT_GB`.
    """
    if profile is HardwareProfile.CPU:
        return CPU_COMFORT_GB
    return card_budget_gb(profile) / FULL_SPEED_SHARE


def memory_for_models_gb(
    profile: HardwareProfile,
    system_memory_gb: float | None,
    card_gb: float | None = None,
) -> float | None:
    """Return the memory (GB) a model may need in total on this computer, or ``None``.

    The usable graphics memory plus the main memory that stays after the system's reserve.
    ``None`` when the main memory is unknown (a server on another computer).
    """
    if system_memory_gb is None:
        return None
    card = card_gb if card_gb is not None else card_budget_gb(profile)
    return card + max(0.0, system_memory_gb - system_reserve_gb(system_memory_gb))


def ram_needed_gb(
    memory_gb: float,
    profile: HardwareProfile,
    card_gb: float | None = None,
    *,
    unified: bool = False,
) -> float:
    """Return the main memory (GB) a model of *memory_gb* takes on this computer.

    What does not fit on the graphics card; all of it when there is no card, and on Apple
    Silicon, where the graphics share is carved out of the same memory.
    """
    if unified:
        return memory_gb
    card = card_gb if card_gb is not None else card_budget_gb(profile)
    return max(0.0, memory_gb - card)


def card_share(memory_gb: float, card_gb: float) -> float:
    """Return the share (0 to 1) of a model of *memory_gb* that fits on a card of *card_gb*."""
    return min(1.0, card_gb / memory_gb) if memory_gb > 0 else 1.0


def runs_at_full_speed(
    memory_gb: float, card_gb: float, *, share: float = FULL_SPEED_SHARE
) -> bool:
    """Return whether a model of *memory_gb* still runs at full speed on a card of *card_gb*.

    The one rule the app and the evaluation tools share: at least *share* of the model has to sit
    on the card.  A share of exactly *share* counts as full speed.
    """
    return card_share(memory_gb, card_gb) >= share


def is_very_slow(memory_gb: float, card_gb: float) -> bool:
    """Return whether a model that does not fit the card runs very slowly (see below 0.7)."""
    return card_share(memory_gb, card_gb) < SLOWER_SHARE


# ── hardware profile detection ────────────────────────────────────────────────


class DetectionReason(StrEnum):
    """Why a hardware profile was chosen — drives the GUI status text."""

    MANUAL = "manual"
    GPU_DETECTED = "gpu_detected"
    APPLE_UNIFIED_MEMORY = "apple_unified_memory"
    NO_GPU_FOUND = "no_gpu_found"
    GPU_MEMORY_UNKNOWN = "gpu_memory_unknown"
    REMOTE_SERVER = "remote_server"


@dataclass(frozen=True)
class HardwareDetection:
    """The resolved hardware profile plus everything needed to explain it."""

    profile: HardwareProfile
    reason: DetectionReason
    #: Name of the GPU that decided the profile, when one was found.
    gpu_name: str | None = None
    #: What the user's computer *has*, in GB: dedicated graphics memory of the
    #: deciding GPU, or the total shared memory on Apple Silicon.  The profile is
    #: derived from this value (Apple scales it by :data:`APPLE_GPU_MEMORY_SHARE`
    #: first), but it is never replaced by the scaled figure.
    memory_gb: float | None = None
    #: Host name of the Ollama server for REMOTE_SERVER, otherwise ``None``.
    host: str | None = None
    #: Memory (RAM) of the computer that runs the models, in GB; ``None`` when unknown
    #: (a server on another computer) or already counted in the profile (Apple Silicon).  Lets
    #: models too big for the graphics card be rated "slower" instead of "too large".
    system_memory_gb: float | None = None
    #: Names of the further graphics cards found, when there was more than one.  The largest
    #: decides the profile; the others are only named, so a teacher who knows their card sees
    #: that it was found.
    other_gpu_names: tuple[str, ...] = ()

    @property
    def card_gb(self) -> float | None:
        """Graphics memory (GB) models can use on this computer, or ``None`` when not known.

        ``None`` (a profile chosen by hand, a card whose memory could not be read, a remote
        server) makes the rating assume the size the profile stands for.  Apple Silicon uses
        the share of the unified memory macOS gives the graphics side.  A card smaller than
        7 GB gives the profile ``"cpu"``, but the card is still there and still decides how
        fast a model runs, so its size is reported whatever the profile.
        """
        if self.memory_gb is None:
            return None
        if self.reason is DetectionReason.APPLE_UNIFIED_MEMORY:
            return self.memory_gb * APPLE_GPU_MEMORY_SHARE
        if self.reason is DetectionReason.GPU_DETECTED:
            return self.memory_gb * CARD_USABLE_SHARE
        return None


@dataclass(frozen=True)
class LocalHardware:
    """Raw detection results for *this* computer (independent of any Ollama URL)."""

    #: ``(name, memory_gb_or_None)`` per detected GPU.
    gpus: tuple[tuple[str, float | None], ...] = ()
    #: Total unified memory (GB) on Apple Silicon, ``None`` elsewhere.
    apple_unified_memory_gb: float | None = None
    #: Memory (RAM) of this computer in GB, ``None`` when it could not be read.
    system_memory_gb: float | None = None


def parse_profile_setting(value: object) -> HardwareProfileSetting | None:
    """Return the setting *value* spells (case and surrounding spaces folded), else ``None``."""
    try:
        return HardwareProfileSetting(str(value).strip().lower())
    except ValueError:
        # A value the setting does not accept leaves the choice to the app.
        return None


def profile_setting(value: object) -> HardwareProfileSetting:
    """Return the setting *value* spells; a value the setting does not accept is ``"auto"``."""
    return parse_profile_setting(value) or HardwareProfileSetting.AUTO


def concrete_profile(setting: object) -> HardwareProfile | None:
    """Return the profile *setting* names, or ``None`` when it leaves the choice to the app.

    ``None`` covers ``"auto"`` and any value the setting does not accept.
    """
    parsed = parse_profile_setting(setting)
    if parsed is None or parsed is HardwareProfileSetting.AUTO:
        return None
    return HardwareProfile(parsed.value)


#: The profile assumed before this computer (or the Ollama server) has been looked at.
UNKNOWN_PROFILE = HardwareProfile.CARD_8GB


def active_profile(detection: HardwareDetection | None, setting: object) -> HardwareProfile:
    """Return the profile the model suggestions follow.

    A profile chosen by hand decides at once: the teacher's choice must not wait for a detection
    that may still be running, and a detection of another computer must not override it.
    *detection* decides only when *setting* leaves the choice to the app; before it lands, the
    profile of an 8 GB card is assumed.
    """
    chosen = concrete_profile(setting)
    if chosen is not None:
        return chosen
    return detection.profile if detection is not None else UNKNOWN_PROFILE


def _profile_for_memory_gb(memory_gb: float) -> HardwareProfile:
    """Map a known GPU memory size to a profile using the documented thresholds."""
    if memory_gb >= GPU_16GB_THRESHOLD_GB:
        return HardwareProfile.CARD_16GB
    if memory_gb >= GPU_8GB_THRESHOLD_GB:
        return HardwareProfile.CARD_8GB
    return HardwareProfile.CPU


def choose_profile(
    *,
    gpus: Sequence[tuple[str, float | None]],
    apple_unified_memory_gb: float | None,
    ollama_is_remote: bool,
    host: str | None = None,
    system_memory_gb: float | None = None,
) -> HardwareDetection:
    """Derive the hardware profile from raw detection results.

    Rules (first match wins):

    1. Ollama runs on another computer → ``8gb`` / :attr:`DetectionReason.REMOTE_SERVER`
       (the local GPU is irrelevant; 8 GB is the most common home-server setup).
    2. At least one GPU with known memory → the **largest** single GPU decides:
       ≥ 15 GB → ``16gb``, ≥ 7 GB → ``8gb``, otherwise ``cpu``.
    3. GPU(s) found, but no memory value → ``8gb`` / ``GPU_MEMORY_UNKNOWN``.
    4. Apple Silicon: effective GPU share = 0.66 x RAM, same thresholds; the
       reported memory is the total RAM.
    5. Nothing found → ``cpu`` / :attr:`DetectionReason.NO_GPU_FOUND`.

    Args:
        gpus: ``(name, memory_gb_or_None)`` per detected GPU.
        apple_unified_memory_gb: Total unified memory (GB) on Apple Silicon, or ``None``.
        ollama_is_remote: True when the configured Ollama URL is not this machine.
        host: Host name of the remote Ollama server (only used for REMOTE_SERVER).
        system_memory_gb: Memory (RAM) of this computer, carried into the result; it says
            nothing about a remote server and is ignored for it.  Apple Silicon ignores it
            too: its graphics share is part of that same memory, so it cannot be offloaded
            into a second time.
    """
    if ollama_is_remote:
        return HardwareDetection(
            profile=HardwareProfile.CARD_8GB, reason=DetectionReason.REMOTE_SERVER, host=host
        )

    known = [(name, mem) for name, mem in gpus if mem is not None]
    if known:
        best = max(range(len(known)), key=lambda index: known[index][1])
        name, mem = known[best]
        return HardwareDetection(
            profile=_profile_for_memory_gb(mem),
            reason=DetectionReason.GPU_DETECTED,
            gpu_name=name,
            memory_gb=mem,
            system_memory_gb=system_memory_gb,
            other_gpu_names=tuple(other for i, (other, _mem) in enumerate(known) if i != best),
        )

    if gpus:
        return HardwareDetection(
            profile=HardwareProfile.CARD_8GB,
            reason=DetectionReason.GPU_MEMORY_UNKNOWN,
            gpu_name=gpus[0][0],
            memory_gb=None,
            system_memory_gb=system_memory_gb,
            other_gpu_names=tuple(name for name, _mem in gpus[1:]),
        )

    if apple_unified_memory_gb is not None:
        # The profile uses the share macOS gives the GPU, but the detection
        # reports the memory the computer actually has ("16 GB", not "10.6 GB").
        effective = APPLE_GPU_MEMORY_SHARE * apple_unified_memory_gb
        return HardwareDetection(
            profile=_profile_for_memory_gb(effective),
            reason=DetectionReason.APPLE_UNIFIED_MEMORY,
            memory_gb=apple_unified_memory_gb,
        )

    return HardwareDetection(
        profile=HardwareProfile.CPU,
        reason=DetectionReason.NO_GPU_FOUND,
        system_memory_gb=system_memory_gb,
    )
