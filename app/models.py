from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Recommendation(BaseModel):
    """Final verdict from the orchestrator. Kept flat so the JSON schema has no $refs."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    company_name: str
    verdict: Literal["BUY", "WAIT", "AVOID"] = Field(
        description="BUY = good entry now; WAIT = good stock, wrong time; AVOID = don't buy"
    )
    confidence: Literal["low", "medium", "high"]
    current_price: float | None = Field(description="Latest price seen in the research, or null")
    summary: str = Field(description="Two or three sentences explaining the verdict")
    next_best_time_to_buy: str = Field(
        description="If verdict is not BUY: the condition, price level, or event to wait for. "
        "If BUY: 'Now'."
    )
    entry_price_low: float | None
    entry_price_high: float | None
    stop_loss: float | None
    target_price: float | None
    risk_reward_ratio: float | None = Field(
        description="(target - entry midpoint) / (entry midpoint - stop_loss), or null"
    )
    time_horizon: str = Field(description="e.g. 'swing: 2-6 weeks'")
    bullish_factors: list[str]
    bearish_factors: list[str]


class AgentReport(BaseModel):
    key: str
    title: str
    text: str


class AnalysisResponse(BaseModel):
    recommendation: Recommendation
    reports: list[AgentReport]
