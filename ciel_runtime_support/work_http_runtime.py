"""Composition adapter for Work HTTP metadata and request observation."""
import json
from pathlib import Path
from .work_http_controller import WorkHttpController, WorkHttpServices
from .work_parallel import WorkParallelStore
from .work_parallel_mcp import project as project_parallel
from .work_web_controller import WorkWebController


class WorkHttpRoutes:
    """Mount the data-free console shell beside the Work REST controller."""

    def __init__(self, api, web=None):
        self.api, self.web = api, web

    def get(self, handler, path):
        return bool(self.web is not None and self.web.handle_get(handler, path)) or self.api.get(handler, path)

    def post(self, handler, path, body):
        return self.api.post(handler, path, body)


def build_work_http(state_dir, instance_dir, load_config, current_provider, current_alias,
                    write_json, version, fingerprint, instance_id, observer, save_config=None, write_text=None):
    def read_scope():
        try:
            value = json.loads((Path(instance_dir) / "compaction-scope.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def current_identity():
        scope = read_scope()
        return str(scope.get("runtime") or "") + ":" + str(scope["session_id"]) if scope.get("session_id") else None

    def runtime_status(identity):
        cfg = load_config()
        provider, pcfg = current_provider(cfg)
        scope = read_scope()
        if str(scope.get("runtime")) + ":" + str(scope.get("session_id")) != identity:
            scope = None
        return {"ciel_runtime": {"version": version, "source_fingerprint": fingerprint, "instance_id": instance_id},
                "session_id": identity, "runtime": identity.split(":", 1)[0] if identity else None,
                "configured": {"provider": provider, "model": str(pcfg.get("current_model") or "") or None,
                               "alias": current_alias(cfg), "source": "configuration"},
                "epoch": scope.get("epoch") if scope else None, **observer.snapshot(scope)}

    def recent_errors(identity):
        error = runtime_status(identity).get("last_error")
        return [error] if error else []

    def parallel_store():
        from .runtime_paths import CONFIG_DIR
        return WorkParallelStore(Path(CONFIG_DIR) / "work-parallel.json")

    def parallel_status(identity, board):
        return project_parallel(parallel_store(), identity, board)

    api = WorkHttpController(WorkHttpServices(state_dir, load_config, current_identity,
        runtime_status, write_json, instance_id, recent_errors, parallel_status,
        save_config, parallel_store))
    return WorkHttpRoutes(api, WorkWebController(write_text) if write_text is not None else None)
