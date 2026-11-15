"""按设备拆分的模型参数与训练。

两层结构：

1. 相机模型（每台摄像头一份 npz）：14 维画面特征 → 「参考亮度」，沿用
   `BrightnessNeuralNetwork`。所谓参考亮度，是相机视角下环境光的单调映射，
   即改造前那个全局模型直接输出的值。
2. 屏幕曲线（每块屏幕一份 json）：参考亮度 → 实际亮度，5 节点单调分段线性。
   默认是恒等映射，因此新屏幕的行为与改造前一致；训练后只调整这块屏的偏好。

这样拆分的好处：相机先验可以直接复用，屏幕曲线只承担「这块屏比参考值偏亮/偏暗」
的个人偏好，单摄像头单屏时也不会出现两层曲线互相抵消。
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

from liangdu import BrightnessNeuralNetwork, TARGET_VERSION, generate_synthetic_data

SCREEN_KNOTS = np.array([0.0, 25.0, 50.0, 75.0, 100.0])
DEFAULT_CURVE = np.array([0.0, 25.0, 50.0, 75.0, 100.0])
PROFILE_SCHEMA = 1


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# 屏幕曲线
# ---------------------------------------------------------------------------


def _interp_weights(values, knots):
    """线性插值的基函数权重矩阵 (N, K)。"""
    values = np.clip(np.asarray(values, dtype=np.float64).reshape(-1), knots[0], knots[-1])
    index = np.searchsorted(knots, values, side="right") - 1
    index = np.clip(index, 0, len(knots) - 2)
    left = knots[index]
    right = knots[index + 1]
    ratio = np.where(right > left, (values - left) / (right - left), 0.0)
    weights = np.zeros((values.shape[0], len(knots)), dtype=np.float64)
    rows = np.arange(values.shape[0])
    weights[rows, index] = 1.0 - ratio
    weights[rows, index + 1] = ratio
    return weights


class ScreenCurve:
    """参考亮度 → 实际亮度的单调曲线。"""

    def __init__(self, brightness=None, sample_count=0, updated_at=""):
        values = DEFAULT_CURVE if brightness is None else np.asarray(brightness, dtype=np.float64)
        if values.shape != SCREEN_KNOTS.shape:
            values = DEFAULT_CURVE.copy()
        self.brightness = _monotone(values)
        self.sample_count = int(sample_count)
        self.updated_at = updated_at or _now()

    def evaluate(self, reference):
        """参考亮度 → 实际亮度（支持标量/数组）。"""
        scalar = np.isscalar(reference)
        values = np.clip(np.asarray(reference, dtype=np.float64), 0.0, 100.0)
        result = np.interp(values, SCREEN_KNOTS, self.brightness)
        return float(result) if scalar else result

    def inverse(self, brightness):
        """实际亮度 → 参考亮度（曲线非降，用微小偏移保证可逆）。"""
        scalar = np.isscalar(brightness)
        values = np.clip(np.asarray(brightness, dtype=np.float64), 0.0, 100.0)
        xp = self.brightness + np.arange(len(self.brightness)) * 1e-6
        result = np.interp(values, xp, SCREEN_KNOTS)
        return float(result) if scalar else result

    def fit(self, reference, brightness, ridge=1.0, alpha=1.0, sample_count=None):
        """用样本拟合曲线：对当前曲线做仿射校正（缩放 + 偏移），再做单调约束。

        选仿射族而不是直接拟合 5 个节点，是因为单屏的样本通常只有几条：
        5 节点自由拟合会在样本点之间乱跳，而「这块屏整体偏亮/偏暗」用
        `新曲线 = a * 当前曲线 + b` 就能一次到位，且天然保持单调。

        Args:
            reference: 相机模型输出的参考亮度
            brightness: 用户为这块屏选择的亮度
            ridge: 向 (a=1, b=0) 收缩的强度，样本越少越保守
            alpha: 新曲线的权重（1.0 表示完全采用本次拟合）
            sample_count: 拟合后的样本总数（默认在原值上累加）
        """
        reference = np.asarray(reference, dtype=np.float64).reshape(-1)
        brightness = np.asarray(brightness, dtype=np.float64).reshape(-1)
        if reference.size == 0:
            return self

        base = self.evaluate(reference)
        design = np.column_stack([base, np.ones_like(base)])
        normal = design.T @ design + float(ridge) * np.eye(2)
        rhs = design.T @ brightness + float(ridge) * np.array([1.0, 0.0])
        try:
            gain, offset = np.linalg.solve(normal, rhs)
        except np.linalg.LinAlgError:
            gain, offset = 1.0, 0.0
        gain = float(np.clip(gain, 0.1, 3.0))
        offset = float(np.clip(offset, -100.0, 100.0))

        corrected = np.clip(gain * self.brightness + offset, 0.0, 100.0)
        if alpha < 1.0:
            corrected = (1.0 - alpha) * self.brightness + alpha * corrected
        self.brightness = _monotone(corrected)
        self.sample_count = int(sample_count) if sample_count is not None else self.sample_count + int(reference.size)
        self.updated_at = _now()
        return self

    def to_dict(self):
        return {
            "schema": PROFILE_SCHEMA,
            "knots": SCREEN_KNOTS.tolist(),
            "brightness": [round(float(v), 2) for v in self.brightness],
            "sample_count": int(self.sample_count),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return cls()
        values = data.get("brightness")
        if not values or len(values) != len(SCREEN_KNOTS):
            return cls()
        return cls(values, data.get("sample_count", 0), data.get("updated_at", ""))


def _monotone(values):
    values = np.clip(np.asarray(values, dtype=np.float64), 0.0, 100.0)
    return np.maximum.accumulate(values)


def load_screen_curve(path):
    if not os.path.exists(path):
        return ScreenCurve()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return ScreenCurve.from_dict(json.load(handle))
    except Exception:
        try:
            os.replace(path, path + ".bak")
        except Exception:
            pass
        return ScreenCurve()


def save_screen_curve(path, curve, device=None):
    """保存屏幕曲线；`device` 是这块屏的参数档案（EDID/分辨率/连接类型等）。"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    data = curve.to_dict()
    if device:
        data["device"] = device
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 相机模型
# ---------------------------------------------------------------------------


