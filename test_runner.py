# test_runner.py
"""
Interactive GUI test runner for FaceLock.
Shows a live camera feed alongside test execution so results are visible in
real time. Tests run the same logic as tests/, but with visual feedback.

Launch: python main.py test-runner
Note: pause or stop the core service before running (camera must be free).
"""

import threading
import time
import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageTk

import config
from modules.face_detector import FaceDetector
from modules.face_encoder import (
    extract_embedding, average_embeddings,
    embedding_to_bytes, bytes_to_embedding, compare_embedding,
)
from modules.authenticator import Authenticator

PREVIEW_W = 480
PREVIEW_H = 360

# Dot colors per test state
_COLORS = {
    "pending": ("#555555", "#777777"),   # dot, label
    "running": ("#e6a817", "white"),
    "pass":    ("#1a8f1a", "#88ff88"),
    "fail":    ("#cc0000", "#ff8888"),
    "skip":    ("#444444", "#666666"),
}


@dataclass
class TC:
    """A single test case."""
    tc_id: str
    name: str
    needs_camera: bool = True
    status: str = "pending"
    message: str = ""


# Ordered list of all test cases shown in the UI.
ALL_TESTS: list[TC] = [
    TC("TC4",  "Wrong face rejects auth",          needs_camera=False),
    TC("TC5a", "Face detected in live frame"),
    TC("TC5b", "Bounding box valid shape"),
    TC("TC5c", "Exactly one face in frame"),
    TC("TC1",  "Embedding is 128-dimensional"),
    TC("TC6",  "Embedding serialization roundtrip"),
    TC("TC8",  "Same face matches within tolerance"),
    TC("Enrl", "Session enrollment (30 frames)",   needs_camera=True),
    TC("TC2",  "Auth on consecutive matches"),
    TC("TC3",  "Streak resets on no face"),
    TC("TC7",  "Auth after serialization"),
]


