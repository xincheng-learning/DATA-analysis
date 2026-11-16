"""Presentation choices only; data, chart types and source definitions stay immutable."""
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field


class ChartAppearance(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title: str | None = Field(default=None,max_length=160)
    x_label: str | None = Field(default=None,max_length=100)
    y_label: str | None = Field(default=None,max_length=100)
    palette: Literal['jade','ocean','sunset'] | None = None
    font_size: int | None = Field(default=None,ge=11,le=24)
    show_legend: bool | None = None
