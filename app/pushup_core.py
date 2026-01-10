# backend/app/pushup_core.py
import math
from typing import Dict, List
import numpy as np
import mediapipe as mp

THRESH_UP = 150
THRESH_DOWN = 110
SMOOTH_FACTOR = 0.5
INCLINATION_THRESH = 45.0

mp_pose = mp.solutions.pose

class SmartRepCounter:
    def __init__(self, up_thresh=THRESH_UP, down_thresh=THRESH_DOWN, smooth_factor=SMOOTH_FACTOR):
        self.up_thresh = up_thresh
        self.down_thresh = down_thresh
        self.alpha = smooth_factor

        self.state = "UP"
        self.count = 0
        self.feedback = "Get Ready"
        self.feedback_color = (200, 200, 200)
        self.mode = "STANDING"

        self.avg_angle = 180.0
        self.in_rep_motion = False

    def update(self, left_elbow, right_elbow, body_inclination):
        # 1) orientation
        if body_inclination > INCLINATION_THRESH:
            self.mode = "STANDING"
            self.feedback = "PAUSED (Stand)"
            self.feedback_color = (200, 200, 200)
            return (
                self.state,
                self.count,
                self.avg_angle,
                self.feedback,
                self.feedback_color,
                self.mode,
            )
        else:
            self.mode = "PUSHUP"

        # 2) smoothing
        raw_avg = (left_elbow + right_elbow) / 2.0
        self.avg_angle = (self.alpha * raw_avg) + ((1 - self.alpha) * self.avg_angle)
        curr = self.avg_angle

        # 3) state machine
        if self.state == "UP":
            if curr < (self.up_thresh - 10):
                self.in_rep_motion = True
            if curr < self.down_thresh:
                self.state = "DOWN"
                self.feedback = "DEPTH HIT!"
                self.feedback_color = (0, 255, 0)
            elif self.in_rep_motion and curr > self.up_thresh:
                self.feedback = "GO DEEPER!"
                self.feedback_color = (0, 0, 255)
                self.in_rep_motion = False
        elif self.state == "DOWN":
            if curr > self.up_thresh:
                self.state = "UP"
                self.count += 1
                self.feedback = "GOOD REP!"
                self.feedback_color = (0, 255, 0)
                self.in_rep_motion = False
            elif curr > (self.down_thresh + 20) and curr < self.up_thresh:
                self.feedback = "LOCK ELBOWS!"
                self.feedback_color = (0, 255, 255)

        return (
            self.state,
            self.count,
            curr,
            self.feedback,
            self.feedback_color,
            self.mode,
        )


def _angle_3pts(a, b, c):
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)
    ab = a - b
    cb = c - b
    denom = np.linalg.norm(ab) * np.linalg.norm(cb)
    if denom < 1e-6:
        return None
    cos_val = float(np.clip(np.dot(ab, cb) / denom, -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_val)))


def _body_inclination(shoulder_xy, ankle_xy):
    dx = abs(shoulder_xy[0] - ankle_xy[0])
    dy = abs(shoulder_xy[1] - ankle_xy[1])
    if dx < 1e-6:
        return 90.0
    return float(math.degrees(math.atan(dy / dx)))


def extract_pushup_features_from_mp_landmarks(landmarks):
    """
    landmarks: list[NormalizedLandmark] from Mediapipe
    Returns: dict with left_elbow, right_elbow, inclination, etc.
    """
    PL = mp_pose.PoseLandmark

    def pt(idx_enum):
        lm = landmarks[idx_enum.value]
        return (lm.x, lm.y), lm.visibility

    (ls_xy, ls_vis) = pt(PL.LEFT_SHOULDER)
    (le_xy, le_vis) = pt(PL.LEFT_ELBOW)
    (lw_xy, lw_vis) = pt(PL.LEFT_WRIST)

    (rs_xy, rs_vis) = pt(PL.RIGHT_SHOULDER)
    (re_xy, re_vis) = pt(PL.RIGHT_ELBOW)
    (rw_xy, rw_vis) = pt(PL.RIGHT_WRIST)

    (la_xy, la_vis) = pt(PL.LEFT_ANKLE)
    (ra_xy, ra_vis) = pt(PL.RIGHT_ANKLE)

    left_elbow_angle = (
        _angle_3pts(ls_xy, le_xy, lw_xy)
        if ls_vis > 0.4 and le_vis > 0.4 and lw_vis > 0.4
        else None
    )
    right_elbow_angle = (
        _angle_3pts(rs_xy, re_xy, rw_xy)
        if rs_vis > 0.4 and re_vis > 0.4 and rw_vis > 0.4
        else None
    )

    if left_elbow_angle is None and right_elbow_angle is None:
        return None

    if left_elbow_angle is None:
        left_elbow_angle = right_elbow_angle
    if right_elbow_angle is None:
        right_elbow_angle = left_elbow_angle

    inc_l = _body_inclination(ls_xy, la_xy)
    inc_r = _body_inclination(rs_xy, ra_xy)
    avg_inclination = (inc_l + inc_r) / 2.0

    return {
        "left_elbow": float(left_elbow_angle),
        "right_elbow": float(right_elbow_angle),
        "inclination": float(avg_inclination),
    }
