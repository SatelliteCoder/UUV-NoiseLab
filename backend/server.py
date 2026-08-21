# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from uuv_noise.simulation import SOURCE_LABELS, run_case


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"
JOBS_DIR = ROOT_DIR / "jobs"
RUNTIME_DIR = ROOT_DIR / "runtime"
SOURCE_TYPES = ("point", "line", "surface", "volume")
SOURCE_LABELS = {**SOURCE_LABELS, "all": "全部四类"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def clamp_number(value, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(minimum, min(maximum, number))


def clamp_int(value, default: int, minimum: int, maximum: int) -> int:
    return int(round(clamp_number(value, default, minimum, maximum)))


def nested(data: dict, key: str) -> dict:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def normalize_config(data: dict) -> dict:
    raw_source_type = str(data.get("source_type", "point")).strip().lower()
    if raw_source_type not in (*SOURCE_TYPES, "all"):
        raw_source_type = "point"

    uuv = nested(data, "uuv")
    source = nested(data, "source")
    receiver = nested(data, "receiver")
    ambient = nested(data, "ambient")
    prop_d = clamp_number(uuv.get("propeller_diameter_m"), 0.24, 0.03, 5.0)

    return {
        "source_type": raw_source_type,
        "fs": clamp_int(data.get("fs"), 24000, 8000, 96000),
        "duration_s": clamp_number(data.get("duration_s"), 4.0, 1.0, 60.0),
        "random_seed": clamp_int(data.get("random_seed"), 20260820, 1, 999999999),
        "uuv": {
            "length_m": clamp_number(uuv.get("length_m"), 3.2, 0.3, 30.0),
            "diameter_m": clamp_number(uuv.get("diameter_m"), 0.45, 0.05, 5.0),
            "depth_m": clamp_number(uuv.get("depth_m"), 50.0, 1.0, 1000.0),
            "speed_mps": clamp_number(uuv.get("speed_mps"), 3.0, 0.0, 25.0),
            "rpm": clamp_number(uuv.get("rpm"), 720.0, 0.0, 5000.0),
            "blade_count": clamp_int(uuv.get("blade_count"), 4, 2, 12),
            "propeller_diameter_m": prop_d,
            "propeller_pitch_m": clamp_number(uuv.get("propeller_pitch_m"), 0.75 * prop_d, 0.03, 5.0),
            "displacement_t": clamp_number(uuv.get("displacement_t"), 1.2, 0.05, 5000.0),
        },
        "source": {
            "heading_deg": clamp_number(source.get("heading_deg"), 20.0, -180.0, 180.0),
            "line_elements": clamp_int(source.get("line_elements"), 21, 3, 81),
            "surface_axial_elements": clamp_int(source.get("surface_axial_elements"), 10, 3, 40),
            "surface_circum_elements": clamp_int(source.get("surface_circum_elements"), 8, 4, 48),
            "volume_axial_elements": clamp_int(source.get("volume_axial_elements"), 6, 3, 30),
            "volume_radial_elements": clamp_int(source.get("volume_radial_elements"), 2, 1, 8),
            "volume_circum_elements": clamp_int(source.get("volume_circum_elements"), 8, 4, 48),
        },
        "receiver": {
            "x_m": clamp_number(receiver.get("x_m"), 600.0, -10000.0, 10000.0),
            "y_m": clamp_number(receiver.get("y_m"), 160.0, -10000.0, 10000.0),
            "z_m": clamp_number(receiver.get("z_m"), -45.0, -1000.0, 100.0),
        },
        "ambient": {
            "enabled": bool(ambient.get("enabled", True)),
            "rms_uPa": clamp_number(ambient.get("rms_uPa"), 800.0, 0.0, 100000.0),
            "slope_db_decade": clamp_number(ambient.get("slope_db_decade"), -17.0, -40.0, 10.0),
        },
        "propagation": {
            "sound_speed_mps": 1500.0,
        },
    }


def add_urls_to_case(job_id: str, case_summary: dict, case_folder: str) -> dict:
    prefix = f"/jobs/{job_id}/"
    if case_folder:
        prefix += f"{case_folder}/"
    case_summary["file_urls"] = {key: prefix + name for key, name in case_summary.get("files", {}).items()}
    return case_summary


def summarize_job(job: dict) -> dict:
    cases = job.get("cases") if isinstance(job.get("cases"), list) else []
    mode = str(job.get("config", {}).get("source_type", "point"))
    latest_case = cases[-1] if cases else {}
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "message": job.get("message"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "engine": job.get("engine", "python"),
        "mode": mode,
        "mode_label": SOURCE_LABELS.get(mode, mode),
        "case_count": len(cases),
        "current_case": job.get("current_case"),
        "latest_case_label": latest_case.get("source_type_label"),
    }


def update_job(job_id: str, patch: dict) -> dict:
    path = JOBS_DIR / job_id / "job.json"
    job = read_json(path)
    job.update(patch)
    job["updated_at"] = now_iso()
    write_json(path, job)
    return job


def list_recent_jobs(limit: int = 12) -> list[dict]:
    items = []
    for job_dir in sorted((p for p in JOBS_DIR.iterdir() if p.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True):
        job_path = job_dir / "job.json"
        if not job_path.exists():
            continue
        try:
            items.append(summarize_job(read_json(job_path)))
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


def run_python_case(job_id: str, case_type: str, base_config: dict, case_output_dir: Path) -> dict:
    started = time.time()
    case_output_dir.mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(base_config)
    config["source_type"] = case_type
    log_path = case_output_dir / "run.log"
    log_path.write_text(f"Python UUV noise simulation started at {now_iso()}\nCase: {case_type}\n", encoding="utf-8")
    summary = run_case(config, case_type, case_output_dir)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"Finished at {now_iso()}\n")
    summary["elapsed_s"] = round(time.time() - started, 2)
    case_folder = case_type if base_config["source_type"] == "all" else ""
    return add_urls_to_case(job_id, summary, case_folder)


def run_job(job_id: str) -> None:
    job_dir = JOBS_DIR / job_id
    try:
        config = read_json(job_dir / "input_config.json")
        update_job(job_id, {"status": "running", "started_at": now_iso(), "message": "Python 正在计算"})

        requested = config["source_type"]
        case_types = SOURCE_TYPES if requested == "all" else (requested,)
        cases = []
        for index, case_type in enumerate(case_types, start=1):
            update_job(job_id, {"message": f"正在运行 {SOURCE_LABELS[case_type]} ({index}/{len(case_types)})", "current_case": case_type})
            case_output_dir = job_dir / case_type if requested == "all" else job_dir
            cases.append(run_python_case(job_id, case_type, config, case_output_dir))

        write_json(job_dir / "web_job_summary.json", {"job_id": job_id, "mode": requested, "mode_label": SOURCE_LABELS.get(requested, requested), "engine": "python", "finished_at": now_iso(), "cases": cases})
        update_job(job_id, {"status": "succeeded", "finished_at": now_iso(), "message": "仿真完成", "engine": "python", "cases": cases})
    except Exception as exc:
        update_job(job_id, {"status": "failed", "finished_at": now_iso(), "message": str(exc), "engine": "python"})


def safe_job_id(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", value))


class AppHandler(BaseHTTPRequestHandler):
    server_version = "UUVNoisePython/0.2"

    def log_message(self, format, *args):
        print(f"[{now_iso()}] {self.address_string()} {format % args}")

    def send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path: Path, base_dir: Path) -> None:
        try:
            resolved = path.resolve()
            base = base_dir.resolve()
            if not resolved.is_file() or not resolved.is_relative_to(base):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(resolved))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self.send_json({"ok": True, "root_dir": str(ROOT_DIR), "engine": "python", "matlab_required": False})
            return
        if path == "/api/jobs":
            limit = 12
            query_limit = parsed.query
            if query_limit:
                try:
                    for part in query_limit.split("&"):
                        key, _, value = part.partition("=")
                        if key == "limit" and value:
                            limit = max(1, min(50, int(value)))
                except ValueError:
                    limit = 12
            self.send_json({"jobs": list_recent_jobs(limit)})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            if not safe_job_id(job_id):
                self.send_json({"error": "bad job id"}, 400)
                return
            job_path = JOBS_DIR / job_id / "job.json"
            if not job_path.exists():
                self.send_json({"error": "job not found"}, 404)
                return
            self.send_json(read_json(job_path))
            return
        if path.startswith("/jobs/"):
            parts = path.split("/", 3)
            if len(parts) < 4 or not safe_job_id(parts[2]):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.serve_file(JOBS_DIR / parts[2] / parts[3], JOBS_DIR / parts[2])
            return
        if path in ("/", "/index.html"):
            self.serve_file(FRONTEND_DIR / "index.html", FRONTEND_DIR)
            return
        self.serve_file(FRONTEND_DIR / path.lstrip("/"), FRONTEND_DIR)

    def do_POST(self):
        if urlparse(self.path).path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 200000:
            self.send_json({"error": "request too large"}, 413)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json({"error": "bad json"}, 400)
            return

        config = normalize_config(payload if isinstance(payload, dict) else {})
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        write_json(job_dir / "input_config.json", config)
        job = {"job_id": job_id, "status": "queued", "message": "等待运行", "created_at": now_iso(), "updated_at": now_iso(), "engine": "python", "config": config, "cases": []}
        write_json(job_dir / "job.json", job)
        threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
        self.send_json(job, 202)


def main() -> None:
    port = int(os.environ.get("UUV_WEB_PORT", "8765"))
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    mimetypes.add_type("audio/wav", ".wav")
    mimetypes.add_type("text/csv", ".csv")
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"UUV noise Python web system: http://127.0.0.1:{port}")
    print(f"Local root: {ROOT_DIR}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
