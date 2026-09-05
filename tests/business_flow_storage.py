"""Stateful 115 transport double for real Feature / RPC composition tests."""
from copy import deepcopy
from pathlib import PurePosixPath
import threading


class Memory115:
    def __init__(self, filename="Fargo.1996.1080p.mkv", directories=0):
        self.filename = filename
        self.directories = directories
        self.nodes = {}
        self.added = []
        self.writes = []
        self.tree_reads = 0
        self.list_reads = 0
        self._lock = threading.RLock()

    def _put(self, path, *, directory, file_id=None):
        path = str(PurePosixPath(path))
        self.nodes[path] = {
            "file_id": file_id or f"node-{len(self.nodes) + 1}",
            "file_name": PurePosixPath(path).name,
            "is_dir": directory, "file_category": "0" if directory else "1",
            "size": 0 if directory else 300 * 1024 * 1024,
            "sha1": "" if directory else "a" * 40,
        }
        return deepcopy(self.nodes[path])

    def add_offline_task(self, link, selected_path):
        with self._lock:
            self.added.append((link, selected_path))
            self.create_dir_recursive(selected_path + "/Release")
            for index in range(self.directories):
                self.create_dir_recursive(selected_path + f"/Release/Extra{index:04d}")
            self._put(selected_path + "/Release/" + self.filename,
                      directory=False, file_id="downloaded-video")
        return True

    def wait_for_download(self, link, **kwargs):
        return {"resource_name": "Release", "info_hash": "fixture-hash", "progress": 100}

    def get_file_info(self, path):
        with self._lock:
            node = self.nodes.get(str(PurePosixPath(path)))
            return deepcopy(node) if node else None

    def get_file_info_batch(self, paths):
        return {path: self.get_file_info(path) for path in paths}

    def get_file_info_by_id(self, file_id):
        with self._lock:
            return next((deepcopy(node) for node in self.nodes.values()
                         if node["file_id"] == str(file_id)), None)

    def get_file_list(self, params):
        with self._lock:
            self.list_reads += 1
            parent = next((path for path, node in self.nodes.items()
                           if node["file_id"] == str(params["cid"])), None)
            items = [deepcopy(node) for path, node in sorted(self.nodes.items())
                     if str(PurePosixPath(path).parent) == parent and path != parent]
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
            return {"list": items[offset:offset + limit], "count": len(items), "offset": offset}

    def get_file_tree(self, path, **kwargs):
        from telepiplex_download.client import Open115Client
        self.tree_reads += 1
        # Exercise the production complete-tree scanner, replacing only 115 I/O.
        return Open115Client.get_file_tree(self, path, **kwargs)

    def is_directory(self, path):
        return bool((self.get_file_info(path) or {}).get("is_dir"))

    def create_dir_recursive(self, path):
        with self._lock:
            path = PurePosixPath(path)
            for parent in reversed((path, *path.parents)):
                key = str(parent)
                if key not in self.nodes:
                    self._put(key, directory=True)
            return deepcopy(self.nodes[str(path)])

    def rename(self, source, leaf):
        with self._lock:
            target = str(PurePosixPath(source).with_name(leaf))
            if target in self.nodes or source not in self.nodes:
                return False
            self.nodes[target] = self.nodes.pop(source)
            self.nodes[target]["file_name"] = leaf
            self.writes.append(("rename", source, target))
            return True

    def move_file(self, source, target_dir):
        with self._lock:
            target = str(PurePosixPath(target_dir) / PurePosixPath(source).name)
            if source not in self.nodes or target in self.nodes or target_dir not in self.nodes:
                return False
            self.nodes[target] = self.nodes.pop(source)
            self.writes.append(("move", source, target))
            return True

    def move_file_detailed(self, source, target):
        moved = self.move_file(source, target)
        return {"state": "moved" if moved else "copy_failed", "copied": moved,
                "source_deleted": moved, "source_path": source,
                "target_path": str(PurePosixPath(target) / PurePosixPath(source).name)}

    def move_files_by_id(self, file_ids, target_dir_id):
        with self._lock:
            by_id = {node["file_id"]: path for path, node in self.nodes.items()}
            target = by_id[str(target_dir_id)]
            submitted = all(self.move_file(by_id[str(file_id)], target) for file_id in file_ids)
            return {"state": "submitted" if submitted else "provider_rejected",
                    "submitted": submitted, "file_ids": file_ids, "target_dir_id": target_dir_id}

    def delete_single_file(self, path):
        with self._lock:
            # Only empty-directory cleanup is expected in the successful movie case.
            if path not in self.nodes or any(other.startswith(path.rstrip('/') + '/') for other in self.nodes):
                return False
            self.nodes.pop(path)
            self.writes.append(("delete", path))
            return True

    def del_offline_task(self, info_hash, del_source_file=0):
        self.writes.append(("delete_task", info_hash, del_source_file))
        return True
