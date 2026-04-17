# modules/authenticator.py
import numpy as np

import config
from modules.face_encoder import compare_embedding


class Authenticator:
    """Requires CONSECUTIVE_FRAMES_REQUIRED successive matches to grant access."""

    def __init__(
        self,
        stored_embedding: np.ndarray,
        tolerance: float = config.DEFAULT_TOLERANCE,
        required_frames: int = config.CONSECUTIVE_FRAMES_REQUIRED,
    ) -> None:
        self._stored = stored_embedding
        self._tolerance = tolerance
        self._required = required_frames
        self._streak = 0

    def feed(self, live_embedding: np.ndarray) -> bool:
        """Feed one frame's embedding. Returns True when auth threshold is reached."""
        if compare_embedding(self._stored, live_embedding, self._tolerance):
            self._streak += 1
        else:
            self._streak = 0
        return self._streak >= self._required

    def reset(self) -> None:
        self._streak = 0

    @property
    def streak(self) -> int:
        return self._streak
