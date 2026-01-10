"""
train_pushup_quality_model.py

Offline script to:
1) Extract pose landmarks from push-up videos using MediaPipe.
2) Load manual labels for frames.
3) Build features from landmarks.
4) Train a small classifier (RandomForest).
5) Save the trained model to disk.

You will:
- Put your push-up videos in some folder.
- Run `extract_landmarks_from_video(...)` for each video (or use the
  helper `bulk_extract_landmarks`).
- Create a labels JSON file mapping frame indices to labels (e.g. "good_rep", "bad_rep").
- Run this script to train and save the model.
"""

import json
import math
import os
from typing import Dict, List, Tuple

import cv2
import joblib
import mediapipe as mp
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder

# ---------------------------------------------------------------------
# 1. MediaPipe Pose setup
# ---------------------------------------------------------------------

mp_pose = mp.solutions.pose


def extract_landmarks_from_video(
    video_path: str,
    out_json_path: str,
    sample_every_n: int = 2,
) -> None:
    """
    Run MediaPipe Pose on a video and save landmarks for sampled frames.

    Output JSON structure:
    [
      {
        "frame": 0,
        "landmarks": [
          {"x": ..., "y": ..., "z": ..., "vis": ...},
          ...
        ]
      },
      ...
    ]
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    pose = mp_pose.Pose(
        static_image_mode=False,
        model_complexity=1,
        enable_segmentation=False,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    data = []
    frame_idx = 0
    saved_count = 0

    print(f"[INFO] Processing video: {video_path}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % sample_every_n != 0:
            frame_idx += 1
            continue

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = pose.process(rgb)

        if not results.pose_landmarks:
            frame_idx += 1
            continue

        lms = results.pose_landmarks.landmark
        landmarks = [
            {
                "x": float(lm.x),
                "y": float(lm.y),
                "z": float(lm.z),
                "vis": float(lm.visibility),
            }
            for lm in lms
        ]
        data.append({"frame": frame_idx, "landmarks": landmarks})
        saved_count += 1

        frame_idx += 1

    cap.release()

    os.makedirs(os.path.dirname(out_json_path) or ".", exist_ok=True)
    with open(out_json_path, "w") as f:
        json.dump(data, f)

    print(
        f"[INFO] Saved {saved_count} landmark frames "
        f"to {out_json_path}"
    )


def bulk_extract_landmarks(
    video_paths: List[str],
    output_dir: str,
    sample_every_n: int = 2,
) -> None:
    """
    Convenience helper to extract landmarks from multiple videos.
    Each video gets its own JSON in output_dir with the same base name.
    """
    os.makedirs(output_dir, exist_ok=True)
    for video_path in video_paths:
        base = os.path.splitext(os.path.basename(video_path))[0]
        out_json = os.path.join(output_dir, f"{base}_landmarks.json")
        extract_landmarks_from_video(
            video_path, out_json, sample_every_n=sample_every_n
        )


# ---------------------------------------------------------------------
# 2. Feature engineering
# ---------------------------------------------------------------------

def angle_3pts(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    """
    Compute angle ABC (in degrees) with points a, b, c as (x, y).
    a, b, c are 2D tuples in normalized coordinates.
    """
    a = np.array(a)
    b = np.array(b)
    c = np.array(c)
    ab = a - b
    cb = c - b
    denom = np.linalg.norm(ab) * np.linalg.norm(cb)
    if denom < 1e-6:
        return 0.0
    cos_val = np.clip(np.dot(ab, cb) / denom, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos_val)))


def features_from_landmarks(landmarks: List[Dict]) -> List[float]:
    """
    Convert 33 pose landmarks (MediaPipe) into a small feature vector
    suitable for classifier training.

    landmarks: list of dicts with keys "x", "y", "z", "vis".

    Returns:
      List[float] of features (angles + simple geometry).
    """
    # MediaPipe Pose indices
    C = {
        "NOSE": 0,
        "L_SHOULDER": 11,
        "R_SHOULDER": 12,
        "L_ELBOW": 13,
        "R_ELBOW": 14,
        "L_WRIST": 15,
        "R_WRIST": 16,
        "L_HIP": 23,
        "R_HIP": 24,
        "L_ANKLE": 27,
        "R_ANKLE": 28,
    }

    def p(idx):
        lm = landmarks[idx]
        return (lm["x"], lm["y"])

    # angles
    left_elbow = angle_3pts(p(C["L_SHOULDER"]), p(C["L_ELBOW"]), p(C["L_WRIST"]))
    right_elbow = angle_3pts(p(C["R_SHOULDER"]), p(C["R_ELBOW"]), p(C["R_WRIST"]))
    left_hip = angle_3pts(p(C["L_SHOULDER"]), p(C["L_HIP"]), p(C["L_ANKLE"]))
    right_hip = angle_3pts(p(C["R_SHOULDER"]), p(C["R_HIP"]), p(C["R_ANKLE"]))

    # simple body tilt / alignment features
    l_shoulder = p(C["L_SHOULDER"])
    r_shoulder = p(C["R_SHOULDER"])
    l_hip = p(C["L_HIP"])
    r_hip = p(C["R_HIP"])

    # average shoulder & hip position
    sh_center = ((l_shoulder[0] + r_shoulder[0]) / 2.0, (l_shoulder[1] + r_shoulder[1]) / 2.0)
    hip_center = ((l_hip[0] + r_hip[0]) / 2.0, (l_hip[1] + r_hip[1]) / 2.0)

    # vertical body alignment (approx: how much hip is below shoulder)
    body_vertical_diff = hip_center[1] - sh_center[1]

    # rough "plank straightness": distance shoulder->hip vs shoulder->ankle
    l_ankle = p(C["L_ANKLE"])
    r_ankle = p(C["R_ANKLE"])
    ankle_center = ((l_ankle[0] + r_ankle[0]) / 2.0, (l_ankle[1] + r_ankle[1]) / 2.0)

    def dist(a, b):
        return math.dist(a, b)

    shoulder_hip_dist = dist(sh_center, hip_center)
    shoulder_ankle_dist = dist(sh_center, ankle_center)
    dist_ratio = shoulder_hip_dist / (shoulder_ankle_dist + 1e-6)

    return [
        left_elbow,
        right_elbow,
        left_hip,
        right_hip,
        body_vertical_diff,
        shoulder_hip_dist,
        shoulder_ankle_dist,
        dist_ratio,
    ]


# ---------------------------------------------------------------------
# 3. Dataset loading (landmarks + labels)
# ---------------------------------------------------------------------

def load_landmark_json(landmarks_json_path: str) -> List[Dict]:
    """
    Load landmarks JSON we created in extract_landmarks_from_video.
    """
    with open(landmarks_json_path, "r") as f:
        return json.load(f)


def load_labels(labels_json_path: str) -> Dict[int, str]:
    """
    Load labels JSON, mapping frame indices to labels.

    Expected format example (per video):

    [
      { "start": 50, "end": 120, "label": "good_rep" },
      { "start": 130, "end": 190, "label": "bad_rep" }
    ]

    OR

    [
      { "frame": 60, "label": "good_rep" },
      { "frame": 61, "label": "good_rep" }
    ]

    Returns:
      dict: { frame_index: "good_rep" | "bad_rep" | ... }
    """
    with open(labels_json_path, "r") as f:
        items = json.load(f)

    frame_to_label: Dict[int, str] = {}
    for item in items:
        if "start" in item:
            start = int(item["start"])
            end = int(item["end"])
            label = item["label"]
            for frame_idx in range(start, end + 1):
                frame_to_label[frame_idx] = label
        else:
            frame_idx = int(item["frame"])
            label = item["label"]
            frame_to_label[frame_idx] = label

    return frame_to_label


def build_dataset_for_one_video(
    landmarks_json_path: str,
    labels_json_path: str,
) -> Tuple[List[List[float]], List[str]]:
    """
    For a single video, match landmark frames with labels and produce X, y.
    """
    frames_data = load_landmark_json(landmarks_json_path)
    frame_to_label = load_labels(labels_json_path)

    X: List[List[float]] = []
    y: List[str] = []

    for row in frames_data:
        frame_idx = int(row["frame"])
        label = frame_to_label.get(frame_idx)
        if label is None:
            continue  # no label for this frame

        feats = features_from_landmarks(row["landmarks"])
        X.append(feats)
        y.append(label)

    print(
        f"[INFO] Built dataset from {landmarks_json_path}, "
        f"samples: {len(X)}"
    )
    return X, y


def build_dataset_multi_video(
    video_specs: List[Dict[str, str]],
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build dataset from multiple videos.

    video_specs: list of dict entries:
      {
        "landmarks": "path/to/video1_landmarks.json",
        "labels": "path/to/video1_labels.json"
      }
    """
    all_X: List[List[float]] = []
    all_y: List[str] = []

    for spec in video_specs:
        X, y = build_dataset_for_one_video(
            spec["landmarks"],
            spec["labels"],
        )
        all_X.extend(X)
        all_y.extend(y)

    X_arr = np.array(all_X, dtype=np.float32)
    y_arr = np.array(all_y, dtype=object)
    print(f"[INFO] Combined dataset: {X_arr.shape}, labels: {len(y_arr)}")
    return X_arr, y_arr


