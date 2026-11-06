from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
import json
import math
from typing import Any


class Status(StrEnum):
    PASS = "Pass"
    FAIL = "Fail"
    REVIEW = "Manual Review"
    DISABLED = "Disabled"


class PhotoContext(StrEnum):
    UNCERTAIN = "uncertain"
    ISOLATED_PRODUCT = "isolated_product"
    MODEL_WORN = "model_worn"

    @classmethod
    def parse(cls, value) -> "PhotoContext":
        try:
            return cls(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Photo context must be isolated_product, model_worn, or uncertain.") from exc


PHOTO_CONTEXT_LABELS = {
    PhotoContext.UNCERTAIN: "Not sure / cannot confirm product-only",
    PhotoContext.ISOLATED_PRODUCT: "Product only (no person or model)",
    PhotoContext.MODEL_WORN: "Product worn or held by a person",
}


QUALITY = ("blur", "low_contrast", "noise", "underexposure", "overexposure", "low_resolution")
COMPLIANCE = ("background_color", "background_uniformity", "frame_coverage", "placement", "margins", "possible_cropping")
DEFECTS = QUALITY + COMPLIANCE


@dataclass
class InspectionProfile:
    name: str = "Demo White Background"
    version: str = "demo-v1"
    require_white: bool = True
    min_short_side: int = 60
    min_long_side: int = 80
    white_min_channel: int = 235
    white_max_spread: int = 15
    min_white_fraction: float = 0.90
    max_background_distance: float = 10.0
    min_coverage: float = 0.55
    max_coverage: float = 0.90
    max_center_offset: float = 0.10
    min_margin: float = 0.025
    # These are transparent starting heuristics, not calibrated performance claims.
    min_sharpness: float = 65.0
    min_contrast: float = 35.0
    max_noise: float = 8.0
    max_dark_fraction: float = 0.65
    max_bright_fraction: float = 0.65
    min_exposure_luminance: float = 35.0
    max_exposure_luminance: float = 235.0
    review_band: float = 0.12
    calibration_short_min: int = 48
    calibration_short_max: int = 100
    calibration_long_min: int = 64
    calibration_long_max: int = 140
    calibrated: bool = False
    calibration_id: str | None = None
    sharpness_metric: str = "laplacian_variance"
    max_blur_noise: float | None = None
    quality_review_bands: dict[str, float] = field(default_factory=dict)
    quality_review_reasons: dict[str, str] = field(default_factory=dict)
    quality_pass_review_reasons: dict[str, str] = field(default_factory=dict)

    def validate(self) -> "InspectionProfile":
        if self.sharpness_metric not in {"laplacian_variance", "laplacian_sobel_ratio"}:
            raise ValueError("Unknown sharpness metric.")
        if self.max_blur_noise is not None and (isinstance(self.max_blur_noise, bool)
            or not isinstance(self.max_blur_noise, (int, float))
            or not math.isfinite(self.max_blur_noise) or self.max_blur_noise < 0):
            raise ValueError("Blur noise guard must be a finite nonnegative number.")
        quality_checks = set(QUALITY) - {"low_resolution"}
        if not isinstance(self.quality_review_bands, dict) or any(
            key not in quality_checks or isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 0.5
            for key, value in self.quality_review_bands.items()
        ):
            raise ValueError("Per-check review bands must be finite numbers within 0..0.5.")
        if not isinstance(self.quality_review_reasons, dict) or any(
            key not in quality_checks or not isinstance(value, str) or not value.strip()
            for key, value in self.quality_review_reasons.items()
        ):
            raise ValueError("Unsupported quality checks need a specific review reason.")
        if not isinstance(self.quality_pass_review_reasons, dict) or any(
            key not in quality_checks or not isinstance(value, str) or not value.strip()
            for key, value in self.quality_pass_review_reasons.items()
        ):
            raise ValueError("Unsupported quality passes need a specific review reason.")
        if not isinstance(self.name, str) or not self.name.strip() or not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Profile name and version must be nonempty strings.")
        if type(self.require_white) is not bool or type(self.calibrated) is not bool:
            raise ValueError("Profile flags must be booleans.")
        for attribute in fields(self):
            value = getattr(self, attribute.name)
            if attribute.type in {int, float} and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                raise ValueError(f"{attribute.name} must be a number.")
        if self.calibration_id is not None and (not isinstance(self.calibration_id, str) or not self.calibration_id.strip()):
            raise ValueError("Calibration ID must be a nonempty string when provided.")
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"{key} must be finite and nonnegative.")
        for key in ("min_short_side", "min_long_side", "white_min_channel", "white_max_spread", "calibration_short_min", "calibration_short_max", "calibration_long_min", "calibration_long_max"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"{key} must be an integer.")
        if not 1 <= self.min_short_side <= self.min_long_side:
            raise ValueError("Resolution limits must be positive and ordered.")
        if not 0 <= self.white_min_channel <= 255 or not 0 <= self.white_max_spread <= 255:
            raise ValueError("RGB thresholds must be between 0 and 255.")
        if not 0 < self.min_coverage < self.max_coverage <= 1:
            raise ValueError("Coverage must satisfy 0 < minimum < maximum <= 1.")
        for key in ("min_white_fraction", "max_center_offset", "max_dark_fraction", "max_bright_fraction"):
            if not 0 <= getattr(self, key) <= 1:
                raise ValueError(f"{key} must be between 0 and 1.")
        if not 0 <= self.min_margin < 0.5 or not 0 <= self.review_band <= 0.5:
            raise ValueError("Margin and review band are out of range.")
        if 2 * self.min_margin + self.min_coverage > 1:
            raise ValueError("Minimum margins leave too little room for minimum coverage.")
        if not 0 <= self.min_exposure_luminance < self.max_exposure_luminance <= 255:
            raise ValueError("Exposure luminance thresholds must be ordered within 0..255.")
        if not 1 <= self.calibration_short_min <= self.calibration_short_max or not 1 <= self.calibration_long_min <= self.calibration_long_max:
            raise ValueError("Calibration dimension ranges must be positive and ordered.")
        if self.calibrated and not self.calibration_id:
            raise ValueError("Calibrated profiles need a calibration ID.")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "InspectionProfile":
        try:
            return cls(**value).validate()
        except TypeError as exc:
            raise ValueError(f"Invalid profile fields: {exc}") from exc


