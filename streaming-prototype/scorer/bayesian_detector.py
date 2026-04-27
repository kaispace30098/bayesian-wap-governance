import math


class BayesianAnomalyDetector:
    """
    Normal-Inverse-Gamma conjugate model for sequential anomaly detection.

    Prior parameters:
        mu0    — prior mean
        nu0    — prior pseudo-observation count
        alpha0 — shape (controls variance uncertainty)
        beta0  — scale parameter

    Predictive distribution: Student-t with df=2α₀, location=μ₀,
        scale=sqrt(β₀(ν₀+1)/(α₀ν₀))

    score(x) returns |t-statistic|.  Higher = more anomalous.
    Thresholds: > 3.0 → YELLOW,  > 4.0 → RED
    O(1) per observation — the prior IS the running baseline.
    """

    def __init__(self, mu0: float, nu0: float, alpha0: float, beta0: float) -> None:
        self._init = (mu0, nu0, alpha0, beta0)
        self.mu0    = mu0
        self.nu0    = nu0
        self.alpha0 = alpha0
        self.beta0  = beta0

    # ── public API ────────────────────────────────────────────────────────────

    def score(self, x: float) -> float:
        return abs(x - self.mu0) / self._scale()

    def update(self, x: float) -> None:
        nu1    = self.nu0 + 1
        mu1    = (self.nu0 * self.mu0 + x) / nu1
        alpha1 = self.alpha0 + 0.5
        beta1  = self.beta0 + self.nu0 * (x - self.mu0) ** 2 / (2 * nu1)
        self.mu0, self.nu0, self.alpha0, self.beta0 = mu1, nu1, alpha1, beta1

    def reset(self) -> None:
        """Restore to original prior — used by REGIME_CHANGE override."""
        self.mu0, self.nu0, self.alpha0, self.beta0 = self._init

    def get_params(self) -> dict:
        return {
            "mu0": self.mu0, "nu0": self.nu0,
            "alpha0": self.alpha0, "beta0": self.beta0,
        }

    # ── internals ─────────────────────────────────────────────────────────────

    def _scale(self) -> float:
        return math.sqrt(self.beta0 * (self.nu0 + 1) / (self.alpha0 * self.nu0))
