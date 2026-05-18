import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone

import modal


APP_NAME = "client-voice-agent"
SECRET_NAME = "client-voice-agent-env"
APP_DIR = "/app"

REQUIRED_ENV_VARS = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
    "GEMINI_API_KEY",
    "WEBHOOK_URL",
)

OPTIONAL_ENV_VARS = (
    "SIP_TRUNK_ID",
    "SIP_DOMAIN",
    "DEFAULT_TRANSFER_NUMBER",
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ffmpeg")
    .pip_install_from_requirements("requirements.txt")
    .add_local_dir("src", remote_path=f"{APP_DIR}/src", copy=True)
    .add_local_file("knowledgebase.md", remote_path=f"{APP_DIR}/knowledgebase.md", copy=True)
    .workdir(APP_DIR)
    .env(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LIVEKIT_AGENTS_LOG_LEVEL": "INFO",
        }
    )
    .run_commands("python src/agent.py download-files")
)

app = modal.App(APP_NAME)


@app.cls(
    image=image,
    secrets=[modal.Secret.from_name(SECRET_NAME)],
    min_containers=1,
    max_containers=1,
    scaledown_window=20 * 60,
    timeout=24 * 60 * 60,
    cpu=2.0,
    memory=2048,
)
class LiveKitReceptionistWorker:
    @modal.enter()
    def start_worker(self):
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._stopping = False
        self._validate_environment()

        command = ["python", "-u", "src/agent.py", "start"]
        print(f"[modal] Starting LiveKit worker: {' '.join(command)}", flush=True)

        self.process = subprocess.Popen(
            command,
            cwd=APP_DIR,
            stdout=None,
            stderr=None,
            start_new_session=True,
        )

        time.sleep(3)
        return_code = self.process.poll()
        if return_code is not None:
            raise RuntimeError(f"LiveKit worker exited during startup: {return_code}")

        self.monitor_thread = threading.Thread(
            target=self._monitor_worker,
            name="livekit-worker-monitor",
            daemon=True,
        )
        self.monitor_thread.start()

        print(
            f"[modal] LiveKit worker registered and running with pid={self.process.pid}",
            flush=True,
        )

    def _validate_environment(self):
        missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                f"Missing required Modal secret values: {joined}. "
                f"Create them in the '{SECRET_NAME}' Modal secret."
            )

        configured_optional = [name for name in OPTIONAL_ENV_VARS if os.getenv(name)]
        print(
            "[modal] Required environment is present. "
            f"Optional SIP vars configured: {configured_optional or 'none'}",
            flush=True,
        )

    def _monitor_worker(self):
        return_code = self.process.wait()
        if self._stopping:
            return

        print(
            f"[modal] LiveKit worker stopped unexpectedly with code={return_code}. "
            "Exiting container so Modal can replace the warm worker.",
            flush=True,
        )
        os._exit(return_code if return_code else 1)

    @modal.method()
    def health(self):
        return_code = self.process.poll()
        return {
            "app": APP_NAME,
            "status": "running" if return_code is None else "stopped",
            "pid": self.process.pid,
            "return_code": return_code,
            "started_at": self.started_at,
        }

    @modal.method()
    def wait(self):
        while True:
            return_code = self.process.poll()
            if return_code is not None:
                raise RuntimeError(f"LiveKit worker stopped with code={return_code}")
            time.sleep(30)

    @modal.exit()
    def stop_worker(self):
        self._stopping = True
        process = getattr(self, "process", None)
        if process is None or process.poll() is not None:
            return

        print("[modal] Stopping LiveKit worker", flush=True)
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=20)
        except Exception as exc:
            print(f"[modal] Graceful stop failed, killing worker: {exc}", flush=True)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                pass


@app.local_entrypoint()
def main(action: str = "health"):
    worker = LiveKitReceptionistWorker()
    if action == "wait":
        worker.wait.remote()
    else:
        print(worker.health.remote())
