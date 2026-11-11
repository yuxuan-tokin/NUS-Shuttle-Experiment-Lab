from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path


@dataclass
class Config:
    workbook: str = "data/simulator.xlsx"
    population: int = 25000
    population_seed: int = 101
    seed: int = 1
    day: str = "2026-08-17"
    semester_starts: list = field(default_factory=lambda: ["2026-08-10", "2027-01-11"])
    excluded_teaching_dates: list = field(default_factory=lambda: [
        "2026-08-10","2026-10-09","2026-11-09","2027-02-08","2027-03-10","2027-03-26",
    ])
    start: float = 360
    end: float = 1440
    choice_interval: float = 5
    observation_interval: float = 1
    decision_interval: float = 5
    capacity: int = 88
    class_alpha: float = 4
    class_lead_minutes: float = 15.
    class_long_cutoff_minutes: float = 40.
    # Numeric values preserve the earlier ramp for saved research settings.
    class_post_start_peak_minutes: float | None = None
    class_base_score: float = -8.86
    class_distance: float = 2
    class_size_eta: float = 1
    class_destination_multipliers: dict = field(default_factory=dict)
    temperature: float = 1
    home_on_boost: float = 2
    home_off_boost: float = 4
    home_morning_boost: float = 2.
    home_morning_until: float = 450.
    exit_target: float = .95
    on_campus_return_target: float = .90
    exit_check: float = 1410
    breakfast_factor: float = .6
    meal_plan_bonus: float = .5
    meal_peak_strength: float = 5.
    meal_urgency: float = 1.25
    dining_base_score: float = -3.4
    meal_score_adjustments: list = field(default_factory=lambda: [0.,0.,0.])
    shuttle_calibration_scope: str = "all_2km"
    dining_minutes: float | list[float] = field(default_factory=lambda: [30., 40., 50.])
    study_total_weight: float = .3
    study_morning_multiplier: float = .08
    study_morning_ramp_start: float = 450.
    study_morning_ramp_end: float = 570.
    study_continuation_share: float = .7
    study_weights: dict = field(default_factory=lambda: {'library': 8., 'residence': 4., 'general': 2., 'canteen': .25})
    study_evening_down: float = .25
    study_evening_up: float = 2.
    study_evening_starts: float = 1050
    general_study_closes: float = 1260
    residence_study_own_only: bool = True
    dining_meal_windows_are_hours: bool = True
    facility_buildings: dict = field(default_factory=lambda: {
        'ACT:DIN-TERRACE': 'COM3',
        'ACT:DIN-YIH': 'YIH',
        'ACT:STUDY-CLB': 'CLB',
        'ACT:STUDY-ERC': 'ERC',
    })
    standalone_facilities: list = field(default_factory=lambda: [
        'ACT:DIN-TECHNO', 'ACT:DIN-FRONTIER', 'ACT:DIN-DECK',
        'ACT:DIN-KRMRT', 'ACT:DIN-SUPPER',
    ])
    sigma_off: float = .25
    sigma_peak: float = .75
    peak_bands: list = field(default_factory=lambda: [[450, 600], [690, 840], [990, 1170]])
    walk_metres_per_minute: float = 78
    walk_multiplier: float = 1.5
    mrt_gateway_weight: float = 4.
    road_multiplier: float = 1.3
    depot_speed_kmh: float = 20
    history_minutes: float = 30
    rule_share: float = .70
    rule_min_crowded: int = 2
    rule_cooldown: float = 10
    long_wait_minutes: float = 15
    queue_threshold: int = 30
    arm: str = "BASE0"
    examples: str = ""
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_key_env: str = "NEXTBUS_API_KEY"
    llm_temperature: float = 0
    llm_timeout: float = 60
    objective_weights: dict = field(default_factory=dict)
    objective_scales: dict = field(default_factory=dict)
    frozen_calibration: str = ""
    research_settings_confirmed: bool = False

    def validate(self):
        for name in ("choice_interval", "observation_interval", "decision_interval", "temperature", "walk_metres_per_minute", "depot_speed_kmh"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 1 <= self.population <= 30373 or self.capacity != 88:
            raise ValueError("Population must be 1..30373 and capacity must be 88")
        if not self.start < self.end or not 0 <= self.rule_share <= 1:
            raise ValueError("Invalid time window or rule share")
        if min(self.sigma_off, self.sigma_peak, self.class_size_eta) < 0:
            raise ValueError("Noise and class-size exponent must be nonnegative")
        if self.arm not in ("BASE0", "BASE1", "LLM-A", "LLM-B", "LLM-C", "LLM-D"):
            raise ValueError("Unknown experiment arm")
        weights = [self.study_weights[k] for k in ('library','residence','general','canteen')]
        if not all(a>b for a,b in zip(weights,weights[1:])) or weights[-1]<=0:
            raise ValueError('Study weights must follow library > residence > general > canteen > 0')
        if not 0<=self.study_evening_starts<self.general_study_closes<=1440:
            raise ValueError('General study must close after its evening decline and by midnight')
        if self.study_evening_down<=0 or self.study_evening_up<=0:
            raise ValueError('Study time multipliers must be positive')
        for name in ('meal_peak_strength', 'meal_urgency', 'study_total_weight'):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be finite and nonnegative')
        if not all(math.isfinite(value) for value in (self.class_base_score,self.dining_base_score)):
            raise ValueError('Class and dining base scores must be finite')
        if not math.isfinite(self.class_lead_minutes) or self.class_lead_minutes<=0:
            raise ValueError('Class attraction lead time must be finite and positive')
        if not math.isfinite(self.class_long_cutoff_minutes) or self.class_long_cutoff_minutes<=0:
            raise ValueError('Long class attraction cutoff must be finite and positive')
        if any(not math.isfinite(value) or value<=0 for value in self.class_destination_multipliers.values()):
            raise ValueError('Class destination multipliers must be finite and positive')
        if self.class_post_start_peak_minutes is not None and (
            not math.isfinite(self.class_post_start_peak_minutes) or self.class_post_start_peak_minutes<0
        ):
            raise ValueError('Class attraction plateau must be finite and nonnegative')
        durations = self.dining_minutes if isinstance(self.dining_minutes,list) else [self.dining_minutes]*3
        if len(durations)!=3 or any(not math.isfinite(value) or value<30 for value in durations):
            raise ValueError('Dining durations must be at least 30 minutes for breakfast, lunch and dinner')
        if not 0<=self.on_campus_return_target<=1:
            raise ValueError('On-campus return target must be between zero and one')
        if not 0<=self.exit_target<=1:
            raise ValueError('Exit target must be between zero and one')
        if len(self.meal_score_adjustments)!=3 or not all(map(math.isfinite,self.meal_score_adjustments)):
            raise ValueError('Provide three finite meal score adjustments')
        if self.shuttle_calibration_scope not in ('long_feasible','long_feasible_2km','all_2km','all'):
            raise ValueError('Unknown shuttle calibration scope')
        if not math.isfinite(self.walk_multiplier) or self.walk_multiplier<=0:
            raise ValueError('Walking multiplier must be finite and positive')
        if not math.isfinite(self.mrt_gateway_weight) or self.mrt_gateway_weight<1:
            raise ValueError('MRT gateway weight must be finite and at least one')
        if not 0<=self.study_morning_multiplier<=1 or not 0<=self.study_morning_ramp_start<self.study_morning_ramp_end<=1440:
            raise ValueError('Invalid morning study multiplier or ramp times')
        if not math.isfinite(self.home_morning_boost) or self.home_morning_boost<0 or not 0<=self.home_morning_until<=1440:
            raise ValueError('Invalid morning residence boost or cutoff')
        if not 0 <= self.study_continuation_share <= 1:
            raise ValueError('Study continuation share must be between zero and one')
        if set(self.standalone_facilities) & set(self.facility_buildings):
            raise ValueError('A facility cannot be both standalone and assigned to another building')
        return self

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path=None):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8"))).validate() if path else cls().validate()