class TestRunner(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Test Runner")
        self.resizable(False, False)
        self.configure(bg="#111122")

        self._alive = True
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._boxes: list = []

        self._detector = FaceDetector(config.TFLITE_MODEL_PATH)
        self._enrolled: np.ndarray | None = None  # set during enrollment phase

        # Per-TC UI row refs: tc_id → {dot, label}
        self._rows: dict[str, dict] = {}

        self._build()
        threading.Thread(target=self._camera_loop, daemon=True).start()
        self._tick()  # starts the UI refresh loop
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        tk.Frame(self, bg="#1a73e8", height=4).pack(fill="x")

        header = tk.Frame(self, bg="#111122", padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="FaceLock — Test Runner",
                 font=("Segoe UI", 14, "bold"),
                 bg="#111122", fg="white").pack(side="left")

        body = tk.Frame(self, bg="#111122", padx=16, pady=4)
        body.pack(fill="both", expand=True)

        # ---- Left: camera + status ----
        left = tk.Frame(body, bg="#111122")
        left.pack(side="left", anchor="n", padx=(0, 16))

        self._canvas = tk.Canvas(left, width=PREVIEW_W, height=PREVIEW_H,
                                  bg="#000000", highlightthickness=1,
                                  highlightbackground="#333355")
        self._canvas.pack()

        self._status_var = tk.StringVar(master=self, value="Ready — press a Run button")
        tk.Label(left, textvariable=self._status_var,
                 font=("Segoe UI", 10), bg="#111122", fg="#aaaaaa",
                 wraplength=PREVIEW_W, justify="center").pack(pady=(8, 4))

        self._prog_var = tk.DoubleVar(master=self, value=0)
        ttk.Progressbar(left, variable=self._prog_var, maximum=100,
                        length=PREVIEW_W, mode="determinate").pack()

        # ---- Right: test list + buttons ----
        right = tk.Frame(body, bg="#111122", width=270)
        right.pack(side="left", fill="y", anchor="n")
        right.pack_propagate(False)

        # Buttons anchored to bottom so they're always visible regardless of
        # how many test rows are above them.
        btn_frame = tk.Frame(right, bg="#111122")
        btn_frame.pack(side="bottom", fill="x", pady=(8, 0))

        self._summary_var = tk.StringVar(master=self, value="")
        tk.Label(btn_frame, textvariable=self._summary_var,
                 font=("Segoe UI", 9), bg="#111122",
                 fg="#888888", justify="left").pack(anchor="w", pady=(0, 6))

        self._btn_unit = tk.Button(
            btn_frame, text="▶  Run Unit Tests",
            font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2",
            bg="#1a73e8", fg="white", padx=10, pady=6,
            command=lambda: self._start(camera=False))
        self._btn_unit.pack(fill="x", pady=(0, 4))

        self._btn_all = tk.Button(
            btn_frame, text="▶  Run All  (camera required)",
            font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2",
            bg="#1a8f1a", fg="white", padx=10, pady=6,
            command=lambda: self._start(camera=True))
        self._btn_all.pack(fill="x")

        # Test rows packed after buttons so they fill remaining space.
        tk.Label(right, text="Tests", font=("Segoe UI", 11, "bold"),
                 bg="#111122", fg="white").pack(anchor="w", pady=(0, 6))

        for tc in ALL_TESTS:
            row_frame = tk.Frame(right, bg="#1e1e32", padx=8, pady=5)
            row_frame.pack(fill="x", pady=(0, 3))

            dot = tk.Label(row_frame, text="●", font=("Segoe UI", 10),
                           bg="#1e1e32", fg="#555555")
            dot.pack(side="left")

            lbl = tk.Label(row_frame,
                           text=f"{tc.tc_id}  {tc.name}",
                           font=("Segoe UI", 9), bg="#1e1e32",
                           fg="#777777", anchor="w", justify="left")
            lbl.pack(side="left", padx=(6, 0), fill="x", expand=True)

            self._rows[tc.tc_id] = {"dot": dot, "label": lbl, "tc": tc}

        # bottom padding
        tk.Frame(self, bg="#111122", height=12).pack()

    # ------------------------------------------------------------------
    # Camera feed
    # ------------------------------------------------------------------

    def _camera_loop(self) -> None:
        # Runs in a daemon thread — continuously captures frames.
        cap = cv2.VideoCapture(0)
        while self._alive:
            ret, frame = cap.read()
            if ret:
                with self._frame_lock:
                    self._latest_frame = frame
            time.sleep(0.033)
        cap.release()

    def _get_frame(self) -> np.ndarray | None:
        with self._frame_lock:
            f = self._latest_frame
            return f.copy() if f is not None else None

    def _tick(self) -> None:
        # Runs on the tkinter event loop — updates the displayed camera frame.
        if not self._alive:
            return
        frame = self._get_frame()
        if frame is not None:
            for (x, y, w, h) in self._boxes:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 220, 0), 2)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            img.thumbnail((PREVIEW_W, PREVIEW_H))
            photo = ImageTk.PhotoImage(img)
            self._canvas.create_image(0, 0, anchor="nw", image=photo)
            self._canvas._p = photo  # prevent GC
        self.after(33, self._tick)

    # ------------------------------------------------------------------
    # Test state helpers (all thread-safe via after())
    # ------------------------------------------------------------------

    def _ui(self, fn) -> None:
        """Schedule fn() on the tkinter thread."""
        self.after(0, fn)

    def _set_status(self, text: str, color: str = "#aaaaaa") -> None:
        self._ui(lambda: self._status_var.set(text) or
                 self._status_var._root.nametowidget(  # type: ignore
                     self._status_var._root.tk.call(
                         "info", "vars")).configure(fg=color)
                 if False else None)
        # Simpler: just set the var; color updated separately
        self._ui(lambda t=text: self._status_var.set(t))

    def _set_progress(self, pct: float) -> None:
        self._ui(lambda p=pct: self._prog_var.set(p))

    def _set_tc(self, tc_id: str, status: str, msg: str = "") -> None:
        row = self._rows.get(tc_id)
        if not row:
            return
        tc = row["tc"]
        tc.status = status
        tc.message = msg
        dot_c, lbl_c = _COLORS.get(status, _COLORS["pending"])
        label_text = f"{tc.tc_id}  {tc.name}"
        if msg:
            label_text += f"\n       {msg}"

        def _apply(d=row["dot"], l=row["label"], dc=dot_c, lc=lbl_c, lt=label_text):
            d.configure(fg=dc)
            l.configure(fg=lc, text=lt)
        self._ui(_apply)

    def _set_btns(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._ui(lambda: self._btn_unit.configure(state=state) or
                 self._btn_all.configure(state=state))

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    def _start(self, camera: bool) -> None:
        for tc in ALL_TESTS:
            self._set_tc(tc.tc_id, "pending")
        self._set_progress(0)
        self._ui(lambda: self._summary_var.set(""))
        self._set_btns(False)
        self._enrolled = None
        threading.Thread(target=self._run_all, args=(camera,), daemon=True).start()

    def _run_all(self, camera: bool) -> None:
        """Sequential test execution — runs in a daemon thread."""
        results: list[str] = []   # "pass" / "fail" / "skip"
        total = len(ALL_TESTS)
        done = 0

        def run(tc_id: str, fn) -> bool:
            self._set_tc(tc_id, "running")
            try:
                msg = fn() or ""
                self._set_tc(tc_id, "pass", msg)
                results.append("pass")
                return True
            except AssertionError as e:
                self._set_tc(tc_id, "fail", str(e))
                results.append("fail")
                return False
            except Exception as e:
                self._set_tc(tc_id, "fail", f"Error: {e}")
                results.append("fail")
                return False

        def skip(tc_id: str) -> None:
            self._set_tc(tc_id, "skip", "skipped")
            results.append("skip")

        def tick(label: str = "") -> None:
            nonlocal done
            done += 1
            self._set_progress(done / total * 100)
            if label:
                self._ui(lambda l=label: self._status_var.set(l))

        # -- TC4: unit test, no camera --
        self._ui(lambda: self._status_var.set("TC4: Testing impostor rejection…"))

        def _tc4():
            enrolled = np.random.rand(128).astype(np.float64)
            impostor = np.random.rand(128).astype(np.float64)
            while np.linalg.norm(enrolled - impostor) <= config.DEFAULT_TOLERANCE:
                impostor = np.random.rand(128).astype(np.float64)
            auth = Authenticator(enrolled)
            result = False
            for _ in range(config.CONSECUTIVE_FRAMES_REQUIRED * 2):
                result = auth.feed(impostor)
            assert not result, "Impostor was incorrectly granted access"
            return "Impostor correctly rejected"

        run("TC4", _tc4)
        tick()

        if not camera:
            for tc in ALL_TESTS:
                if tc.tc_id != "TC4":
                    skip(tc.tc_id)
            self._finish(results)
            return

        # -- TC5: detection --
        self._ui(lambda: self._status_var.set("TC5: Look at the camera…"))

        def _wait_for_face(timeout: float = 10.0) -> tuple:
            """Return (small_frame, boxes) when exactly one face is in frame."""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._get_frame()
                if frame is not None:
                    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                    boxes = self._detector.find_faces(small)
                    self._boxes = [(x*2, y*2, w*2, h*2) for x, y, w, h in boxes]
                    if len(boxes) >= 1:
                        return small, boxes
                time.sleep(0.05)
            raise AssertionError("No face detected after 10 s — check camera")

        def _tc5a():
            _, boxes = _wait_for_face()
            return f"{len(boxes)} face(s) detected"

        run("TC5a", _tc5a); tick()

        def _tc5b():
            _, boxes = _wait_for_face()
            for box in boxes:
                assert len(box) == 4, f"Box has {len(box)} elements, expected 4"
                assert all(isinstance(v, int) and v >= 0 for v in box)
            return "Shape (x, y, w, h) ✓"

        run("TC5b", _tc5b); tick()

        def _tc5c():
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                frame = self._get_frame()
                if frame is not None:
                    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                    if self._detector.has_exactly_one_face(small):
                        return "has_exactly_one_face → True ✓"
                time.sleep(0.05)
            raise AssertionError("has_exactly_one_face never returned True")

        run("TC5c", _tc5c); tick()

        # -- TC1: embedding --
        self._ui(lambda: self._status_var.set("TC1: Extracting embedding…"))
        live_emb: np.ndarray | None = None

        def _tc1():
            nonlocal live_emb
            small, boxes = _wait_for_face()
            if len(boxes) == 1:
                emb = extract_embedding(small, boxes[0])
                assert emb is not None, "extract_embedding returned None"
                assert emb.shape == (128,), f"Expected (128,), got {emb.shape}"
                live_emb = emb
                return "128-d ✓"
            raise AssertionError("Need exactly one face")

        run("TC1", _tc1); tick()

        # -- TC6: serialization (offline, uses live_emb) --
        self._ui(lambda: self._status_var.set("TC6: Serialization roundtrip…"))

        def _tc6():
            assert live_emb is not None, "TC1 did not produce an embedding"
            restored = bytes_to_embedding(embedding_to_bytes(live_emb))
            assert np.allclose(live_emb, restored), "Mismatch after roundtrip"
            return "bytes → embedding → bytes ✓"

        run("TC6", _tc6); tick()

        # -- TC8: same face, two captures --
        self._ui(lambda: self._status_var.set("TC8: Comparing two captures of the same face…"))

        def _tc8():
            embs = []
            for _ in range(2):
                small, boxes = _wait_for_face()
                if len(boxes) == 1:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        embs.append(emb)
                time.sleep(0.3)
            assert len(embs) == 2, "Could not capture two embeddings"
            dist = float(np.linalg.norm(embs[0] - embs[1]))
            assert compare_embedding(embs[0], embs[1], config.DEFAULT_TOLERANCE), \
                f"Distance {dist:.3f} > tolerance {config.DEFAULT_TOLERANCE}"
            return f"Distance {dist:.3f} ≤ {config.DEFAULT_TOLERANCE} ✓"

        run("TC8", _tc8); tick()

        # -- Enrollment phase --
        self._set_tc("Enrl", "running")
        self._ui(lambda: self._status_var.set(
            "Enrollment: hold still — capturing 30 frames…"))
        embeddings: list[np.ndarray] = []
        last_cap = 0.0
        enroll_deadline = time.monotonic() + 90

        while len(embeddings) < config.ENROLLMENT_FRAMES:
            if time.monotonic() > enroll_deadline:
                break
            frame = self._get_frame()
            if frame is None:
                time.sleep(0.05)
                continue
            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            boxes = self._detector.find_faces(small)
            self._boxes = [(x*2, y*2, w*2, h*2) for x, y, w, h in boxes]
            now = time.monotonic()
            if (len(boxes) == 1
                    and now - last_cap >= config.ENROLLMENT_CAPTURE_INTERVAL):
                emb = extract_embedding(small, boxes[0])
                if emb is not None:
                    embeddings.append(emb)
                    last_cap = now
                    pct = len(embeddings) / config.ENROLLMENT_FRAMES * 100
                    n = len(embeddings)
                    self._set_progress(pct)
                    self._ui(lambda n=n: self._status_var.set(
                        f"Enrollment: {n}/{config.ENROLLMENT_FRAMES} frames captured"))
            time.sleep(0.04)

        if len(embeddings) >= config.ENROLLMENT_FRAMES:
            self._enrolled = average_embeddings(embeddings)
            self._set_tc("Enrl", "pass", f"{len(embeddings)} frames averaged")
            results.append("pass")
        else:
            self._set_tc("Enrl", "fail",
                         f"Only {len(embeddings)}/{config.ENROLLMENT_FRAMES}")
            results.append("fail")
            for tc_id in ("TC2", "TC3", "TC7"):
                skip(tc_id)
            tick("Enrollment failed — auth tests skipped")
            self._finish(results)
            return

        tick()

        # -- TC2: auth consecutive --
        self._ui(lambda: self._status_var.set(
            "TC2: Look at the camera to authenticate…"))

        def _tc2():
            assert self._enrolled is not None
            auth = Authenticator(self._enrolled)
            deadline = time.monotonic() + config.AUTO_LOCK_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                frame = self._get_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes = self._detector.find_faces(small)
                self._boxes = [(x*2, y*2, w*2, h*2) for x, y, w, h in boxes]
                if len(boxes) == 1:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        dist = float(np.linalg.norm(self._enrolled - emb))
                        self._ui(lambda d=dist: self._status_var.set(
                            f"TC2: Authenticating… distance {d:.3f}"))
                        if auth.feed(emb):
                            return f"Authenticated — distance {dist:.3f}"
                else:
                    auth.reset()
                time.sleep(0.1)
            raise AssertionError(
                f"Auth timed out — face not recognized within "
                f"{config.AUTO_LOCK_TIMEOUT_SECONDS}s")

        run("TC2", _tc2); tick()

        # -- TC3: streak reset --
        self._ui(lambda: self._status_var.set("TC3: Testing streak reset…"))

        def _tc3():
            assert self._enrolled is not None
            auth = Authenticator(self._enrolled)
            # Build a partial streak
            attempts = 0
            while auth.streak < config.CONSECUTIVE_FRAMES_REQUIRED - 1 and attempts < 30:
                frame = self._get_frame()
                if frame is not None:
                    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                    boxes = self._detector.find_faces(small)
                    if len(boxes) == 1:
                        emb = extract_embedding(small, boxes[0])
                        if emb is not None:
                            auth.feed(emb)
                attempts += 1
                time.sleep(0.1)
            streak_before = auth.streak
            assert streak_before > 0, "Could not build partial streak — check face visibility"
            # Blank frame has no faces
            blank = np.zeros((240, 320, 3), dtype=np.uint8)
            assert self._detector.find_faces(blank) == []
            auth.reset()
            assert auth.streak == 0
            return f"Streak {streak_before} → 0 ✓"

        run("TC3", _tc3); tick()

        # -- TC7: auth after serialization --
        self._ui(lambda: self._status_var.set(
            "TC7: Auth with serialized embedding — look at camera…"))

        def _tc7():
            assert self._enrolled is not None
            restored = bytes_to_embedding(embedding_to_bytes(self._enrolled))
            auth = Authenticator(restored)
            deadline = time.monotonic() + config.AUTO_LOCK_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                frame = self._get_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes = self._detector.find_faces(small)
                self._boxes = [(x*2, y*2, w*2, h*2) for x, y, w, h in boxes]
                if len(boxes) == 1:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        if auth.feed(emb):
                            return "Serialized embedding authenticated ✓"
                else:
                    auth.reset()
                time.sleep(0.1)
            raise AssertionError("Auth failed after serialization roundtrip")

        run("TC7", _tc7); tick()

        self._finish(results)

    def _finish(self, results: list[str]) -> None:
        self._boxes = []
        passed  = results.count("pass")
        failed  = results.count("fail")
        skipped = results.count("skip")
        color = "#1a8f1a" if failed == 0 else "#cc0000"
        summary = f"{passed} passed  {failed} failed  {skipped} skipped"
        self._ui(lambda s=summary, c=color: (
            self._status_var.set("Done — " + s),
            self._summary_var.set(s),
        ))
        self._set_btns(True)

    def _on_close(self) -> None:
        self._alive = False
        self.destroy()


def run() -> None:
    app = TestRunner()
    app.mainloop()


if __name__ == "__main__":
    run()
