"""Labeled-corpus loader for detection-tuning skill.

Loads EVTX-ATTACK-SAMPLES (or another corpus) from a single parsed-CSV
file, scopes events to a Sigma rule's declared `logsource`, and applies
filename-keyword ground-truth labels.

Why CSV instead of EVTX parsing:
    EVTX-ATTACK-SAMPLES ships `evtx_data.csv` — every event from every
    EVTX file, parsed into a flat schema with the same column names
    Sigma rules reference (`CommandLine`, `Image`, `GrantedAccess`,
    `CallTrace`, `TargetImage`, …). Loading the CSV with pandas is
    ~100ms and gives us a clean evaluator surface; raw EVTX parsing
    would be ~minutes per scan and force us to choose a parser (libevtx
    / python-evtx / evtx-rs) without buying anything the CSV doesn't
    already provide.

Why logsource-aware scoping:
    A Sigma rule's `logsource: category: process_access, product: windows`
    declaration means the rule expects to run against process_access
    events (Sysmon EID 10). Evaluating the rule against the whole
    corpus inflates the negative count (all the routine system events
    that the rule legitimately ignores) and craters precision math.
    Pre-flight 3 discovered this by accident — see
    docs/journal/futures/detection-tuning-preflight.md for the trace.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


_HEX_PADDED = re.compile(r"^0x0+([0-9a-fA-F]+)$")
_HEX_ANY = re.compile(r"^0x[0-9a-fA-F]+$")


def _normalize_hex_cell(value: str) -> str:
    """Strip leading zeros from padded hex strings.

    ``0x00001038`` → ``0x1038``. EVTX parsers commonly emit padded hex
    for access masks; SigmaHQ rule authors write the canonical un-padded
    form. Substring matchers (``|contains``) require both sides on the
    same shape — without normalization, a rule for ``0x1038`` misses
    every event the corpus records as ``0x00001038``.
    """
    if not isinstance(value, str) or not value:
        return value
    m = _HEX_PADDED.match(value)
    if m:
        return "0x" + m.group(1).lower()
    if _HEX_ANY.match(value):
        return value.lower()
    return value


# Columns where hex normalization matters. These are the Sysmon /
# Security fields where access masks, type codes, etc. appear as
# padded hex in EVTX output and as canonical hex in Sigma rules.
_HEX_COLUMNS = ("GrantedAccess", "Keywords", "Hashes")


# Sigma's logsource taxonomy → (Channel pattern, EventID list) filter.
# Covers the categories present in EVTX-ATTACK-SAMPLES; expand here when
# rules from new categories enter the queue.
_LOGSOURCE_FILTERS: dict[tuple[str, str], tuple[str, list[str]]] = {
    ("process_creation", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["1"]),
    ("process_access", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["10"]),
    ("image_load", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["7"]),
    ("file_event", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["11"]),
    ("registry_event", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["12", "13", "14"]),
    ("network_connection", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["3"]),
    ("dns_query", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["22"]),
    ("process_termination", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["5"]),
    ("create_remote_thread", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["8"]),
    ("pipe_created", "windows"): ("Microsoft-Windows-Sysmon/Operational", ["17", "18"]),
}


@dataclass(frozen=True)
class LogsourceScope:
    """Materialized event subset for one Sigma rule's logsource declaration."""
    category: str
    product: str
    channel: str
    event_ids: tuple[str, ...]
    events: pd.DataFrame  # rows from the corpus matching this logsource


class UnsupportedLogsource(Exception):
    """The Sigma rule's logsource isn't in the corpus loader's mapping."""


class CorpusLoader:
    """Lazy-loaded labeled corpus for one detection-tuning run."""

    def __init__(self, csv_path: str | Path):
        self._csv_path = Path(csv_path)
        self._df: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._df is None:
            if not self._csv_path.is_file():
                raise FileNotFoundError(
                    f"Corpus CSV not found at {self._csv_path}. "
                    "Did pre-flight 1 actually run? "
                    "Expected EVTX-ATTACK-SAMPLES checkout with evtx_data.csv."
                )
            df = pd.read_csv(
                self._csv_path, low_memory=False, dtype=str
            ).fillna("")
            for col in _HEX_COLUMNS:
                if col in df.columns:
                    df[col] = df[col].apply(_normalize_hex_cell)
            self._df = df
        return self._df

    @property
    def total_events(self) -> int:
        return len(self._load())

    def scope_for_logsource(
        self, category: str, product: str = "windows",
    ) -> LogsourceScope:
        """Return the events subset matching a Sigma rule's logsource.

        Raises ``UnsupportedLogsource`` if the (category, product) pair
        isn't in the embedded mapping — the caller should classify this
        as a RULE_PARSE_FAILURE-style outcome and surface the gap.
        """
        key = (category, product)
        if key not in _LOGSOURCE_FILTERS:
            raise UnsupportedLogsource(
                f"No logsource mapping for category={category!r} product={product!r}. "
                "Add it to corpus_loader._LOGSOURCE_FILTERS if rules from this "
                "category need to enter the queue."
            )
        channel, event_ids = _LOGSOURCE_FILTERS[key]
        df = self._load()
        subset = df[
            (df["Channel"] == channel)
            & (df["EventID"].isin(event_ids))
        ].copy()
        return LogsourceScope(
            category=category,
            product=product,
            channel=channel,
            event_ids=tuple(event_ids),
            events=subset,
        )

    @staticmethod
    def label_positives(
        df: pd.DataFrame, positive_filename_keywords: Iterable[str],
    ) -> pd.Series:
        """Apply ground-truth labeling.

        Filename-keyword match against ``EVTX_FileName``. Sufficient for
        cred-dumping-style techniques where filenames are descriptive.
        Replace with a per-rule ATT&CK technique ID match if a future
        corpus's per-tactic labeling proves too coarse for recall-focused
        rules.

        ``SdsCorpus`` reuses this via the EVTX_FileName column it
        synthesizes from JSON source filenames — single labeling
        implementation, two corpora.
        """
        keywords = [k.lower() for k in positive_filename_keywords]
        if not keywords:
            return pd.Series(False, index=df.index)
        fn_lower = df["EVTX_FileName"].str.lower()
        return fn_lower.apply(lambda f: any(k in f for k in keywords))


