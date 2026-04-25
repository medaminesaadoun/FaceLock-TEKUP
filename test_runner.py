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
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageTk

import config
from modules.face_detector import FaceDetector
from modules.face_encoder import (
    extract_embedding, average_embeddings,
    embedding_to_bytes, bytes_to_embedding, compare_embedding,
)
from modules.authenticator import Authenticator

WIN_W, WIN_H = 1280, 720
PREVIEW_W, PREVIEW_H = 640, 480

_COLORS = {
    "pending": ("#555566", "#888899"),
    "running": ("#e6a817", "white"),
    "pass":    ("#1a8f1a", "#88ff88"),
    "fail":    ("#cc0000", "#ff8888"),
    "skip":    ("#333344", "#666677"),
}

_AUTH_TESTS = {"TC2", "TC3", "TC7"}


@dataclass
class TC:
    tc_id: str
    name: str
    needs_camera: bool = True
    status: str = "pending"
    message: str = ""


ALL_TESTS: list[TC] = [
    TC("TC4",  "Wrong face rejects auth",         needs_camera=False),
    TC("TC5a", "Face detected in live frame"),
    TC("TC5b", "Bounding box valid shape"),
    TC("TC5c", "Exactly one face in frame"),
    TC("TC1",  "Embedding is 128-dimensional"),
    TC("TC6",  "Embedding serialization roundtrip"),
    TC("TC8",  "Same face matches within tolerance"),
    TC("Enrl", "Session enrollment  (30 frames)"),
    TC("TC2",  "Auth on consecutive matches"),
    TC("TC3",  "Streak resets on no face"),
    TC("TC7",  "Auth after serialization"),
]


