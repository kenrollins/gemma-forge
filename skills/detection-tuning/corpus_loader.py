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

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


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
            self._df = pd.read_csv(
                self._csv_path, low_memory=False, dtype=str
            ).fillna("")
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

        Friday-night slice: filename-keyword match against ``EVTX_FileName``.
        This is the same labeling rule pre-flight 3 validated — sufficient
        for cred-dumping-style techniques where filenames are descriptive.

        Saturday afternoon may replace this with a per-rule ATT&CK technique
        ID match if the corpus's per-tactic labeling proves too coarse for
        recall-focused rules.
        """
        keywords = [k.lower() for k in positive_filename_keywords]
        if not keywords:
            return pd.Series(False, index=df.index)
        fn_lower = df["EVTX_FileName"].str.lower()
        return fn_lower.apply(lambda f: any(k in f for k in keywords))