# ---------------------------------------------------------------------
# 4. Training
# ---------------------------------------------------------------------

def train_pushup_model(
    X: np.ndarray,
    y: np.ndarray,
    model_out_path: str,
    label_encoder_out_path: str,
) -> None:
    """
    Train a RandomForest push-up quality classifier and save:
      - the model (joblib)
      - the label encoder (joblib)
    """
    le = LabelEncoder()
    y_encoded = le.fit_transform(y)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y_encoded,
        test_size=0.2,
        random_state=42,
        stratify=y_encoded,
    )

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        random_state=42,
        n_jobs=-1,
    )

    print("[INFO] Training model...")
    clf.fit(X_train, y_train)

    print("[INFO] Evaluating...")
    y_pred = clf.predict(X_test)
    print(classification_report(y_test, y_pred, target_names=le.classes_.tolist()))
    print("Confusion matrix:")
    print(confusion_matrix(y_test, y_pred))

    # Save model + label encoder
    os.makedirs(os.path.dirname(model_out_path) or ".", exist_ok=True)
    joblib.dump(clf, model_out_path)
    joblib.dump(le, label_encoder_out_path)

    print(f"[INFO] Saved model to {model_out_path}")
    print(f"[INFO] Saved label encoder to {label_encoder_out_path}")