def camera_model_compatible(model):
    if model is None:
        return False
    return int(getattr(model, "input_size", 0)) == 14 and int(getattr(model, "target_version", 0)) >= TARGET_VERSION


def load_camera_model_from(paths):
    """按顺序尝试加载相机模型，返回 (model, 来源路径) 或 (None, None)。"""
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            model = BrightnessNeuralNetwork()
            model.load_model(path)
            if camera_model_compatible(model):
                return model, path
        except Exception:
            continue
    return None, None


def new_camera_model(num_samples=1000, seed=None):
    """创建一个经过合成先验训练的相机模型。"""
    model = BrightnessNeuralNetwork()
    features, targets = generate_synthetic_data(num_samples=num_samples, seed=seed)
    model.train(features, targets, learning_rate=0.001, epochs=1000, batch_size=64)
    return model


def save_camera_model(path, model):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    model.save_model(path)


def finetune_camera_model(model, reference_samples, prior_samples=400, epochs=300, learning_rate=0.0005, seed=None):
    """用真实样本微调相机模型，并与合成先验混合（先验至少一半）。

    Args:
        model: 相机模型
        reference_samples: [(features, reference_brightness), ...]，reference 由屏幕曲线反解得到
    """
    if not reference_samples:
        return model
    prior_features, prior_targets = generate_synthetic_data(num_samples=prior_samples, seed=seed)
    features = list(prior_features)
    targets = list(prior_targets)
    for item_features, item_target in reference_samples:
        features.append(list(item_features))
        targets.append(float(item_target))
    model.train(features, targets, learning_rate=learning_rate, epochs=epochs, batch_size=32)
    return model


# ---------------------------------------------------------------------------
# 样本文件
# ---------------------------------------------------------------------------


def sample_path(root, camera_id, screen_id):
    return os.path.join(root, "samples", f"{camera_id}__{screen_id}.npz")


def load_samples(path):
    if not os.path.exists(path):
        return [], []
    try:
        data = np.load(path)
        features = np.asarray(data["X"], dtype=np.float64)
        labels = np.asarray(data["y"], dtype=np.float64).reshape(-1)
        if features.ndim != 2 or features.shape[1] != 14 or features.shape[0] != labels.shape[0]:
            return [], []
        return features.tolist(), labels.tolist()
    except Exception:
        return [], []


def save_samples(path, features, labels):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp.npz"
    np.savez(tmp, X=np.asarray(features, dtype=np.float64), y=np.asarray(labels, dtype=np.float64))
    os.replace(tmp, path)


def append_samples(path, features, label, max_samples=500):
    """追加一条样本并落盘，返回追加后的 (X, y)。"""
    all_features, all_labels = load_samples(path)
    all_features.append([float(v) for v in features])
    all_labels.append(float(label))
    if len(all_features) > max_samples:
        all_features = all_features[-max_samples:]
        all_labels = all_labels[-max_samples:]
    save_samples(path, all_features, all_labels)
    return all_features, all_labels


def load_legacy_samples(path):
    """旧版 training_samples.npz：X 14 维 + y。"""
    return load_samples(path)


def legacy_weighted(legacy_X, legacy_y, weight=0.5):
    """按权重抽一半的旧样本（向下取整，至少保留 1 条）。"""
    if not legacy_X:
        return [], []
    count = max(1, int(round(len(legacy_X) * max(0.0, min(1.0, weight)))))
    step = len(legacy_X) / count
    picked = [min(len(legacy_X) - 1, int(i * step)) for i in range(count)]
    return [legacy_X[i] for i in picked], [legacy_y[i] for i in picked]
