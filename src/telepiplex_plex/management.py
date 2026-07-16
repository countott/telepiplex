# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy

from telepiplex_plugin_sdk.media_metadata import (
    MEDIA_METADATA_KEY,
    SERIES_EPISODE_MAPPINGS,
    extract_confirmed_media_metadata,
    resolve_category_route,
)

from . import rules as plex_rules
from .context import logger


STEP_ORDER = ("scanning", "artwork", "audio", "subtitle")
GATING_STEPS = {"scanning"}


class WaitingForSelection(RuntimeError):
    def __init__(
        self,
        kind,
        target_id,
        candidates,
        *,
        rating_key="",
        part_id=0,
    ):
        super().__init__("Plex enhancement selection required")
        self.kind = str(kind)
        self.target_id = str(target_id)
        self.candidates = deepcopy(list(candidates or []))
        self.rating_key = str(rating_key or "")
        self.part_id = int(part_id or 0)
        self.step_result = None

    def as_dict(self, candidate_index=0):
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "rating_key": self.rating_key,
            "part_id": self.part_id,
            "candidates": deepcopy(self.candidates),
            "candidate_index": int(candidate_index),
        }


class PlexOperationCancelled(RuntimeError):
    pass


class PlexManagementService:
    def __init__(
        self,
        jobs,
        plex,
        tmdb=None,
        fanart=None,
        notifier=None,
        category_folders=None,
        scan_poll_interval=5,
        scan_timeout=300,
        clock=time.time,
        sleeper=time.sleep,
    ):
        self.jobs = jobs
        self.plex = plex
        self.tmdb = tmdb
        self.fanart = fanart
        self.notifier = notifier
        self.category_folders = list(category_folders or [])
        self.scan_poll_interval = max(float(scan_poll_interval), 0)
        self.scan_timeout = max(float(scan_timeout), 0)
        self._clock = clock
        self._sleep = sleeper

    @staticmethod
    def _completion_payload(completion):
        event = completion.event
        metadata = {}
        for value in (
            event.naming_metadata,
            event.metadata,
            completion.result.metadata,
        ):
            if isinstance(value, dict):
                metadata.update(deepcopy(value))
        return {
            "provider": str(event.provider or ""),
            "selected_path": str(event.selected_path or ""),
            "final_path": str(completion.result.final_path or event.final_path or ""),
            "resource_name": str(event.resource_name or ""),
            "user_id": int(event.user_id),
            "terminal_processor": str(completion.terminal_processor or ""),
            "metadata": metadata,
        }

    @staticmethod
    def _log_contract_rejection(reason):
        logger.warning(f"plex_contract_completion_rejected reason={str(reason)}")

    def enqueue_completion(self, completion):
        if not str(completion.terminal_processor or "").startswith("renaming."):
            return None
        payload = self._completion_payload(completion)
        return self._enqueue_payload(payload)

    def enqueue_organized_event(self, event: dict):
        event = dict(event or {})
        metadata = {}
        if isinstance(event.get("media_metadata"), dict):
            metadata[MEDIA_METADATA_KEY] = deepcopy(event["media_metadata"])
        final_path = str(event.get("final_path") or "")
        payload = {
            "provider": str(event.get("provider") or ""),
            "selected_path": str(event.get("selected_path") or ""),
            "final_path": final_path,
            "resource_name": str(event.get("resource_name") or final_path.rstrip("/").rsplit("/", 1)[-1]),
            "user_id": int(event.get("user_id") or 0),
            "chat_id": int(event.get("chat_id") or event.get("user_id") or 0),
            "operation_id": str(event.get("operation_id") or ""),
            "operation_revision": int(event.get("operation_revision") or 0),
            "terminal_processor": "renaming.feature",
            "metadata": metadata,
        }
        return self._enqueue_payload(payload)

    def enqueue_organized_event_jobs(self, event: dict):
        job = self.enqueue_organized_event(event)
        return [job] if job else []

    def _enqueue_payload(self, payload):
        metadata = payload["metadata"]
        contract_present = MEDIA_METADATA_KEY in metadata
        contract = extract_confirmed_media_metadata(metadata)
        if contract_present and contract is None:
            self._log_contract_rejection("invalid_contract")
            return None
        if contract:
            identity = str(contract["metadata_id"])
        else:
            identity = "\x1f".join(
                (
                    payload["provider"],
                    payload["final_path"],
                    payload["resource_name"],
                )
            )
        targets = self._payload_targets(payload, contract)
        if not targets:
            return None
        payload = deepcopy(payload)
        payload["targets"] = targets
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        job, created = self.jobs.create_or_get_with_status(key, payload)
        result = dict(job)
        result["created"] = created
        return result

    def _enqueue_payload_jobs(self, payload):
        job = self._enqueue_payload(payload)
        return [job] if job else []

    def _payload_targets(self, payload, contract):
        if not contract:
            if not payload["final_path"]:
                return []
            return [{
                "target_id": "legacy",
                "media_type": "movie",
                "final_path": payload["final_path"],
                "category_kind": "",
            }]
        placement = contract["placement"]
        if placement["library_type"] == "movie":
            if not payload["final_path"]:
                self._log_contract_rejection("terminal_path_missing")
                return []
            return [{
                "target_id": "movie",
                "media_type": "movie",
                "final_path": payload["final_path"],
                "season_number": None,
                "episode_number": None,
                "category_kind": placement["category_kind"],
                "mapping_kind": placement["mapping_kind"],
            }]
        locked = (
            (int(placement["season_number"]), int(placement["episode_number"]))
            if placement["mapping_kind"] in SERIES_EPISODE_MAPPINGS else None
        )
        targets = []
        for item in contract.get("items") or []:
            try:
                season, episode = int(item.get("season_number")), int(item.get("episode_number"))
            except (TypeError, ValueError):
                continue
            final_path = str(item.get("final_path") or "").strip()
            if not final_path or (locked and (season, episode) != locked):
                continue
            targets.append({
                "target_id": str(item.get("item_id") or f"S{season:02d}E{episode:03d}"),
                "media_type": "episode", "final_path": final_path,
                "season_number": season, "episode_number": episode,
                "category_kind": placement["category_kind"],
                "mapping_kind": placement["mapping_kind"],
            })
        if not targets:
            self._log_contract_rejection(
                "locked_episode_unresolved" if locked else "confirmed_series_unresolved"
            )
        return targets

    @staticmethod
    def _safe_error(exc):
        message = str(exc).replace("\n", " ")
        message = re.sub(
            r"(?i)\b(x-plex-token|api_key|auth_token|token)(\s*[:=]\s*)[^&,\s]+",
            r"\1\2***",
            message,
        )
        message = re.sub(
            r"(?i)(authorization\s*:\s*bearer|bearer)\s+[^,\s]+",
            r"\1 ***",
            message,
        )
        return message[:500]

    @staticmethod
    def _merge_step(job, name, result):
        steps = dict(job.get("step_results") or {})
        steps[str(name)] = dict(result or {})
        return steps

    def _record_step(self, job_id, name, result):
        job = self.jobs.get(job_id)
        return self.jobs.update(
            job_id,
            step_results=self._merge_step(job, name, result),
            error=None,
        )

    @staticmethod
    def _step_finished(job, state):
        result = (job.get("step_results") or {}).get(state) or {}
        return result.get("status") in {"success", "warning", "unchanged", "confirmed"}

    def run_job(self, job_id, *, should_cancel=None, on_stage=None):
        runners = {
            "artwork": self._artwork_stage,
            "audio": self._audio_stage,
            "subtitle": self._subtitle_stage,
        }
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        if job["state"] == "completed":
            return job
        for state in STEP_ORDER:
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    f"Plex operation cancelled before {state}"
                )
            job = self.jobs.get(job_id)
            if self._step_finished(job, state):
                continue
            if on_stage:
                on_stage(state, job)
            self.jobs.update(job_id, state=state, error=None)
            try:
                if state == "scanning":
                    step_result = self._scan(
                        self.jobs.get(job_id), should_cancel=should_cancel
                    )
                else:
                    step_result = runners[state](self.jobs.get(job_id))
            except PlexOperationCancelled:
                raise
            except WaitingForSelection as exc:
                waiting = exc.as_dict()
                step_result = exc.step_result or {
                    "status": "awaiting_selection",
                    "waiting": waiting,
                }
                self._record_step(job_id, state, step_result)
                waiting_job = self.jobs.update(
                    job_id,
                    state="awaiting_selection",
                    error=None,
                )
                self._notify_waiting(waiting_job, waiting)
                return self.jobs.get(job_id)
            except Exception as exc:
                message = self._safe_error(exc)
                if state in GATING_STEPS:
                    failed = self.jobs.update(job_id, state="failed", error=message)
                    self._notify_once(failed)
                    return self.jobs.get(job_id)
                step_result = {"status": "warning", "message": message}
            self._record_step(job_id, state, step_result)
        completed = self.jobs.update(job_id, state="completed", error=None)
        self._notify_once(completed)
        return self.jobs.get(job_id)

    def run_batch(self, job_ids, *, should_cancel=None, on_stage=None):
        """Scan once per library, then run each final-path job independently."""
        ordered_ids = [int(job_id) for job_id in job_ids]
        groups = {}
        for job_id in ordered_ids:
            job = self.jobs.get(job_id)
            if not job or job["state"] == "completed":
                continue
            if self._step_finished(job, "scanning"):
                continue
            library_id = str(self._route_library(job))
            groups.setdefault(library_id, []).append(job)

        for library_id, jobs in groups.items():
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled before scanning"
                )
            started = next((
                (job.get("step_results") or {}).get("scanning")
                for job in jobs
                if "before_rating_keys" in (
                    (job.get("step_results") or {}).get("scanning") or {}
                )
            ), None)
            if started:
                before = list(started.get("before_rating_keys") or [])
            else:
                before = sorted(self.plex.snapshot_recent(library_id))
            scan_started = {
                "status": "started",
                "library_id": library_id,
                "before_rating_keys": before,
                "batch_size": len(jobs),
            }
            for job in jobs:
                if on_stage:
                    on_stage("scanning", job)
                self.jobs.update(
                    job["id"],
                    state="scanning",
                    step_results=self._merge_step(job, "scanning", scan_started),
                    error=None,
                )
            try:
                self.plex.scan_library(library_id)
            except Exception as exc:
                message = self._safe_error(exc)
                for job in jobs:
                    failed = self.jobs.update(
                        job["id"], state="failed", error=message
                    )
                    self._notify_once(failed)
                continue
            scan_result = {
                "status": "success",
                "library_id": library_id,
                "before_rating_keys": before,
                "batch_size": len(jobs),
            }
            for job in jobs:
                current = self.jobs.get(job["id"])
                self.jobs.update(
                    job["id"],
                    step_results=self._merge_step(
                        current, "scanning", scan_result
                    ),
                    error=None,
                )
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled after scanning"
                )

        results = []
        for job_id in ordered_ids:
            job = self.jobs.get(job_id)
            if not job or job["state"] == "failed":
                if job:
                    results.append(job)
                continue
            results.append(self.run_job(
                job_id,
                should_cancel=should_cancel,
                on_stage=on_stage,
            ))
        return results

    def _media_metadata(self, job):
        metadata = (job.get("payload") or {}).get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        contract = extract_confirmed_media_metadata(metadata)
        if MEDIA_METADATA_KEY in metadata and contract is None:
            raise ValueError("Invalid or unsupported media_metadata contract")
        return contract

    def _effective_metadata(self, job):
        contract = self._media_metadata(job)
        if not contract:
            return (job.get("payload") or {}).get("metadata") or {}
        identity = dict(contract.get("identity") or {})
        identity["title"] = (
            identity.get("chinese_title")
            or identity.get("english_title")
            or ""
        )
        identity["original_title"] = identity.get("english_title") or ""
        identity["media_type"] = (
            "tv"
            if contract["placement"]["library_type"] == "series"
            else "movie"
        )
        return identity

    def _route_library(self, job, target=None):
        target = dict(target or {})
        category_kind = str(target.get("category_kind") or "")
        if category_kind:
            route = resolve_category_route(
                {"category_folder": self.category_folders},
                category_kind,
            )
            if not route or not route.get("plex_library_id"):
                raise LookupError(f"No Plex library route for {category_kind}")
            return route["plex_library_id"]
        contract = self._media_metadata(job)
        if contract:
            category_kind = contract["placement"]["category_kind"]
            route = resolve_category_route(
                {"category_folder": self.category_folders},
                category_kind,
            )
            if not route or not route.get("plex_library_id"):
                raise LookupError(f"No Plex library route for {category_kind}")
            return route["plex_library_id"]
        selected_path = str(job["payload"].get("selected_path") or "").rstrip("/")
        matches = []
        for entry in self.category_folders:
            path = str(entry.get("path") or "").rstrip("/")
            library_id = str(entry.get("plex_library_id") or "").strip()
            if path and library_id and (selected_path == path or selected_path.startswith(path + "/")):
                matches.append((len(path), library_id))
        if not matches:
            raise LookupError(f"No Plex library route for {selected_path}")
        return max(matches)[1]

    def _scan(self, job, *, should_cancel=None):
        targets = list((job.get("payload") or {}).get("targets") or [])
        if not targets:
            legacy_target = (job.get("payload") or {}).get("target")
            if isinstance(legacy_target, dict):
                targets = [deepcopy(legacy_target)]
        if not targets:
            final_path = str((job.get("payload") or {}).get("final_path") or "")
            if final_path:
                targets = [{
                    "target_id": "legacy",
                    "media_type": "movie",
                    "final_path": final_path,
                    "category_kind": "",
                }]
        groups = {}
        target_results = {}
        for target in targets:
            target_id = str(target.get("target_id") or "")
            try:
                library_id = str(self._route_library(job, target))
            except Exception as exc:
                target_results[target_id] = {
                    "status": "warning",
                    "final_path": str(target.get("final_path") or ""),
                    "message": self._safe_error(exc),
                }
                continue
            groups.setdefault(library_id, []).append(target)

        scan_result = {
            "status": "started",
            "libraries": {},
            "targets": target_results,
        }
        self.jobs.update(
            job["id"],
            step_results=self._merge_step(job, "scanning", scan_result),
        )

        located = 0
        first_rating_key = ""
        for library_id, library_targets in groups.items():
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled before library scan"
                )
            library_result = {
                "status": "started",
                "target_ids": [
                    str(target.get("target_id") or "")
                    for target in library_targets
                ],
            }
            scan_result["libraries"][library_id] = library_result
            self.jobs.update(
                job["id"],
                step_results=self._merge_step(job, "scanning", scan_result),
            )
            try:
                self.plex.scan_library(library_id)
            except Exception as exc:
                message = self._safe_error(exc)
                library_result.update({"status": "warning", "message": message})
                for target in library_targets:
                    target_id = str(target.get("target_id") or "")
                    target_results[target_id] = {
                        "status": "warning",
                        "library_id": library_id,
                        "final_path": str(target.get("final_path") or ""),
                        "message": message,
                    }
                self.jobs.update(
                    job["id"],
                    step_results=self._merge_step(
                        job,
                        "scanning",
                        scan_result,
                    ),
                )
                continue
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled after library scan"
                )
            missing = 0
            for index, target in enumerate(library_targets, start=1):
                target_id = str(target.get("target_id") or "")
                final_path = str(target.get("final_path") or "")
                deadline = self._clock() + self.scan_timeout
                item = None
                lookup_error = ""
                while item is None:
                    if should_cancel and should_cancel():
                        raise PlexOperationCancelled(
                            "Plex operation cancelled while locating final path"
                        )
                    try:
                        item = self.plex.find_item_by_path(
                            library_id,
                            final_path,
                        )
                    except PlexOperationCancelled:
                        raise
                    except Exception as exc:
                        lookup_error = self._safe_error(exc)
                        break
                    if item is not None or self._clock() >= deadline:
                        break
                    self._sleep(self.scan_poll_interval)
                if lookup_error:
                    missing += 1
                    target_results[target_id] = {
                        "status": "warning",
                        "library_id": library_id,
                        "final_path": final_path,
                        "message": lookup_error,
                    }
                elif item is None:
                    missing += 1
                    target_results[target_id] = {
                        "status": "warning",
                        "library_id": library_id,
                        "final_path": final_path,
                        "message": "Plex item was not found by final path",
                    }
                else:
                    rating_key = str(item.get("rating_key") or "")
                    if not rating_key:
                        missing += 1
                        target_results[target_id] = {
                            "status": "warning",
                            "library_id": library_id,
                            "final_path": final_path,
                            "message": "Plex item is missing a rating key",
                        }
                    else:
                        located += 1
                        first_rating_key = first_rating_key or rating_key
                        target_results[target_id] = {
                            "status": "success",
                            "library_id": library_id,
                            "rating_key": rating_key,
                            "final_path": final_path,
                        }
                library_result["located"] = index - missing
                library_result["missing"] = missing
                self.jobs.update(
                    job["id"],
                    rating_key=first_rating_key or None,
                    step_results=self._merge_step(
                        job,
                        "scanning",
                        scan_result,
                    ),
                )
            library_result["status"] = "warning" if missing else "success"
            library_result["located"] = len(library_targets) - missing
            library_result["missing"] = missing
            self.jobs.update(
                job["id"],
                step_results=self._merge_step(job, "scanning", scan_result),
            )

        total = len(targets)
        scan_result["status"] = (
            "success" if located == total else "warning" if located else "failed"
        )
        scan_result["located"] = located
        scan_result["missing"] = total - located
        self.jobs.update(
            job["id"],
            rating_key=first_rating_key or None,
            step_results=self._merge_step(job, "scanning", scan_result),
        )
        if not located:
            raise LookupError("Plex scan completed but no target was located")
        return scan_result

    @staticmethod
    def _stage_status(results):
        statuses = [
            str(result.get("status") or "")
            for result in results.values()
            if isinstance(result, dict)
        ]
        if any(status == "warning" for status in statuses):
            return "warning"
        if statuses and all(status == "unchanged" for status in statuses):
            return "unchanged"
        return "success"

    def _located_targets(self, job):
        payload_targets = list((job.get("payload") or {}).get("targets") or [])
        by_id = {
            str(target.get("target_id") or ""): target
            for target in payload_targets
        }
        scanning = (job.get("step_results") or {}).get("scanning") or {}
        located = []
        for target_id, result in (scanning.get("targets") or {}).items():
            if (
                isinstance(result, dict)
                and result.get("status") == "success"
                and result.get("rating_key")
            ):
                located.append((
                    deepcopy(by_id.get(str(target_id)) or {
                        "target_id": str(target_id),
                        "final_path": result.get("final_path") or "",
                    }),
                    str(result["rating_key"]),
                ))
        return located

    @staticmethod
    def _selection_finished(result):
        return str((result or {}).get("status") or "") in {
            "success",
            "warning",
            "unchanged",
            "confirmed",
        }

    def _run_target_stage(self, job, runner, stage_name):
        current_step = (
            (job.get("step_results") or {}).get(stage_name)
            or {}
        )
        previous_results = dict(current_step.get("targets") or {})
        confirmed_selections = deepcopy(
            list(current_step.get("confirmed_selections") or [])
        )
        results = {}
        for target, rating_key in self._located_targets(job):
            target_id = str(target.get("target_id") or "")
            previous = previous_results.get(target_id) or {}
            if self._selection_finished(previous):
                results[target_id] = deepcopy(previous)
                continue
            target_job = deepcopy(job)
            target_job["rating_key"] = rating_key
            target_job["target"] = deepcopy(target)
            target_job["_stage_name"] = str(stage_name)
            target_job["_stage_results"] = deepcopy(results)
            try:
                result = runner(target_job)
            except WaitingForSelection as exc:
                waiting = exc.as_dict()
                results[target_id] = {
                    "status": "awaiting_selection",
                    "waiting": waiting,
                }
                exc.step_result = {
                    "status": "awaiting_selection",
                    "targets": results,
                    "waiting": waiting,
                    "confirmed_selections": confirmed_selections,
                }
                raise
            except Exception as exc:
                result = {
                    "status": "warning",
                    "message": self._safe_error(exc),
                }
            results[target_id] = dict(result or {})
        return {
            "status": self._stage_status(results),
            "targets": results,
            "confirmed_selections": confirmed_selections,
        }

    def _artwork_stage(self, job):
        return self._run_target_stage(job, self._artwork, "artwork")

    def _audio_stage(self, job):
        return self._run_target_stage(job, self._audio_target, "audio")

    def _subtitle_stage(self, job):
        return self._run_target_stage(job, self._subtitle_target, "subtitle")

    @staticmethod
    def _candidate_identity(candidate):
        return (
            str(candidate.get("title") or "").strip().casefold(),
            int(candidate.get("year") or 0),
        )

    def _locate(self, job, *, should_cancel=None):
        scan = job["step_results"]["scanning"]
        library_id = scan["library_id"]
        contract = self._media_metadata(job)
        plex_target = (job.get("payload") or {}).get("target") or {}
        if contract and plex_target.get("media_type") == "episode":
            placement = contract["placement"]
            identity = ((contract.get("relation") or {}).get("target_series") or {}) if placement["mapping_kind"] in SERIES_EPISODE_MAPPINGS else (contract.get("identity") or {})
            expected_final_paths = [plex_target.get("final_path")]
            deadline = self._clock() + self.scan_timeout
            item = None
            while item is None:
                if should_cancel and should_cancel():
                    raise PlexOperationCancelled(
                        "Plex operation cancelled while locating episode"
                    )
                item = self.plex.find_series_episode(
                    library_id,
                    tvdb_series_id=(
                        (identity.get("external_ids") or {}).get("tvdb") or ""
                    ),
                    title=(
                        identity.get("english_title")
                        or identity.get("chinese_title")
                        or ""
                    ),
                    year=identity.get("year") or "",
                    season_number=plex_target["season_number"],
                    episode_number=plex_target["episode_number"],
                    expected_final_paths=expected_final_paths,
                )
                if item is not None or self._clock() >= deadline:
                    break
                self._sleep(self.scan_poll_interval)
            if not item:
                raise LookupError("Confirmed Plex episode was not found")
            self.jobs.update(job["id"], rating_key=str(item["rating_key"]))
            return {
                "status": "success",
                "rating_key": str(item["rating_key"]),
                "candidates": [item],
            }

        if contract and plex_target.get("media_type") == "movie":
            identity = contract.get("identity") or {}
            deadline = self._clock() + self.scan_timeout
            item = None
            while item is None:
                if should_cancel and should_cancel():
                    raise PlexOperationCancelled(
                        "Plex operation cancelled while locating movie"
                    )
                item = self.plex.find_movie(
                    library_id,
                    title=identity.get("english_title") or identity.get("chinese_title") or "",
                    year=identity.get("year") or "",
                    expected_final_paths=[plex_target.get("final_path")],
                )
                if item is not None or self._clock() >= deadline:
                    break
                self._sleep(self.scan_poll_interval)
            if not item:
                raise LookupError("Confirmed Plex movie was not found")
            self.jobs.update(job["id"], rating_key=str(item["rating_key"]))
            return {"status": "success", "rating_key": str(item["rating_key"]), "candidates": [item]}

        before = set(scan.get("before_rating_keys") or [])
        deadline = self._clock() + self.scan_timeout
        candidates = []
        while True:
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled while locating item"
                )
            candidates = self.plex.locate_candidates(library_id, before)
            if candidates or self._clock() >= deadline:
                break
            self._sleep(self.scan_poll_interval)
        if not candidates:
            raise LookupError("Plex scan completed but no new item was located")
        metadata = self._effective_metadata(job)
        expected = (
            str(metadata.get("title") or metadata.get("original_title") or "").strip().casefold(),
            int(metadata.get("year") or 0),
        )
        exact = [item for item in candidates if self._candidate_identity(item) == expected]
        chosen = exact[0] if len(exact) == 1 else candidates[0] if len(candidates) == 1 else None
        if chosen is None:
            if contract:
                raise LookupError("Contract-bound Plex location is ambiguous")
            raise RuntimeError(
                "Legacy Plex location confirmation is no longer supported"
            )
        self.jobs.update(job["id"], rating_key=str(chosen["rating_key"]))
        return {"status": "success", "rating_key": str(chosen["rating_key"]), "candidates": candidates}

    def _match(self, job):
        rating_key = str(job.get("rating_key") or "")
        if not rating_key:
            raise LookupError("Plex rating key is missing")
        contract = self._media_metadata(job)
        if contract:
            mapping_kind = contract["placement"]["mapping_kind"]
            item = self.plex.get_item(rating_key)
            if mapping_kind == "temporary_related_special":
                return {
                    "status": "unchanged",
                    "action": "custom_metadata_pending",
                    "item": item,
                }
            if mapping_kind == "tvdb_official":
                expected = {
                    "tvdb": str(contract["placement"]["tvdb_episode_id"])
                }
                if not plex_rules.external_ids_match(expected, item.get("guids")):
                    raise RuntimeError(
                        "Official Plex Special does not match confirmed TVDB episode"
                    )
                return {"status": "success", "action": "verified", "item": item}
            if mapping_kind == "ai_inferred_tvdb":
                if not any(
                    str(guid).startswith("tvdb://")
                    for guid in item.get("guids") or []
                ):
                    raise RuntimeError(
                        "AI-inferred Special is still not verified by TVDB"
                    )
                return {
                    "status": "success",
                    "action": "verified_after_scan",
                    "item": item,
                }
            if mapping_kind == "standalone":
                target = (job.get("payload") or {}).get("target") or {}
                if target.get("media_type") == "episode":
                    return {"status": "success", "action": "verified_by_series_episode_path", "item": item}
                expected = {
                    source: str(value)
                    for source, value in (
                        (contract.get("identity") or {}).get("external_ids") or {}
                    ).items()
                    if source in {"imdb", "tmdb", "tvdb"}
                    and str(value).strip()
                }
                if expected and plex_rules.external_ids_match(
                    expected, item.get("guids")
                ):
                    return {
                        "status": "success",
                        "action": "verified",
                        "item": item,
                    }
                if expected:
                    identity = contract.get("identity") or {}
                    candidates = self.plex.list_match_candidates(
                        rating_key,
                        title=(
                            identity.get("english_title")
                            or identity.get("chinese_title")
                        ),
                        year=identity.get("year"),
                    )
                    exact = plex_rules.choose_exact_match(expected, candidates)
                    if exact is None:
                        raise RuntimeError(
                            "Standalone Plex match could not be verified"
                        )
                    fixed = self.plex.fix_match(rating_key, exact["guid"])
                    if not plex_rules.external_ids_match(
                        expected, fixed.get("guids")
                    ):
                        raise RuntimeError(
                            "Standalone Plex match verification failed"
                        )
                    return {
                        "status": "success",
                        "action": "fixed",
                        "item": fixed,
                    }
                expected_identity = self._candidate_identity(
                    self._effective_metadata(job)
                )
                if self._candidate_identity(item) != expected_identity:
                    raise RuntimeError(
                        "Standalone Plex title/year could not be verified"
                    )
                return {
                    "status": "success",
                    "action": "verified_by_title_year",
                    "item": item,
                }
        return self._legacy_match(job, rating_key)

    def _legacy_match(self, job, rating_key):
        metadata = job["payload"].get("metadata") or {}
        external_ids = metadata.get("external_ids") or {}
        item = self.plex.get_item(rating_key)
        if plex_rules.external_ids_match(external_ids, item.get("guids")):
            return {"status": "success", "action": "verified", "item": item}
        candidates = self.plex.list_match_candidates(
            rating_key,
            title=metadata.get("title") or metadata.get("original_title"),
            year=metadata.get("year"),
        )
        exact = plex_rules.choose_exact_match(external_ids, candidates)
        if exact is None:
            raise RuntimeError(
                "Legacy Plex match confirmation is no longer supported"
            )
        fixed = self.plex.fix_match(rating_key, exact["guid"])
        if not plex_rules.external_ids_match(external_ids, fixed.get("guids")):
            raise RuntimeError("Plex match verification failed after fixMatch")
        return {"status": "success", "action": "fixed", "candidate": exact, "item": fixed}

    @staticmethod
    def _contains_chinese(value):
        return bool(re.search(r"[\u3400-\u9fff]", str(value or "")))

    def _localize(self, job):
        contract = self._media_metadata(job)
        mapping_kind = (
            (contract.get("placement") or {}).get("mapping_kind")
            if contract
            else ""
        )
        if mapping_kind in {"tvdb_official", "ai_inferred_tvdb"}:
            return {
                "status": "unchanged",
                "action": "official_metadata_preserved",
            }
        if mapping_kind == "temporary_related_special":
            identity = contract["identity"]
            item = self.plex.edit_custom_episode_metadata(
                job["rating_key"],
                title=(
                    identity.get("chinese_title")
                    or identity.get("english_title")
                    or ""
                ),
                summary=identity.get("summary") or "",
                original_release_date=(
                    identity.get("original_release_date") or ""
                ),
                year=identity.get("year") or "",
            )
            return {
                "status": "success",
                "action": "custom_metadata",
                "item": item,
            }
        item = self.plex.refresh_zh_cn(job["rating_key"])
        localized = self._contains_chinese(item.get("title")) or self._contains_chinese(item.get("summary"))
        return {
            "status": "success" if localized else "warning",
            "language": "zh-CN",
            "verified_chinese": localized,
        }

    @staticmethod
    def _external_ids_from_item(item):
        result = {}
        for guid in item.get("guids") or []:
            source, separator, value = str(guid).partition("://")
            if separator and source in {"tmdb", "tvdb", "imdb"}:
                result[source] = value
        return result

    def _artwork_candidates(self, item):
        ids = self._external_ids_from_item(item)
        metadata_ids = item.get("external_ids") or {}
        ids.update({key: str(value) for key, value in metadata_ids.items() if value})
        media_type = "tv" if item.get("media_type") in {"show", "episode", "tv"} else "movie"
        warnings = []
        tmdb_posters = []
        fanart_posters = []
        if self.tmdb and ids.get("tmdb"):
            try:
                tmdb_posters = self.tmdb.textless_posters(media_type, ids["tmdb"])
            except Exception as exc:
                warnings.append(self._safe_error(exc))
        if self.fanart:
            try:
                fanart_posters = self.fanart.textless_posters(media_type, ids)
            except Exception as exc:
                warnings.append(self._safe_error(exc))
        return tmdb_posters, fanart_posters, warnings

    @staticmethod
    def _target_id(job):
        return str((job.get("target") or {}).get("target_id") or "")

    def _confirmed_selection(self, job, kind, *, rating_key, part_id=0):
        step = (
            (job.get("step_results") or {}).get(str(kind))
            or {}
        )
        target_id = self._target_id(job)
        for selection in step.get("confirmed_selections") or []:
            if (
                str(selection.get("kind") or "") == str(kind)
                and str(selection.get("target_id") or "") == target_id
                and str(selection.get("rating_key") or "") == str(rating_key)
                and int(selection.get("part_id") or 0) == int(part_id or 0)
            ):
                return deepcopy(selection.get("candidate") or {})
        return None

    def _artwork_item(self, job):
        rating_key = str(job.get("rating_key") or "")
        item = self.plex.get_item(rating_key)
        if (
            item.get("media_type") == "episode"
            and item.get("grandparent_rating_key")
        ):
            rating_key = str(item["grandparent_rating_key"])
            item = self.plex.get_item(rating_key)
        return rating_key, item

    def _series_artwork_already_processed(self, job, rating_key):
        target_id = self._target_id(job)
        for existing_target_id, result in (
            job.get("_stage_results") or {}
        ).items():
            if (
                str(existing_target_id) != target_id
                and str((result or {}).get("rating_key") or "")
                == str(rating_key)
                and self._selection_finished(result)
            ):
                return True
        return False

    def _artwork(self, job):
        contract = self._media_metadata(job)
        mapping_kind = (
            (contract.get("placement") or {}).get("mapping_kind")
            if contract
            else ""
        )
        if mapping_kind in {"tvdb_official", "ai_inferred_tvdb"}:
            return {
                "status": "unchanged",
                "action": "official_artwork_preserved",
                "attempted": False,
            }
        if mapping_kind == "temporary_related_special":
            identity = contract.get("identity") or {}
            poster_url = str(identity.get("poster_url") or "").strip()
            if not poster_url:
                return {
                    "status": "unchanged",
                    "message": "No confirmed custom poster",
                    "attempted": True,
                }
            self.plex.set_poster_url(job["rating_key"], poster_url)
            return {
                "status": "success",
                "attempted": True,
                "selected": {
                    "url": poster_url,
                    "source": identity.get("poster_source") or "media_metadata",
                },
                "rating_key": str(job["rating_key"]),
            }
        artwork_rating_key, item = self._artwork_item(job)
        if self._series_artwork_already_processed(job, artwork_rating_key):
            return {
                "status": "unchanged",
                "action": "series_artwork_already_processed",
                "attempted": False,
                "rating_key": artwork_rating_key,
            }
        confirmed = self._confirmed_selection(
            job,
            "artwork",
            rating_key=artwork_rating_key,
        )
        if confirmed:
            return {
                "status": "success",
                "attempted": True,
                "selected": confirmed,
                "rating_key": artwork_rating_key,
            }
        item["external_ids"] = self._effective_metadata(job).get("external_ids") or {}
        tmdb_posters, fanart_posters, warnings = self._artwork_candidates(item)
        ranked = plex_rules.rank_textless_posters(
            tmdb_posters,
            fanart_posters,
        )
        chosen = plex_rules.choose_textless_poster(tmdb_posters, fanart_posters)
        if chosen:
            self.plex.set_poster_url(artwork_rating_key, chosen["url"])
            return {
                "status": "warning" if warnings else "success",
                "attempted": True,
                "selected": chosen,
                "rating_key": artwork_rating_key,
                "warnings": warnings,
            }
        if ranked:
            raise WaitingForSelection(
                "artwork",
                self._target_id(job),
                ranked,
                rating_key=artwork_rating_key,
            )
        return {
            "status": "warning" if warnings else "unchanged",
            "attempted": True,
            "message": "No automatic textless poster candidate",
            "plex_candidates": self.plex.list_posters(artwork_rating_key),
            "rating_key": artwork_rating_key,
            "warnings": warnings,
        }

    def _audio_target(self, job):
        metadata = self._effective_metadata(job)
        tmdb_id = (metadata.get("external_ids") or {}).get("tmdb")
        media_type = "tv" if metadata.get("media_type") in {"show", "episode", "tv"} else "movie"
        warnings = []
        original_language = None
        if self.tmdb and tmdb_id:
            try:
                original_language = self.tmdb.details(
                    media_type,
                    tmdb_id,
                ).get("original_language")
            except Exception as exc:
                warnings.append(self._safe_error(exc))
        else:
            warnings.append("TMDB original language is unavailable")
        if not original_language and not warnings:
            warnings.append("TMDB original language is unavailable")
        audio_results = []
        for part in self.plex.list_streams(job["rating_key"]):
            confirmed = self._confirmed_selection(
                job,
                "audio",
                rating_key=job["rating_key"],
                part_id=part["id"],
            )
            if confirmed:
                audio_results.append({
                    "part_id": part["id"],
                    "stream_id": confirmed.get("id"),
                    "changed": True,
                    "confirmed": True,
                })
                continue
            ranked = (
                plex_rules.rank_original_audio(
                    part.get("audio_streams"),
                    original_language,
                )
                if original_language
                else []
            )
            audio = (
                plex_rules.choose_original_audio(
                    part.get("audio_streams"),
                    original_language,
                )
                if original_language
                else None
            )
            if ranked and audio is None:
                raise WaitingForSelection(
                    "audio",
                    self._target_id(job),
                    ranked,
                    rating_key=job["rating_key"],
                    part_id=part["id"],
                )
            if not ranked and original_language:
                warnings.append(
                    "No original-language audio stream was found "
                    f"for part {part['id']}"
                )
            if audio and not audio.get("selected"):
                self.plex.select_audio(
                    job["rating_key"],
                    part["id"],
                    audio["id"],
                )
            audio_results.append({
                "part_id": part["id"],
                "stream_id": audio.get("id") if audio else None,
                "changed": bool(audio and not audio.get("selected")),
            })
        return {
            "status": "warning" if warnings else "success",
            "parts": audio_results,
            "warnings": warnings,
        }

    def _subtitle_target(self, job):
        subtitle_results = []
        for part in self.plex.list_streams(job["rating_key"]):
            confirmed = self._confirmed_selection(
                job,
                "subtitle",
                rating_key=job["rating_key"],
                part_id=part["id"],
            )
            if confirmed:
                subtitle_results.append({
                    "part_id": part["id"],
                    "stream_id": confirmed.get("id"),
                    "source": (
                        "external"
                        if confirmed.get("external")
                        else "embedded"
                    ),
                    "changed": True,
                    "confirmed": True,
                })
                continue
            ranked = plex_rules.rank_chi_subtitles(
                part.get("subtitle_streams")
            )
            subtitle = plex_rules.choose_chi_subtitle(
                part.get("subtitle_streams")
            )
            if ranked and subtitle is None:
                raise WaitingForSelection(
                    "subtitle",
                    self._target_id(job),
                    ranked,
                    rating_key=job["rating_key"],
                    part_id=part["id"],
                )
            if subtitle and not subtitle.get("selected"):
                self.plex.select_subtitle(
                    job["rating_key"],
                    part["id"],
                    subtitle["id"],
                )
            subtitle_results.append({
                "part_id": part["id"],
                "stream_id": subtitle.get("id") if subtitle else None,
                "source": (
                    "external"
                    if subtitle and subtitle.get("external")
                    else "embedded" if subtitle else None
                ),
                "changed": bool(subtitle and not subtitle.get("selected")),
            })
        found = any(result["stream_id"] for result in subtitle_results)
        summary = (
            subtitle_results[0]
            if len(subtitle_results) == 1
            else {"parts": subtitle_results}
        )
        return {
            "status": "success" if found else "unchanged",
            **summary,
        }

    def _streams(self, job):
        metadata = self._effective_metadata(job)
        tmdb_id = (metadata.get("external_ids") or {}).get("tmdb")
        media_type = "tv" if metadata.get("media_type") in {"show", "episode", "tv"} else "movie"
        warnings = []
        original_language = None
        if self.tmdb and tmdb_id:
            try:
                original_language = self.tmdb.details(media_type, tmdb_id).get("original_language")
            except Exception as exc:
                warnings.append(self._safe_error(exc))
        else:
            warnings.append("TMDB original language is unavailable")
        audio_results = []
        subtitle_results = []
        for part in self.plex.list_streams(job["rating_key"]):
            audio = plex_rules.choose_original_audio(part.get("audio_streams"), original_language)
            if audio and not audio.get("selected"):
                self.plex.select_audio(job["rating_key"], part["id"], audio["id"])
            audio_results.append({
                "part_id": part["id"],
                "stream_id": audio.get("id") if audio else None,
                "changed": bool(audio and not audio.get("selected")),
            })
            subtitle = plex_rules.choose_chi_subtitle(part.get("subtitle_streams"))
            if subtitle and not subtitle.get("selected"):
                self.plex.select_subtitle(job["rating_key"], part["id"], subtitle["id"])
            subtitle_results.append({
                "part_id": part["id"],
                "stream_id": subtitle.get("id") if subtitle else None,
                "source": "external" if subtitle and subtitle.get("external") else "embedded" if subtitle else None,
                "changed": bool(subtitle and not subtitle.get("selected")),
            })
        subtitle_summary = subtitle_results[0] if len(subtitle_results) == 1 else {"parts": subtitle_results}
        return {
            "status": "warning" if warnings else "success",
            "audio": audio_results,
            "subtitle": subtitle_summary,
            "warnings": warnings,
        }

    def _notify_once(self, job):
        if not self.notifier:
            return
        steps = dict(job.get("step_results") or {})
        if steps.get("_notification", {}).get("sent"):
            return
        self.notifier(job["payload"].get("user_id"), self.format_job_summary(job))
        steps["_notification"] = {"sent": True}
        self.jobs.update(job["id"], step_results=steps)

    def _notify_waiting(self, job, waiting):
        if not self.notifier:
            return
        self.notifier(
            job["payload"].get("user_id"),
            f"⚠️ Plex {waiting['kind']} 需要确认\n任务 {job['id']}",
            waiting,
        )

    @staticmethod
    def format_job_summary(job):
        icon = "✅" if job.get("state") == "completed" else "⚠️"
        name = job.get("payload", {}).get("resource_name") or f"Job {job.get('id')}"
        text = f"{icon} Plex 管理：{name}\n状态：{job.get('state')}"
        if job.get("error"):
            text += f"\n错误：{job['error']}"
        return text

    def retry_job(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        steps = dict(job.get("step_results") or {})
        restart_index = 0
        for index, name in enumerate(STEP_ORDER):
            if not self._step_finished(job, name):
                restart_index = index
                break
        else:
            restart_index = len(STEP_ORDER)
        for name in STEP_ORDER[restart_index:]:
            steps.pop(name, None)
        self.jobs.update(job_id, state="queued", step_results=steps, error=None)
        return self.run_job(job_id)

    def pending_selection(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        for name in STEP_ORDER:
            waiting = (
                ((job.get("step_results") or {}).get(name) or {})
                .get("waiting")
            )
            if isinstance(waiting, dict):
                return deepcopy(waiting)
        return None

    def set_selection_index(self, job_id, index):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        waiting = self.pending_selection(job_id)
        if not waiting:
            raise ValueError(f"Plex job {job_id} is not awaiting a selection")
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise ValueError("Selection index must be an integer") from exc
        candidates = list(waiting.get("candidates") or [])
        if index < 0 or index >= len(candidates):
            raise ValueError(f"Selection index is out of range: {index}")
        kind = str(waiting["kind"])
        steps = deepcopy(job.get("step_results") or {})
        steps[kind]["waiting"]["candidate_index"] = index
        self.jobs.update(job_id, step_results=steps, error=None)
        return deepcopy(steps[kind]["waiting"])

    def confirm_selection(
        self,
        job_id,
        index,
        *,
        should_cancel=None,
        on_stage=None,
    ):
        waiting = self.set_selection_index(job_id, index)
        job = self.jobs.get(job_id)
        candidate = deepcopy(waiting["candidates"][int(index)])
        kind = str(waiting["kind"])
        rating_key = str(waiting.get("rating_key") or "")
        part_id = int(waiting.get("part_id") or 0)
        if kind == "artwork":
            url = str(candidate.get("url") or "")
            if not url:
                raise ValueError("Selected artwork candidate has no URL")
            self.plex.set_poster_url(rating_key, url)
        elif kind == "audio":
            self.plex.select_audio(rating_key, part_id, candidate["id"])
        elif kind == "subtitle":
            self.plex.select_subtitle(rating_key, part_id, candidate["id"])
        else:
            raise ValueError(f"Unsupported Plex selection kind: {kind}")

        steps = deepcopy(job.get("step_results") or {})
        step = steps[kind]
        confirmed = list(step.get("confirmed_selections") or [])
        confirmed.append({
            "kind": kind,
            "target_id": str(waiting.get("target_id") or ""),
            "rating_key": rating_key,
            "part_id": part_id,
            "candidate_index": int(index),
            "candidate": candidate,
        })
        step["confirmed_selections"] = confirmed
        step["status"] = "started"
        step.pop("waiting", None)
        target_id = str(waiting.get("target_id") or "")
        target_result = (step.get("targets") or {}).get(target_id)
        if isinstance(target_result, dict):
            target_result.pop("waiting", None)
            target_result["status"] = "started"
        self.jobs.update(
            job_id,
            state=kind,
            step_results=steps,
            error=None,
        )
        return self.run_job(
            job_id,
            should_cancel=should_cancel,
            on_stage=on_stage,
        )

    def confirm_match(
        self, job_id, selection, *, should_cancel=None, on_stage=None
    ):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        if "://" in str(selection):
            fixed = self.plex.fix_match(job["rating_key"], str(selection))
            expected = job["payload"].get("metadata", {}).get("external_ids") or {}
            if not plex_rules.external_ids_match(expected, fixed.get("guids")):
                raise RuntimeError("Selected Plex match does not match expected external IDs")
            steps = self._merge_step(job, "matching", {"status": "confirmed", "candidate_guid": str(selection)})
            self.jobs.update(job_id, state="localizing", step_results=steps, error=None)
        else:
            steps = dict(job.get("step_results") or {})
            for name in STEP_ORDER[2:]:
                steps.pop(name, None)
            steps["locating"] = {
                "status": "confirmed",
                "rating_key": str(selection),
            }
            self.jobs.update(job_id, state="matching", rating_key=str(selection), step_results=steps, error=None)
        return self.run_job(
            job_id,
            should_cancel=should_cancel,
            on_stage=on_stage,
        )

    def resume_incomplete_jobs(self, executor=None):
        jobs = [
            job
            for job in self.jobs.list(1000)
            if job["state"] not in {
                "completed",
                "awaiting_selection",
            }
        ]
        for job in jobs:
            if executor:
                executor.submit(self.run_job, job["id"])
            else:
                self.run_job(job["id"])
        return len(jobs)

    def server_status(self):
        return self.plex.server_status()

    def list_libraries(self):
        return self.plex.list_libraries()

    def inspect_item(self, rating_key):
        return self.plex.get_item(rating_key)

    def list_match_candidates(self, rating_key):
        item = self.plex.get_item(rating_key)
        return self.plex.list_match_candidates(rating_key, title=item.get("title"), year=item.get("year"))

    def list_artwork_candidates(self, rating_key):
        item = self.plex.get_item(rating_key)
        tmdb, fanart, warnings = self._artwork_candidates(item)
        return {
            "tmdb": tmdb,
            "fanart": fanart,
            "plex": self.plex.list_posters(rating_key),
            "warnings": warnings,
        }

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def list_jobs(self, limit=50):
        return self.jobs.list(limit)

    @staticmethod
    def _normalize_action(action):
        aliases = {
            "plex_scan_library": "scan_library",
            "plex_fix_match": "fix_match",
            "plex_refresh_chinese_metadata": "refresh_chinese_metadata",
            "plex_set_textless_poster": "set_textless_poster",
            "plex_select_original_audio": "select_original_audio",
            "plex_select_chi_subtitle": "select_chi_subtitle",
            "plex_run_management_pipeline": "run_management_pipeline",
            "plex_retry_job": "retry_job",
            "plex_apply_metadata_batch": "metadata_batch",
        }
        return aliases.get(str(action), str(action))

    def prepare_operation(self, action, payload):
        action = self._normalize_action(action)
        payload = dict(payload or {})
        if action == "metadata_batch":
            payload = self._validated_metadata_batch(payload)
        token = self.jobs.issue_confirmation(
            int(payload.get("job_id") or 0),
            action,
            payload,
        )
        return {
            "status": "confirmation_required",
            "action": action,
            "payload": payload,
            "confirmation_token": token,
        }

    def _validated_metadata_batch(self, payload):
        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes or len(changes) > 20:
            raise ValueError("metadata_batch requires 1 to 20 changes")
        allowed = {
            "fix_match",
            "refresh_chinese_metadata",
            "set_textless_poster",
        }
        normalized = []
        for change in changes:
            if not isinstance(change, dict):
                raise ValueError("metadata_batch change must be an object")
            action = self._normalize_action(change.get("action"))
            change_payload = change.get("payload")
            if action not in allowed or not isinstance(change_payload, dict):
                raise ValueError(
                    "metadata_batch only accepts match, localization, and poster writes"
                )
            normalized.append({
                "action": action,
                "payload": dict(change_payload),
            })
        return {"changes": normalized}

    def apply_operation(self, action, payload, confirmation_token):
        action = self._normalize_action(action)
        confirmed = self.jobs.consume_confirmation(confirmation_token, action)
        if not confirmed:
            raise ValueError("Invalid, expired, or already used confirmation token")
        stored_payload = dict(confirmed)
        stored_payload.pop("action", None)
        if int(stored_payload.get("job_id") or 0) == 0:
            stored_payload.pop("job_id", None)
        result = self._execute_operation(action, stored_payload)
        return {"status": "applied", "action": action, "result": result}

    def _execute_operation(self, action, payload):
        if action == "metadata_batch":
            validated = self._validated_metadata_batch(payload)
            return {
                "results": [
                    {
                        "action": change["action"],
                        "result": self._execute_operation(
                            change["action"], change["payload"]
                        ),
                    }
                    for change in validated["changes"]
                ]
            }
        if action == "scan_library":
            self.plex.scan_library(payload["library_id"])
            return {"library_id": str(payload["library_id"])}
        if action == "fix_match":
            return self.plex.fix_match(payload["rating_key"], payload["candidate_guid"])
        if action == "refresh_chinese_metadata":
            return self.plex.refresh_zh_cn(payload["rating_key"])
        if action == "set_textless_poster":
            return self.plex.set_poster_url(payload["rating_key"], payload["url"])
        if action == "select_original_audio":
            self.plex.select_audio(payload["rating_key"], payload["part_id"], payload["stream_id"])
            return dict(payload)
        if action == "select_chi_subtitle":
            self.plex.select_subtitle(payload["rating_key"], payload["part_id"], payload["stream_id"])
            return dict(payload)
        if action == "run_management_pipeline":
            return self.run_job(payload["job_id"])
        if action == "retry_job":
            return self.retry_job(payload["job_id"])
        raise ValueError(f"Unsupported Plex operation: {action}")
