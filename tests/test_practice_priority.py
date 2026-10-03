import io
import threading

import pytest
from paperspeak import db, runtime


def test_long_language_model_step_yields_to_new_recording(database, monkeypatch):
    model = runtime.Runtime()
    model.job_kind = "lesson"
    model.mode = "qwen-q8"
    monkeypatch.setattr(model, "ensure_llm", lambda profile=None: None)
    entered = threading.Event()
    stopped = threading.Event()
    close_modes = []

    def close(immediate=False):
        close_modes.append(immediate)
        stopped.set()

    monkeypatch.setattr(model, "close", close)

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class SlowClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            entered.set()
            assert stopped.wait(3), "The lesson did not yield to the recording"
            return Response({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(
        runtime.httpx, "post", lambda *args, **kwargs: Response({"tokens": [1, 2]})
    )
    monkeypatch.setattr(runtime.httpx, "Client", SlowClient)

    def record():
        assert entered.wait(3)
        db.enqueue("practice", "new-recording", priority=0)

    thread = threading.Thread(target=record)
    thread.start()
    try:
        with pytest.raises(runtime.PracticePreempted):
            model.ask("Explain this paper", thinking=False)
    finally:
        stopped.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert close_modes == [True]


def test_recording_priority_does_not_interrupt_recordings(database):
    db.enqueue("practice", "next-recording", priority=0)
    model = runtime.Runtime()
    model.job_kind = "lesson"
    assert model.practice_waiting()
    model.job_kind = "practice"
    assert not model.practice_waiting()


def test_long_speech_step_yields_to_new_recording(database, monkeypatch):
    model = runtime.Runtime()
    model.job_kind = "discover"
    model.mode = "asr"

    class BusyActor:
        stdin = io.StringIO()
        stdout = object()

        def poll(self):
            return None

    model.process = BusyActor()
    closed = []
    monkeypatch.setattr(
        model, "close", lambda immediate=False: closed.append(immediate)
    )

    def wait_for_actor(*args):
        db.enqueue("practice", "new-recording", priority=0)
        return [], [], []

    monkeypatch.setattr("select.select", wait_for_actor)
    with pytest.raises(runtime.PracticePreempted):
        model.speech("phoneme", {"audio": "unused.wav"})
    assert closed == [True]


def test_language_model_completes_when_no_recording_waits(database, monkeypatch):
    model = runtime.Runtime()
    model.job_kind = "lesson"
    model.mode = "qwen-q8"
    monkeypatch.setattr(model, "ensure_llm", lambda profile=None: None)

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class FastClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            return Response({"choices": [{"message": {"content": '{"ok":true}'}}]})

    monkeypatch.setattr(
        runtime.httpx, "post", lambda *args, **kwargs: Response({"tokens": [1, 2]})
    )
    monkeypatch.setattr(runtime.httpx, "Client", FastClient)
    assert model.ask("Explain this paper", thinking=False) == {"ok": True}


def test_image_generation_yields_gpu_to_new_recording(database, monkeypatch):
    model = runtime.Runtime()
    model.job_kind = "thumbnail"
    model.mode = "image"

    class BusyActor:
        stdin = io.StringIO()
        stdout = object()

        def poll(self):
            return None

    model.process = BusyActor()
    closed = []
    monkeypatch.setattr(
        model, "close", lambda immediate=False: closed.append(immediate)
    )

    def wait_for_actor(*args):
        db.enqueue("practice", "recording-during-image", priority=0)
        return [], [], []

    monkeypatch.setattr("select.select", wait_for_actor)
    with pytest.raises(runtime.PracticePreempted):
        model.image({"prompt": "local pixel art", "output": "unused.png"})
    assert closed == [True]


def test_service_restart_is_not_counted_as_model_output_failure(database):
    model = runtime.Runtime()

    @runtime.restart_safe
    def stopped_call(self):
        self.shutdown_requested = True
        raise OSError("server disconnected while stopping")

    with pytest.raises(runtime.PracticePreempted, match="service restart"):
        stopped_call(model)
    with pytest.raises(runtime.PracticePreempted, match="service restart"):
        model.image({"prompt": "must not load a new model during shutdown"})


def test_gpu_memory_race_waits_without_becoming_a_model_repair(database, monkeypatch):
    model = runtime.Runtime()
    closed = []
    monkeypatch.setattr(
        model, "close", lambda immediate=False: closed.append(immediate)
    )

    @runtime.restart_safe
    def memory_race(self):
        raise RuntimeError("CUDA out of memory while another process was using the GPU")

    with pytest.raises(runtime.GPUUnavailable):
        memory_race(model)
    assert closed == [True]
