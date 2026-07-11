# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import re
import time

from . import plex_rules


STEP_ORDER = ("scanning", "locating", "matching", "localizing", "artwork", "streams")
GATING_STEPS = {"scanning", "locating", "matching"}


class WaitingForMatchConfirmation(RuntimeError):
    def __init__(self, candidates, kind="match"):
        super().__init__("Plex confirmation required")
        self.candidates = list(candidates or [])
        self.kind = str(kind)


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
        for value in (event.metadata, event.naming_metadata, completion.result.metadata):
            if isinstance(value, dict):
                metadata.update(value)
        return {
            "provider": str(event.provider or ""),
            "selected_path": str(event.selected_path or ""),
            "final_path": str(completion.result.final_path or event.final_path or ""),
            "resource_name": str(event.resource_name or ""),
            "user_id": int(event.user_id),
            "terminal_processor": str(completion.terminal_processor or ""),
            "metadata": metadata,
        }

    def enqueue_completion(self, completion):
        if not str(completion.terminal_processor or "").startswith("renaming."):
            return None
        payload = self._completion_payload(completion)
        identity = "\x1f".join(
            (payload["provider"], payload["final_path"], payload["resource_name"])
        )
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.jobs.create_or_get(key, payload)

    @staticmethod
    def _safe_error(exc):
        return str(exc).replace("\n", " ")[:500]

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

    def run_job(self, job_id):
        runners = {
            "scanning": self._scan,
            "locating": self._locate,
            "matching": self._match,
            "localizing": self._localize,
            "artwork": self._artwork,
            "streams": self._streams,
        }
        job = self.jobs.get(job_id)
        if not job:
            raise LookupError(f"Plex job not found: {job_id}")
        if job["state"] == "completed":
            return job
        for state in STEP_ORDER:
            job = self.jobs.get(job_id)
            if self._step_finished(job, state):
                continue
            self.jobs.update(job_id, state=state, error=None)
            try:
                step_result = runners[state](self.jobs.get(job_id))
            except WaitingForMatchConfirmation as exc:
                waiting = {
                    "status": "waiting",
                    "kind": exc.kind,
                    "candidates": exc.candidates,
                }
                self._record_step(job_id, state, waiting)
                return self.jobs.update(job_id, state="waiting_match_confirmation")
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

    def _route_library(self, job):
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

    def _scan(self, job):
        library_id = self._route_library(job)
        before = sorted(self.plex.snapshot_recent(library_id))
        self.plex.scan_library(library_id)
        return {"status": "success", "library_id": library_id, "before_rating_keys": before}

    @staticmethod
    def _candidate_identity(candidate):
        return (
            str(candidate.get("title") or "").strip().casefold(),
            int(candidate.get("year") or 0),
        )

    def _locate(self, job):
        scan = job["step_results"]["scanning"]
        library_id = scan["library_id"]
        before = set(scan.get("before_rating_keys") or [])
        deadline = self._clock() + self.scan_timeout
        candidates = []
        while True:
            candidates = self.plex.locate_candidates(library_id, before)
            if candidates or self._clock() >= deadline:
                break
            self._sleep(self.scan_poll_interval)
        if not candidates:
            raise LookupError("Plex scan completed but no new item was located")
        metadata = job["payload"].get("metadata") or {}
        expected = (
            str(metadata.get("title") or metadata.get("original_title") or "").strip().casefold(),
            int(metadata.get("year") or 0),
        )
        exact = [item for item in candidates if self._candidate_identity(item) == expected]
        chosen = exact[0] if len(exact) == 1 else candidates[0] if len(candidates) == 1 else None
        if chosen is None:
            raise WaitingForMatchConfirmation(candidates, kind="location")
        self.jobs.update(job["id"], rating_key=str(chosen["rating_key"]))
        return {"status": "success", "rating_key": str(chosen["rating_key"]), "candidates": candidates}

    def _match(self, job):
        rating_key = str(job.get("rating_key") or "")
        if not rating_key:
            raise LookupError("Plex rating key is missing")
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
            raise WaitingForMatchConfirmation(candidates, kind="match")
        fixed = self.plex.fix_match(rating_key, exact["guid"])
        if not plex_rules.external_ids_match(external_ids, fixed.get("guids")):
            raise RuntimeError("Plex match verification failed after fixMatch")
        return {"status": "success", "action": "fixed", "candidate": exact, "item": fixed}

    @staticmethod
    def _contains_chinese(value):
        return bool(re.search(r"[\u3400-\u9fff]", str(value or "")))

    def _localize(self, job):
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

    def _artwork(self, job):
        item = dict((job["step_results"].get("matching") or {}).get("item") or {})
        if not item:
            item = self.plex.get_item(job["rating_key"])
        item["external_ids"] = job["payload"].get("metadata", {}).get("external_ids") or {}
        tmdb_posters, fanart_posters, warnings = self._artwork_candidates(item)
        chosen = plex_rules.choose_textless_poster(tmdb_posters, fanart_posters)
        if chosen:
            self.plex.set_poster_url(job["rating_key"], chosen["url"])
            return {"status": "warning" if warnings else "success", "selected": chosen, "warnings": warnings}
        return {
            "status": "warning" if warnings else "unchanged",
            "message": "No automatic textless poster candidate",
            "plex_candidates": self.plex.list_posters(job["rating_key"]),
            "warnings": warnings,
        }

    def _streams(self, job):
        metadata = job["payload"].get("metadata") or {}
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

    def confirm_match(self, job_id, selection):
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
        return self.run_job(job_id)

    def resume_incomplete_jobs(self, executor=None):
        jobs = [job for job in self.jobs.list(1000) if job["state"] not in {"completed", "waiting_match_confirmation"}]
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
        }
        return aliases.get(str(action), str(action))

    def prepare_operation(self, action, payload):
        action = self._normalize_action(action)
        payload = dict(payload or {})
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