# ---------------------------------------------------------------------
# 5. MAIN (edit paths here)
# ---------------------------------------------------------------------

if __name__ == "__main__":
    # === STEP A: (Optional) Extract landmarks from one or more videos ===
    #
    # Uncomment this block the first time you run it to generate landmarks
    # JSON files from your push-up videos. After that, you can comment it
    # out and just use the JSONs for training.

    # videos = [
    #     "data/raw/pushup_good_1.mp4",
    #     "data/raw/pushup_good_2.mp4",
    #     "data/raw/pushup_bad_1.mp4",
    # ]
    # bulk_extract_landmarks(
    #     video_paths=videos,
    #     output_dir="data/landmarks",
    #     sample_every_n=2,
    # )
    # exit(0)  # run once, then comment out or remove exit

    # === STEP B: Build dataset from (landmarks.json, labels.json) pairs ===
    #
    # You must create labels JSONs matching the expected format.
    # Example:
    #   data/landmarks/pushup_good_1_landmarks.json
    #   data/labels/pushup_good_1_labels.json
    #
    # In the labels file, you define which frame indices are "good_rep", "bad_rep", etc.

    video_specs = [
        {
            "landmarks": "data/landmarks/pushup_good_1_landmarks.json",
            "labels": "data/labels/pushup_good_1_labels.json",
        },
        {
            "landmarks": "data/landmarks/pushup_bad_1_landmarks.json",
            "labels": "data/labels/pushup_bad_1_labels.json",
        },
        # add more videos here...
    ]

    X, y = build_dataset_multi_video(video_specs)

    # === STEP C: Train and save model ===
    model_out = "models/pushup_quality_model.joblib"
    le_out = "models/pushup_quality_labels.joblib"

    train_pushup_model(X, y, model_out, le_out)

    print("[DONE] Training complete.")
