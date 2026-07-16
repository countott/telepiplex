# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import re
import secrets
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
        self.selection_nonce = secrets.token_hex(8)
        self.step_result = None

    def as_dict(self, candidate_index=0):
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "rating_key": self.rating_key,
            "part_id": self.part_id,
            "candidates": deepcopy(self.candidates),
            "candidate_index": int(candidate_index),
            "selection_nonce": self.selection_nonce,
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
        persisted = deepcopy(
            ((job.get("step_results") or {}).get("scanning") or {})
        )
        library_results = dict(persisted.get("libraries") or {})
        target_results = dict(persisted.get("targets") or {})
        groups = {}
        for target in targets:
            target_id = str(target.get("target_id") or "")
            previous = target_results.get(target_id) or {}
            if (
                previous.get("status") == "success"
                and previous.get("rating_key")
            ):
                library_id = str(previous.get("library_id") or "")
                if library_id:
                    group = groups.setdefault(
                        library_id,
                        {"targets": [], "unresolved": []},
                    )
                    group["targets"].append(target)
                    continue
            try:
                library_id = str(self._route_library(job, target))
            except Exception as exc:
                target_results[target_id] = {
                    "status": "warning",
                    "final_path": str(target.get("final_path") or ""),
                    "message": self._safe_error(exc),
                }
                continue
            group = groups.setdefault(
                library_id,
                {"targets": [], "unresolved": []},
            )
            group["targets"].append(target)
            group["unresolved"].append(target)

        scan_result = {
            "status": "started",
            "libraries": library_results,
            "targets": target_results,
        }

        def persist_scan():
            current = self.jobs.get(job["id"])
            rating_key = next((
                str(result.get("rating_key") or "")
                for target_id, result in target_results.items()
                if (
                    target_id in target_ids
                    and isinstance(result, dict)
                    and result.get("status") == "success"
                    and result.get("rating_key")
                )
            ), "")
            return self.jobs.update(
                job["id"],
                rating_key=rating_key or None,
                step_results=self._merge_step(
                    current,
                    "scanning",
                    scan_result,
                ),
            )

        target_ids = {
            str(target.get("target_id") or "")
            for target in targets
        }
        persist_scan()
        for library_id, group in groups.items():
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled before library scan"
                )
            library_targets = list(group["targets"])
            unresolved = list(group["unresolved"])
            library_target_ids = [
                str(target.get("target_id") or "")
                for target in library_targets
            ]
            library_result = dict(library_results.get(library_id) or {})
            library_result["target_ids"] = library_target_ids
            scan_result["libraries"][library_id] = library_result
            if not unresolved:
                library_result["status"] = "success"
                library_result["located"] = len(library_targets)
                library_result["missing"] = 0
                persist_scan()
                continue

            if library_result.get("status") != "success":
                library_result.update({
                    "status": "started",
                    "located": sum(
                        1
                        for target_id in library_target_ids
                        if (
                            (target_results.get(target_id) or {}).get("status")
                            == "success"
                        )
                    ),
                    "missing": len(unresolved),
                })
                library_result.pop("message", None)
                persist_scan()
                try:
                    self.plex.scan_library(library_id)
                except Exception as exc:
                    message = self._safe_error(exc)
                    library_result.update({
                        "status": "warning",
                        "message": message,
                    })
                    for target in unresolved:
                        target_id = str(target.get("target_id") or "")
                        target_results[target_id] = {
                            "status": "warning",
                            "library_id": library_id,
                            "final_path": str(target.get("final_path") or ""),
                            "message": message,
                        }
                    persist_scan()
                    continue
                library_result["status"] = "success"
                library_result.pop("message", None)
                persist_scan()

            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled after library scan"
                )

            pending = {
                str(target.get("target_id") or ""): target
                for target in unresolved
            }
            deadline = self._clock() + self.scan_timeout
            while pending:
                if should_cancel and should_cancel():
                    raise PlexOperationCancelled(
                        "Plex operation cancelled while locating final paths"
                    )
                paths = [
                    str(target.get("final_path") or "")
                    for target in pending.values()
                ]
                try:
                    if hasattr(self.plex, "index_items_by_paths"):
                        indexed = self.plex.index_items_by_paths(
                            library_id,
                            paths,
                        )
                    else:
                        indexed = {
                            path: self.plex.find_item_by_path(
                                library_id,
                                path,
                            )
                            for path in paths
                        }
                except PlexOperationCancelled:
                    raise
                except Exception as exc:
                    message = self._safe_error(exc)
                    for target_id, target in pending.items():
                        target_results[target_id] = {
                            "status": "warning",
                            "library_id": library_id,
                            "final_path": str(target.get("final_path") or ""),
                            "message": message,
                        }
                    pending.clear()
                    persist_scan()
                    break

                for target_id, target in list(pending.items()):
                    final_path = str(target.get("final_path") or "")
                    item = (indexed or {}).get(final_path)
                    if isinstance(item, Exception):
                        target_results[target_id] = {
                            "status": "warning",
                            "library_id": library_id,
                            "final_path": final_path,
                            "message": self._safe_error(item),
                        }
                        pending.pop(target_id, None)
                        continue
                    if item is None:
                        continue
                    rating_key = str(item.get("rating_key") or "")
                    if not rating_key:
                        target_results[target_id] = {
                            "status": "warning",
                            "library_id": library_id,
                            "final_path": final_path,
                            "message": "Plex item is missing a rating key",
                        }
                    else:
                        target_results[target_id] = {
                            "status": "success",
                            "library_id": library_id,
                            "rating_key": rating_key,
                            "final_path": final_path,
                        }
                    pending.pop(target_id, None)

                located_in_library = sum(
                    1
                    for target_id in library_target_ids
                    if (
                        (target_results.get(target_id) or {}).get("status")
                        == "success"
                    )
                )
                library_result["located"] = located_in_library
                library_result["missing"] = (
                    len(library_target_ids) - located_in_library
                )
                persist_scan()
                if not pending or self._clock() >= deadline:
                    break
                self._sleep(self.scan_poll_interval)

            for target_id, target in pending.items():
                target_results[target_id] = {
                    "status": "warning",
                    "library_id": library_id,
                    "final_path": str(target.get("final_path") or ""),
                    "message": "Plex item was not found by final path",
                }
            located_in_library = sum(
                1
                for target_id in library_target_ids
                if (
                    (target_results.get(target_id) or {}).get("status")
                    == "success"
                )
            )
            library_result["located"] = located_in_library
            library_result["missing"] = (
                len(library_target_ids) - located_in_library
            )
            persist_scan()

        total = len(target_ids)
        located = sum(
            1
            for target_id in target_ids
            if (
                (target_results.get(target_id) or {}).get("status")
                == "success"
            )
        )
        scan_result["status"] = (
            "success" if located == total else "warning" if located else "failed"
        )
        scan_result["located"] = located
        scan_result["missing"] = total - located
        persist_scan()
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
                persisted_job = self.jobs.get(job["id"])
                persisted_step = deepcopy(
                    (
                        (persisted_job.get("step_results") or {})
                        .get(stage_name)
                    )
                    or {}
                )
                persisted_results = dict(
                    persisted_step.get("targets") or {}
                )
                persisted_results.update(deepcopy(results))
                target_result = dict(
                    persisted_results.get(target_id) or {}
                )
                target_result.update({
                    "status": "awaiting_selection",
                    "waiting": waiting,
                })
                persisted_results[target_id] = target_result
                persisted_step.update({
                    "status": "awaiting_selection",
                    "targets": persisted_results,
                    "waiting": waiting,
                    "confirmed_selections": confirmed_selections,
                })
                exc.step_result = persisted_step
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

    def _persisted_part_result(self, job, stage_name, part_id):
        step = (
            (job.get("step_results") or {}).get(str(stage_name))
            or {}
        )
        target_result = (
            (step.get("targets") or {}).get(self._target_id(job))
            or {}
        )
        parts = target_result.get("parts")
        if not isinstance(parts, list):
            parts = [target_result] if target_result.get("part_id") else []
        for result in parts:
            if (
                int((result or {}).get("part_id") or 0) == int(part_id)
                and self._selection_finished(result)
            ):
                return deepcopy(result)
        return None

    def _persisted_target_warnings(self, job, stage_name):
        step = (
            (job.get("step_results") or {}).get(str(stage_name))
            or {}
        )
        target_result = (
            (step.get("targets") or {}).get(self._target_id(job))
            or {}
        )
        return list(target_result.get("warnings") or [])

    def _persist_part_result(
        self,
        job,
        stage_name,
        part_result,
        *,
        warnings=None,
    ):
        current = self.jobs.get(job["id"])
        steps = deepcopy(current.get("step_results") or {})
        step = deepcopy(steps.get(str(stage_name)) or {})
        targets = dict(step.get("targets") or {})
        for target_id, result in (job.get("_stage_results") or {}).items():
            targets.setdefault(str(target_id), deepcopy(result))
        target_id = self._target_id(job)
        target_result = dict(targets.get(target_id) or {})
        parts = target_result.get("parts")
        if not isinstance(parts, list):
            parts = [target_result] if target_result.get("part_id") else []
        part_id = int(part_result.get("part_id") or 0)
        parts = [
            result
            for result in parts
            if int((result or {}).get("part_id") or 0) != part_id
        ]
        parts.append(deepcopy(part_result))
        target_result = {
            "status": "started",
            "parts": parts,
        }
        if warnings is not None:
            target_result["warnings"] = list(warnings)
        targets[target_id] = target_result
        step.update({
            "status": "started",
            "targets": targets,
        })
        step.pop("waiting", None)
        steps[str(stage_name)] = step
        updated = self.jobs.update(
            job["id"],
            step_results=steps,
            error=None,
        )
        job["step_results"] = deepcopy(updated.get("step_results") or {})
        return deepcopy(part_result)

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
        warnings = self._persisted_target_warnings(job, "audio")
        original_language = None
        if self.tmdb and tmdb_id:
            try:
                original_language = self.tmdb.details(
                    media_type,
                    tmdb_id,
                ).get("original_language")
            except Exception as exc:
                message = self._safe_error(exc)
                if message not in warnings:
                    warnings.append(message)
        else:
            message = "TMDB original language is unavailable"
            if message not in warnings:
                warnings.append(message)
        if (
            not original_language
            and "TMDB original language is unavailable" not in warnings
        ):
            warnings.append("TMDB original language is unavailable")
        audio_results = []
        for part in self.plex.list_streams(job["rating_key"]):
            persisted = self._persisted_part_result(
                job,
                "audio",
                part["id"],
            )
            if persisted:
                audio_results.append(persisted)
                continue
            confirmed = self._confirmed_selection(
                job,
                "audio",
                rating_key=job["rating_key"],
                part_id=part["id"],
            )
            if confirmed:
                part_result = {
                    "status": "confirmed",
                    "part_id": part["id"],
                    "stream_id": confirmed.get("id"),
                    "changed": True,
                    "confirmed": True,
                }
                audio_results.append(self._persist_part_result(
                    job,
                    "audio",
                    part_result,
                    warnings=warnings,
                ))
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
                message = (
                    "No original-language audio stream was found for part "
                    f"{part['id']}"
                )
                if message not in warnings:
                    warnings.append(message)
            if audio and not audio.get("selected"):
                self.plex.select_audio(
                    job["rating_key"],
                    part["id"],
                    audio["id"],
                )
            part_result = {
                "status": (
                    "success"
                    if audio and not audio.get("selected")
                    else "unchanged"
                ),
                "part_id": part["id"],
                "stream_id": audio.get("id") if audio else None,
                "changed": bool(audio and not audio.get("selected")),
            }
            audio_results.append(self._persist_part_result(
                job,
                "audio",
                part_result,
                warnings=warnings,
            ))
        return {
            "status": "warning" if warnings else "success",
            "parts": audio_results,
            "warnings": warnings,
        }

    def _subtitle_target(self, job):
        subtitle_results = []
        for part in self.plex.list_streams(job["rating_key"]):
            persisted = self._persisted_part_result(
                job,
                "subtitle",
                part["id"],
            )
            if persisted:
                subtitle_results.append(persisted)
                continue
            confirmed = self._confirmed_selection(
                job,
                "subtitle",
                rating_key=job["rating_key"],
                part_id=part["id"],
            )
            if confirmed:
                part_result = {
                    "status": "confirmed",
                    "part_id": part["id"],
                    "stream_id": confirmed.get("id"),
                    "source": (
                        "external"
                        if confirmed.get("external")
                        else "embedded"
                    ),
                    "changed": True,
                    "confirmed": True,
                }
                subtitle_results.append(self._persist_part_result(
                    job,
                    "subtitle",
                    part_result,
                ))
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
            part_result = {
                "status": (
                    "success"
                    if subtitle and not subtitle.get("selected")
                    else "unchanged"
                ),
                "part_id": part["id"],
                "stream_id": subtitle.get("id") if subtitle else None,
                "source": (
                    "external"
                    if subtitle and subtitle.get("external")
                    else "embedded" if subtitle else None
                ),
                "changed": bool(subtitle and not subtitle.get("selected")),
            }
            subtitle_results.append(self._persist_part_result(
                job,
                "subtitle",
                part_result,
            ))
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
        if job["state"] not in {"failed", "interrupted", "cancelled"}:
            raise ValueError(
                f"Plex job {job_id} is not retryable from state "
                f"{job['state']}"
            )
        steps = dict(job.get("step_results") or {})
        restart_index = 0
        for index, name in enumerate(STEP_ORDER):
            if not self._step_finished(job, name):
                restart_index = index
                break
        else:
            restart_index = len(STEP_ORDER)
        for name in STEP_ORDER[restart_index + 1:]:
            steps.pop(name, None)
        if not self.jobs.claim_retry(job_id, step_results=steps):
            current = self.jobs.get(job_id)
            state = str((current or {}).get("state") or "missing")
            raise ValueError(
                f"Plex job {job_id} is not retryable from state {state}"
            )
        return self.run_job(job_id)

    def pending_selection(self, job_id, *, selection_nonce=""):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        if job["state"] != "awaiting_selection":
            return None
        for name in STEP_ORDER:
            waiting = (
                ((job.get("step_results") or {}).get(name) or {})
                .get("waiting")
            )
            if isinstance(waiting, dict):
                if (
                    selection_nonce
                    and str(waiting.get("selection_nonce") or "")
                    != str(selection_nonce)
                ):
                    return None
                return deepcopy(waiting)
        return None

    def set_selection_index(self, job_id, index, *, selection_nonce):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        waiting = self.pending_selection(
            job_id,
            selection_nonce=selection_nonce,
        )
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
        selection_nonce,
        should_cancel=None,
        on_stage=None,
    ):
        waiting = self.set_selection_index(
            job_id,
            index,
            selection_nonce=selection_nonce,
        )
        job = self.jobs.get(job_id)
        if (
            job["state"] != "awaiting_selection"
            or str(waiting.get("selection_nonce") or "")
            != str(selection_nonce or "")
        ):
            raise ValueError(f"Plex job {job_id} is not awaiting a selection")
        candidate = deepcopy(waiting["candidates"][int(index)])
        kind = str(waiting["kind"])
        rating_key = str(waiting.get("rating_key") or "")
        part_id = int(waiting.get("part_id") or 0)
        if should_cancel and should_cancel():
            raise PlexOperationCancelled(
                f"Plex operation cancelled before {kind} selection"
            )
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

    def cancel_pending_selection(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        if job["state"] not in {"awaiting_selection", "cancelled"}:
            raise ValueError(
                f"Plex job {job_id} is not awaiting a selection"
            )
        steps = deepcopy(job.get("step_results") or {})
        for name in STEP_ORDER:
            step = steps.get(name)
            if not isinstance(step, dict):
                continue
            waiting = step.pop("waiting", None)
            if not isinstance(waiting, dict):
                continue
            step["status"] = "cancelled"
            target_id = str(waiting.get("target_id") or "")
            target_result = (step.get("targets") or {}).get(target_id)
            if isinstance(target_result, dict):
                target_result.pop("waiting", None)
                target_result["status"] = "cancelled"
        return self.jobs.update(
            job_id,
            state="cancelled",
            step_results=steps,
            error="cancelled while awaiting enhancement selection",
        )

    def resume_incomplete_jobs(self, executor=None):
        jobs = [
            job
            for job in self.jobs.list(1000)
            if job["state"] not in {
                "completed",
                "awaiting_selection",
                "cancelled",
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

    def scan_libraries(self, library_ids=None, *, should_cancel=None):
        libraries = [
            dict(library)
            for library in self.plex.list_libraries()
            if str((library or {}).get("id") or "").strip()
        ]
        for library in libraries:
            library["id"] = str(library["id"])
        by_id = {
            str(library["id"]): library
            for library in libraries
        }
        if library_ids is None:
            requested_ids = list(by_id)
        else:
            requested_ids = []
            for library_id in library_ids:
                library_id = str(library_id or "").strip()
                if library_id and library_id not in requested_ids:
                    requested_ids.append(library_id)

        result = {"succeeded": [], "failed": []}
        for library_id in requested_ids:
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled before manual library scan"
                )
            library = dict(by_id.get(library_id) or {
                "id": library_id,
                "title": library_id,
            })
            if library_id not in by_id:
                library["error"] = "Plex library is no longer available"
                result["failed"].append(library)
                continue
            try:
                self.plex.scan_library(library_id)
            except Exception as exc:
                library["error"] = self._safe_error(exc)
                result["failed"].append(library)
            else:
                result["succeeded"].append(library)
            if should_cancel and should_cancel():
                raise PlexOperationCancelled(
                    "Plex operation cancelled after manual library scan"
                )
        return result

    def inspect_item(self, rating_key):
        return self.plex.get_item(rating_key)

    def list_artwork_candidates(self, rating_key):
        item = self.plex.get_item(rating_key)
        tmdb, fanart, warnings = self._artwork_candidates(item)
        return {
            "tmdb": tmdb,
            "fanart": fanart,
            "plex": self.plex.list_posters(rating_key),
            "warnings": warnings,
        }

    def _list_stream_candidates(self, rating_key, stream_key):
        return [
            {
                "part_id": int(part.get("id") or 0),
                "file": str(part.get("file") or ""),
                "candidates": deepcopy(list(part.get(stream_key) or [])),
            }
            for part in self.plex.list_streams(rating_key)
        ]

    def list_audio_candidates(self, rating_key):
        return self._list_stream_candidates(rating_key, "audio_streams")

    def list_subtitle_candidates(self, rating_key):
        return self._list_stream_candidates(rating_key, "subtitle_streams")

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def list_jobs(self, limit=50):
        return self.jobs.list(limit)

    @staticmethod
    def _normalize_action(action):
        aliases = {
            "plex_scan_library": "scan_library",
            "plex_set_textless_poster": "set_textless_poster",
            "plex_select_original_audio": "select_original_audio",
            "plex_select_chi_subtitle": "select_chi_subtitle",
            "plex_retry_job": "retry_job",
        }
        return aliases.get(str(action), str(action))

    @staticmethod
    def _validate_operation(action):
        if action not in {
            "scan_library",
            "set_textless_poster",
            "select_original_audio",
            "select_chi_subtitle",
            "retry_job",
        }:
            raise ValueError(f"Unsupported Plex operation: {action}")
        return action

    def prepare_operation(self, action, payload):
        action = self._validate_operation(self._normalize_action(action))
        payload = dict(payload or {})
        if action == "retry_job":
            job_id = int(payload.get("job_id") or 0)
            job = self.jobs.get(job_id)
            if not job:
                raise LookupError(f"Plex job not found: {job_id}")
            if job["state"] not in {"failed", "interrupted", "cancelled"}:
                raise ValueError(
                    f"Plex job {job_id} is not retryable from state "
                    f"{job['state']}"
                )
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

    def apply_operation(self, action, payload, confirmation_token):
        action = self._validate_operation(self._normalize_action(action))
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
        if action == "scan_library":
            self.plex.scan_library(payload["library_id"])
            return {"library_id": str(payload["library_id"])}
        if action == "set_textless_poster":
            return self.plex.set_poster_url(payload["rating_key"], payload["url"])
        if action == "select_original_audio":
            self.plex.select_audio(payload["rating_key"], payload["part_id"], payload["stream_id"])
            return dict(payload)
        if action == "select_chi_subtitle":
            self.plex.select_subtitle(payload["rating_key"], payload["part_id"], payload["stream_id"])
            return dict(payload)
        if action == "retry_job":
            return self.retry_job(payload["job_id"])
        raise ValueError(f"Unsupported Plex operation: {action}")
