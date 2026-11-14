"""One web worker, plus the mail checker after that worker is ready."""

import os

workers = 1
threads = 4


def post_worker_init(worker):
    enabled = os.environ.get("MAIL_WORKER")
    if enabled is None:
        enabled = "1" if os.environ.get("RENDER") == "true" else "0"
    if enabled == "1":
        from mail_worker import start

        start()
