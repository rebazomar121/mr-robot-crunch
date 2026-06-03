"""Resource monitor: every 5s check CPU% and memory%.

When CPU usage OR memory usage exceeds the threshold the monitor sets the
shared pause event and records the reason. The parent run loop notices the
pause and asks the user whether to continue or stop — the monitor never reads
stdin itself.
"""

import threading
import time

try:
    import psutil
except ImportError:  # optional: only the interactive run loop needs it
    psutil = None

CHECK_INTERVAL = 5.0
DEFAULT_THRESHOLD = 95.0


class ResourceMonitor(threading.Thread):
    def __init__(self, pause_event, stop_event, threshold=DEFAULT_THRESHOLD):
        super().__init__(daemon=True)
        self.pause_event = pause_event
        self.stop_event = stop_event
        self.threshold = threshold
        self.last_cpu = 0.0
        self.last_mem = 0.0
        self.auto_pause_reason = None  # set when the monitor forces a pause
        # Prime cpu_percent so the first real reading is meaningful.
        if psutil is not None:
            psutil.cpu_percent(interval=None)

    def run(self) -> None:
        if psutil is None:
            return  # no psutil: resource auto-pause disabled, run continues normally
        # Poll in small slices so we react quickly to stop_event.
        elapsed = 0.0
        while not self.stop_event.is_set():
            time.sleep(0.5)
            elapsed += 0.5
            if elapsed < CHECK_INTERVAL:
                continue
            elapsed = 0.0
            try:
                self.last_cpu = psutil.cpu_percent(interval=None)
                self.last_mem = psutil.virtual_memory().percent
            except Exception:
                continue

            over = self.last_cpu > self.threshold or self.last_mem > self.threshold
            if over and not self.pause_event.is_set():
                self.auto_pause_reason = (
                    f"high system load (CPU {self.last_cpu:.0f}% / "
                    f"MEM {self.last_mem:.0f}%, threshold {self.threshold:.0f}%)"
                )
                self.pause_event.set()

    def snapshot(self):
        return self.last_cpu, self.last_mem