class TestRunner(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FaceLock — Test Runner")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(bg="#111122")

        self._alive = True
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._boxes: list = []
        self._embedding: np.ndarray | None = None

        self._detector = FaceDetector(config.TFLITE_MODEL_PATH)
        self._enrolled: np.ndarray | None = None
        self._rows: dict[str, dict] = {}

        self._build()
        threading.Thread(target=self._camera_loop, daemon=True).start()
        self._tick()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        style = ttk.Style(self)
        # Make checkbutton background match our dark theme.
        style.configure("Dark.TCheckbutton",
                        background="#1e1e32",
                        foreground="#cccccc",
                        font=("Segoe UI", 10))
        style.map("Dark.TCheckbutton",
                  background=[("active", "#2a2a44")],
                  foreground=[("active", "white")])

        # ---- Accent bar ----
        tk.Frame(self, bg="#1a73e8", height=4).pack(fill="x")

        # ---- Header ----
        header = tk.Frame(self, bg="#111122", padx=20, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="FaceLock — Test Runner",
                 font=("Segoe UI", 15, "bold"),
                 bg="#111122", fg="white").pack(side="left")
        tk.Label(header,
                 text="Stop the core service before running  (camera must be free)",
                 font=("Segoe UI", 9), bg="#111122", fg="#555566").pack(
                     side="left", padx=(16, 0), anchor="s", pady=(0, 3))

        # ---- Body: two columns ----
        body = tk.Frame(self, bg="#111122")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # Left column — camera + status
        left = tk.Frame(body, bg="#111122")
        left.pack(side="left", fill="y", anchor="n", padx=(0, 20))

        self._canvas = tk.Canvas(left, width=PREVIEW_W, height=PREVIEW_H,
                                  bg="#000000", highlightthickness=1,
                                  highlightbackground="#2a2a44")
        self._canvas.pack()

        self._status_var = tk.StringVar(
            master=self, value="Ready — select tests and press a Run button")
        tk.Label(left, textvariable=self._status_var,
                 font=("Segoe UI", 10), bg="#111122", fg="#aaaaaa",
                 wraplength=PREVIEW_W, justify="center").pack(pady=(8, 4))

        self._prog_var = tk.DoubleVar(master=self, value=0)
        ttk.Progressbar(left, variable=self._prog_var, maximum=100,
                        length=PREVIEW_W, mode="determinate").pack()

        # Right column — test list + buttons
        right = tk.Frame(body, bg="#111122")
        right.pack(side="left", fill="both", expand=True, anchor="n")

        # ---- Buttons (top of right panel) ----
        btn_row = tk.Frame(right, bg="#111122")
        btn_row.pack(fill="x", pady=(0, 12))

        self._btn_selected = tk.Button(
            btn_row, text="▶  Run Selected",
            font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2",
            bg="#1a73e8", fg="white", padx=14, pady=8,
            command=lambda: self._start("selected"))
        self._btn_selected.pack(side="left", padx=(0, 8))

        self._btn_unit = tk.Button(
            btn_row, text="▶  Run Unit Tests",
            font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2",
            bg="#555577", fg="white", padx=14, pady=8,
            command=lambda: self._start("unit"))
        self._btn_unit.pack(side="left", padx=(0, 8))

        self._btn_all = tk.Button(
            btn_row, text="▶  Run All  (camera required)",
            font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2",
            bg="#1a8f1a", fg="white", padx=14, pady=8,
            command=lambda: self._start("all"))
        self._btn_all.pack(side="left", padx=(0, 8))

        sel_frame = tk.Frame(btn_row, bg="#111122")
        sel_frame.pack(side="right")
        tk.Button(sel_frame, text="Select all",
                  font=("Segoe UI", 9), bg="#222233", fg="#aaaaaa",
                  relief="flat", cursor="hand2",
                  command=self._select_all).pack(side="left", padx=(0, 4))
        tk.Button(sel_frame, text="Deselect all",
                  font=("Segoe UI", 9), bg="#222233", fg="#aaaaaa",
                  relief="flat", cursor="hand2",
                  command=self._deselect_all).pack(side="left")

        # ---- Test rows ----
        # Header row
        hdr = tk.Frame(right, bg="#0d0d1e")
        hdr.pack(fill="x", pady=(0, 2))
        tk.Label(hdr, text="  ", width=3,
                 bg="#0d0d1e", fg="#555566").pack(side="left")
        tk.Label(hdr, text="ID", width=6, anchor="w",
                 font=("Segoe UI", 9, "bold"), bg="#0d0d1e",
                 fg="#555566").pack(side="left")
        tk.Label(hdr, text="Test name", anchor="w",
                 font=("Segoe UI", 9, "bold"), bg="#0d0d1e",
                 fg="#555566").pack(side="left", padx=(4, 0))
        tk.Label(hdr, text="Result", width=32, anchor="w",
                 font=("Segoe UI", 9, "bold"), bg="#0d0d1e",
                 fg="#555566").pack(side="right", padx=(0, 8))

        for tc in ALL_TESTS:
            row = tk.Frame(right, bg="#1a1a2e", pady=0)
            row.pack(fill="x", pady=(0, 2))

            # Checkbox — ttk handles its own click events correctly.
            var = tk.BooleanVar(master=self, value=True)
            cb = ttk.Checkbutton(row, variable=var, style="Dark.TCheckbutton",
                                  cursor="hand2")
            cb.pack(side="left", padx=(8, 0))

            # Status dot.
            dot = tk.Label(row, text="●", font=("Segoe UI", 11),
                           bg="#1a1a2e", fg="#555566")
            dot.pack(side="left", padx=(6, 0))

            # TC id badge.
            badge_text = tc.tc_id + (" 📷" if tc.needs_camera else "   ")
            tk.Label(row, text=badge_text, width=8, anchor="w",
                     font=("Consolas", 9), bg="#1a1a2e",
                     fg="#6688aa").pack(side="left", padx=(6, 0))

            # Test name.
            name_lbl = tk.Label(row, text=tc.name, anchor="w",
                                font=("Segoe UI", 10), bg="#1a1a2e",
                                fg="#888899")
            name_lbl.pack(side="left", padx=(4, 0), fill="x", expand=True)

            # Result label (right-aligned).
            result_lbl = tk.Label(row, text="—", anchor="e", width=32,
                                  font=("Segoe UI", 9), bg="#1a1a2e",
                                  fg="#555566")
            result_lbl.pack(side="right", padx=(0, 10))

            self._rows[tc.tc_id] = {
                "dot": dot, "name": name_lbl,
                "result": result_lbl, "tc": tc, "var": var,
            }

        # ---- Summary bar ----
        self._summary_var = tk.StringVar(master=self, value="")
        tk.Label(right, textvariable=self._summary_var,
                 font=("Segoe UI", 10), bg="#111122",
                 fg="#aaaaaa").pack(anchor="w", pady=(10, 0))

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------

    def _camera_loop(self) -> None:
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

    def _draw_embedding_overlay(self, img: Image.Image,
                                 emb: np.ndarray) -> Image.Image:
        """128-bar spectrum at the bottom of the frame.
        Blue = positive dimensions, red = negative."""
        draw = ImageDraw.Draw(img)
        w, h = img.size
        strip_h = 56
        n = len(emb)
        bar_w = max(1, (w - 16) // n)
        x0 = (w - n * bar_w) // 2
        cy = h - strip_h // 2

        draw.rectangle([0, h - strip_h, w, h], fill=(0, 0, 15))
        draw.line([x0, cy, x0 + n * bar_w, cy], fill=(60, 60, 100), width=1)

        mx = max(float(np.abs(emb).max()), 0.01)
        max_bar = strip_h // 2 - 4

        for i, val in enumerate(emb):
            x = x0 + i * bar_w
            length = int(float(val) / mx * max_bar)
            color = (70, 130, 255) if val >= 0 else (255, 70, 70)
            if length > 0:
                draw.rectangle([x, cy - length, x + max(bar_w - 1, 1), cy],
                               fill=color)
            elif length < 0:
                draw.rectangle([x, cy, x + max(bar_w - 1, 1), cy - length],
                               fill=color)

        draw.text((4, h - strip_h + 3), "128-d embedding vector",
                  fill=(80, 80, 120))
        return img

    def _tick(self) -> None:
        if not self._alive:
            return
        frame = self._get_frame()
        if frame is not None:
            for (x, y, w, h) in self._boxes:
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 220, 0), 2)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            img = img.resize((PREVIEW_W, PREVIEW_H), Image.BILINEAR)
            emb = self._embedding
            if emb is not None:
                img = self._draw_embedding_overlay(img, emb)
            photo = ImageTk.PhotoImage(img)
            self._canvas.create_image(0, 0, anchor="nw", image=photo)
            self._canvas._p = photo
        self.after(33, self._tick)

    # ------------------------------------------------------------------
    # Checkbox helpers
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        for row in self._rows.values():
            row["var"].set(True)

    def _deselect_all(self) -> None:
        for row in self._rows.values():
            row["var"].set(False)

    # ------------------------------------------------------------------
    # Test state helpers
    # ------------------------------------------------------------------

    def _ui(self, fn) -> None:
        self.after(0, fn)

    def _set_status(self, text: str) -> None:
        self._ui(lambda t=text: self._status_var.set(t))

    def _set_progress(self, pct: float) -> None:
        self._ui(lambda p=pct: self._prog_var.set(p))

    def _set_tc(self, tc_id: str, status: str, msg: str = "") -> None:
        row = self._rows.get(tc_id)
        if not row:
            return
        row["tc"].status = status
        dot_c, _ = _COLORS.get(status, _COLORS["pending"])
        symbols = {"pending": "●", "running": "▶", "pass": "✓",
                   "fail": "✗", "skip": "—"}
        symbol = symbols.get(status, "●")
        name_c = {
            "pending": "#888899", "running": "white",
            "pass": "#88ff88", "fail": "#ff8888", "skip": "#555566",
        }.get(status, "#888899")

        def _apply(r=row, dc=dot_c, nc=name_c, sym=symbol, m=msg):
            r["dot"].configure(text=sym, fg=dc)
            r["name"].configure(fg=nc)
            r["result"].configure(text=m if m else "—",
                                  fg=dc if m else "#555566")
        self._ui(_apply)

    def _set_btns(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._ui(lambda s=state: [
            b.configure(state=s)
            for b in (self._btn_selected, self._btn_unit, self._btn_all)
        ])

    # ------------------------------------------------------------------
    # Runner
    # ------------------------------------------------------------------

    def _start(self, mode: str) -> None:
        for tc in ALL_TESTS:
            self._set_tc(tc.tc_id, "pending")
        self._set_progress(0)
        self._ui(lambda: self._summary_var.set(""))
        self._set_btns(False)
        self._enrolled = None
        self._embedding = None

        if mode == "unit":
            wanted = {tc.tc_id for tc in ALL_TESTS if not tc.needs_camera}
        elif mode == "all":
            wanted = {tc.tc_id for tc in ALL_TESTS}
        else:
            wanted = {tc_id for tc_id, r in self._rows.items()
                      if r["var"].get()}

        # Auto-include dependencies.
        if wanted & _AUTH_TESTS:
            wanted.add("Enrl")
        if "TC6" in wanted:
            wanted.add("TC1")

        threading.Thread(target=self._run_all, args=(wanted,),
                         daemon=True).start()

    def _run_all(self, wanted: set[str]) -> None:
        results: list[str] = []
        total = len(ALL_TESTS)
        done = 0

        def run(tc_id: str, fn) -> bool:
            if tc_id not in wanted:
                _skip(tc_id, "not selected")
                return False
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

        def _skip(tc_id: str, reason: str = "dependency failed") -> None:
            self._set_tc(tc_id, "skip", reason)
            results.append("skip")

        def tick(label: str = "") -> None:
            nonlocal done
            done += 1
            self._set_progress(done / total * 100)
            if label:
                self._set_status(label)

        # ---- TC4 ----
        self._set_status("TC4: Testing impostor rejection…")

        def _tc4():
            e = np.random.rand(128).astype(np.float64)
            imp = np.random.rand(128).astype(np.float64)
            while np.linalg.norm(e - imp) <= config.DEFAULT_TOLERANCE:
                imp = np.random.rand(128).astype(np.float64)
            auth = Authenticator(e)
            result = False
            for _ in range(config.CONSECUTIVE_FRAMES_REQUIRED * 2):
                result = auth.feed(imp)
            assert not result, "Impostor was incorrectly granted access"
            return "Impostor correctly rejected"
        run("TC4", _tc4); tick()

        # Helper: wait for at least one face.
        def _wait_face(timeout: float = 10.0):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                frame = self._get_frame()
                if frame is not None:
                    small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                    boxes = self._detector.find_faces(small)
                    self._boxes = [(x*2,y*2,w*2,h*2) for x,y,w,h in boxes]
                    if boxes:
                        return small, boxes
                time.sleep(0.05)
            raise AssertionError("No face detected after 10 s — check camera")

        # ---- TC5 ----
        self._set_status("TC5: Look at the camera…")

        def _tc5a():
            _, b = _wait_face()
            return f"{len(b)} face(s) detected"
        run("TC5a", _tc5a); tick()

        def _tc5b():
            _, b = _wait_face()
            for box in b:
                assert len(box) == 4
                assert all(isinstance(v, int) and v >= 0 for v in box)
            return "Shape (x, y, w, h) valid"
        run("TC5b", _tc5b); tick()

        def _tc5c():
            dl = time.monotonic() + 10
            while time.monotonic() < dl:
                f = self._get_frame()
                if f is not None:
                    s = cv2.resize(f, (0, 0), fx=0.5, fy=0.5)
                    if self._detector.has_exactly_one_face(s):
                        return "has_exactly_one_face → True"
                time.sleep(0.05)
            raise AssertionError("has_exactly_one_face never returned True")
        run("TC5c", _tc5c); tick()

        # ---- TC1 ----
        self._set_status("TC1: Extracting face embedding…")
        live_emb: np.ndarray | None = None

        def _tc1():
            nonlocal live_emb
            small, boxes = _wait_face()
            if len(boxes) == 1:
                emb = extract_embedding(small, boxes[0])
                assert emb is not None, "extract_embedding returned None"
                assert emb.shape == (128,), f"Expected (128,), got {emb.shape}"
                live_emb = emb
                self._embedding = emb
                return "128-d embedding extracted"
            raise AssertionError("Need exactly one face in frame")
        run("TC1", _tc1); tick()

        # ---- TC6 ----
        self._set_status("TC6: Serialization roundtrip…")

        def _tc6():
            assert live_emb is not None, "TC1 did not produce an embedding"
            restored = bytes_to_embedding(embedding_to_bytes(live_emb))
            assert np.allclose(live_emb, restored), "Mismatch after roundtrip"
            self._embedding = restored
            return "bytes ↔ embedding roundtrip OK"
        run("TC6", _tc6); tick()

        # ---- TC8 ----
        self._set_status("TC8: Comparing two captures…")

        def _tc8():
            embs = []
            for _ in range(2):
                small, boxes = _wait_face()
                if len(boxes) == 1:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        embs.append(emb)
                        self._embedding = emb
                time.sleep(0.3)
            assert len(embs) == 2, "Could not capture two embeddings"
            dist = float(np.linalg.norm(embs[0] - embs[1]))
            assert compare_embedding(embs[0], embs[1], config.DEFAULT_TOLERANCE), \
                f"Distance {dist:.3f} > tolerance {config.DEFAULT_TOLERANCE}"
            return f"Distance {dist:.3f} ≤ {config.DEFAULT_TOLERANCE}"
        run("TC8", _tc8); tick()

        # ---- Enrollment ----
        if "Enrl" not in wanted:
            _skip("Enrl", "not selected"); tick()
        else:
            self._set_tc("Enrl", "running")
            self._set_status("Enrollment: hold still — capturing 30 frames…")
            embeddings: list[np.ndarray] = []
            last_cap = 0.0
            dl = time.monotonic() + 90

            while len(embeddings) < config.ENROLLMENT_FRAMES:
                if time.monotonic() > dl:
                    break
                frame = self._get_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes = self._detector.find_faces(small)
                self._boxes = [(x*2,y*2,w*2,h*2) for x,y,w,h in boxes]
                now = time.monotonic()
                if len(boxes) == 1 and now - last_cap >= config.ENROLLMENT_CAPTURE_INTERVAL:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        embeddings.append(emb)
                        self._embedding = emb
                        last_cap = now
                        n = len(embeddings)
                        self._set_progress(n / config.ENROLLMENT_FRAMES * 100)
                        self._set_status(
                            f"Enrollment: {n}/{config.ENROLLMENT_FRAMES} frames")
                time.sleep(0.04)

            if len(embeddings) >= config.ENROLLMENT_FRAMES:
                self._enrolled = average_embeddings(embeddings)
                self._embedding = self._enrolled
                self._set_tc("Enrl", "pass",
                             f"{len(embeddings)} frames averaged")
                results.append("pass")
            else:
                self._set_tc("Enrl", "fail",
                             f"Only {len(embeddings)}/{config.ENROLLMENT_FRAMES}")
                results.append("fail")
                for tid in ("TC2", "TC3", "TC7"):
                    _skip(tid, "enrollment failed")
                self._finish(results)
                return
            tick()

        # ---- TC2 ----
        self._set_status("TC2: Look at the camera to authenticate…")

        def _tc2():
            assert self._enrolled is not None, "No enrolled embedding"
            auth = Authenticator(self._enrolled)
            dl2 = time.monotonic() + config.AUTO_LOCK_TIMEOUT_SECONDS
            while time.monotonic() < dl2:
                frame = self._get_frame()
                if frame is None:
                    time.sleep(0.1); continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes = self._detector.find_faces(small)
                self._boxes = [(x*2,y*2,w*2,h*2) for x,y,w,h in boxes]
                if len(boxes) == 1:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        self._embedding = emb
                        dist = float(np.linalg.norm(self._enrolled - emb))
                        self._set_status(f"TC2: distance {dist:.3f}")
                        if auth.feed(emb):
                            return f"Authenticated — distance {dist:.3f}"
                else:
                    auth.reset()
                time.sleep(0.1)
            raise AssertionError(f"Timed out after {config.AUTO_LOCK_TIMEOUT_SECONDS}s")
        run("TC2", _tc2); tick()

        # ---- TC3 ----
        self._set_status("TC3: Testing streak reset…")

        def _tc3():
            assert self._enrolled is not None, "No enrolled embedding"
            auth = Authenticator(self._enrolled)
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
            sb = auth.streak
            assert sb > 0, "Could not build partial streak"
            blank = np.zeros((240, 320, 3), dtype=np.uint8)
            assert self._detector.find_faces(blank) == []
            auth.reset()
            assert auth.streak == 0
            return f"Streak {sb} → 0"
        run("TC3", _tc3); tick()

        # ---- TC7 ----
        self._set_status("TC7: Auth with serialized embedding…")

        def _tc7():
            assert self._enrolled is not None, "No enrolled embedding"
            restored = bytes_to_embedding(embedding_to_bytes(self._enrolled))
            auth = Authenticator(restored)
            dl3 = time.monotonic() + config.AUTO_LOCK_TIMEOUT_SECONDS
            while time.monotonic() < dl3:
                frame = self._get_frame()
                if frame is None:
                    time.sleep(0.1); continue
                small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                boxes = self._detector.find_faces(small)
                self._boxes = [(x*2,y*2,w*2,h*2) for x,y,w,h in boxes]
                if len(boxes) == 1:
                    emb = extract_embedding(small, boxes[0])
                    if emb is not None:
                        self._embedding = emb
                        if auth.feed(emb):
                            return "Serialized embedding authenticated"
                else:
                    auth.reset()
                time.sleep(0.1)
            raise AssertionError("Auth failed after serialization")
        run("TC7", _tc7); tick()

        self._finish(results)

    def _finish(self, results: list[str]) -> None:
        self._boxes = []
        self._embedding = None
        passed  = results.count("pass")
        failed  = results.count("fail")
        skipped = results.count("skip")
        summary = f"{passed} passed  {failed} failed  {skipped} skipped"
        self._set_status(("Done" if failed == 0 else "Finished with failures")
                         + f" — {summary}")
        self._ui(lambda s=summary: self._summary_var.set(s))
        self._set_btns(True)

    def _on_close(self) -> None:
        self._alive = False
        self.destroy()


def run() -> None:
    app = TestRunner()
    app.mainloop()


if __name__ == "__main__":
    run()