def demo_profiles() -> dict[str, InspectionProfile]:
    return {
        "Demo White Background": InspectionProfile(),
        "Demo Uniform Background": InspectionProfile(name="Demo Uniform Background", require_white=False),
    }


@dataclass
class CheckResult:
    identifier: str
    group: str
    status: Status
    measurement: dict[str, Any]
    threshold: dict[str, Any]
    explanation: str
    evidence: str | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def aggregate(checks: list[CheckResult]) -> Status:
    active = [c.status for c in checks if c.status != Status.DISABLED]
    if Status.FAIL in active:
        return Status.FAIL
    if not active or Status.REVIEW in active:
        return Status.REVIEW
    return Status.PASS


@dataclass
class InspectionResult:
    inspection_id: str
    created_at: str
    input_hash: str
    width: int
    height: int
    pipeline_version: str
    profile: dict
    photo_context: PhotoContext
    checks: list[CheckResult]
    notes: list[str]
    segmentation: dict
    duration_ms: float
    original_png: bytes = field(repr=False)
    mask_png: bytes = field(repr=False)
    overlay_png: bytes = field(repr=False)

    @property
    def quality_status(self) -> Status:
        return aggregate([c for c in self.checks if c.group == "quality"])

    @property
    def compliance_status(self) -> Status:
        return aggregate([c for c in self.checks if c.group == "compliance"])

    @property
    def status(self) -> Status:
        return aggregate(self.checks)

    def to_dict(self) -> dict:
        payload = {k: v for k, v in self.__dict__.items() if not k.endswith("_png")}
        payload["checks"] = [c.to_dict() for c in self.checks]
        payload.update(status=self.status, quality_status=self.quality_status, compliance_status=self.compliance_status)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, allow_nan=False)