class SdsCorpus:
    """Lazy-loaded labeled corpus for OTRF Security-Datasets captures.

    Sibling to ``CorpusLoader`` (which reads EVTX-ATTACK-SAMPLES'
    pre-parsed CSV). This one walks a directory of NDJSON files (the
    SDS native format), concatenates them into one DataFrame, and
    synthesizes an ``EVTX_FileName`` column from the source file
    basename so the shared ``label_positives`` static method works
    unmodified.

    Schema compatibility: SDS events come from real Sysmon, with the
    same ``Channel`` / ``EventID`` / ``CommandLine`` / ``GrantedAccess``
    / ``CallTrace`` / ``TargetImage`` columns the EVTX corpus has.
    The Evaluator doesn't need to know which corpus it's scoring
    against — both expose the same shape.

    The point of having a second corpus is to test the architectural
    claim that knowledge transfers across corpora — same Sigma rule
    will score differently on different telemetry sources, and a tip
    pool built against one corpus should help on a second. See
    docs/journal/futures/detection-tuning-skill.md "cross-run
    intelligence story" section.
    """

    def __init__(self, extracted_dir: str | Path):
        self._dir = Path(extracted_dir)
        self._df: pd.DataFrame | None = None

    def _load(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        if not self._dir.is_dir():
            raise FileNotFoundError(
                f"SDS extracted dir not found at {self._dir}. "
                "Expected unzipped OTRF/Security-Datasets captures "
                "(*.json NDJSON files)."
            )
        json_files = sorted(self._dir.glob("*.json"))
        if not json_files:
            raise FileNotFoundError(
                f"No .json files found in {self._dir}. "
                "Unzip the SDS captures first."
            )

        # NDJSON: one event per line. pandas read_json with lines=True
        # is the fastest path, but it doesn't expose source filename.
        # Read per-file so we can stamp EVTX_FileName for the label
        # matcher. ~3 seconds for the 128k-event credential_access +
        # execution subset.
        frames = []
        for jf in json_files:
            try:
                df = pd.read_json(jf, lines=True, dtype=False)
            except ValueError as exc:
                # Skip malformed files rather than failing the whole load —
                # SDS occasionally ships truncated captures.
                continue
            df["EVTX_FileName"] = jf.name
            frames.append(df)

        df = pd.concat(frames, ignore_index=True, sort=False)
        # Normalize types to match CorpusLoader: everything as string.
        # pandas leaves int columns as int from read_json; cast for
        # uniform downstream handling.
        df = df.astype(str).fillna("")
        for col in _HEX_COLUMNS:
            if col in df.columns:
                df[col] = df[col].apply(_normalize_hex_cell)
        self._df = df
        return df

    @property
    def total_events(self) -> int:
        return len(self._load())

    def scope_for_logsource(
        self, category: str, product: str = "windows",
    ) -> LogsourceScope:
        """Same contract as ``CorpusLoader.scope_for_logsource``."""
        key = (category, product)
        if key not in _LOGSOURCE_FILTERS:
            raise UnsupportedLogsource(
                f"No logsource mapping for category={category!r} "
                f"product={product!r}. Add it to corpus_loader."
                "_LOGSOURCE_FILTERS if rules from this category need to "
                "enter the queue."
            )
        channel, event_ids = _LOGSOURCE_FILTERS[key]
        df = self._load()
        subset = df[
            (df["Channel"] == channel)
            & (df["EventID"].isin(event_ids))
        ].copy()
        return LogsourceScope(
            category=category,
            product=product,
            channel=channel,
            event_ids=tuple(event_ids),
            events=subset,
        )

    # Reuse the same label_positives implementation — the EVTX_FileName
    # column is synthesized from json source files in _load() so the
    # static method works as-is on either corpus. The explicit
    # staticmethod() re-wrap is required because assigning a class's
    # @staticmethod attribute to another class strips the descriptor.
    label_positives = staticmethod(CorpusLoader.label_positives)
