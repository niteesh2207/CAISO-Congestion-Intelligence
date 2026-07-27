from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class JobState(str, Enum):
    QUEUED="QUEUED"
    DISPATCHED="DISPATCHED"
    RUNNING="RUNNING"
    SUCCEEDED="SUCCEEDED"
    FAILED="FAILED"
    CANCELLED="CANCELLED"


class AnswerDepth(str, Enum):
    SIMPLE="simple"
    PROFESSIONAL="professional"
    EXPERT="expert"


class StudyJob(BaseModel):
    job_id: str
    workspace_id: str
    case_id: str
    question: str
    requested_capabilities: list[str] = Field(default_factory=list)
    required_addons: list[str] = Field(default_factory=list)
    state: JobState = JobState.QUEUED
    answer_profile: str = "power_engineer"
    answer_depth: AnswerDepth = AnswerDepth.PROFESSIONAL
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class SolverAgentHeartbeat(BaseModel):
    agent_id: str
    hostname: str
    simulator_version: str | None = None
    simulator_build: str | None = None
    addons: list[str] = Field(default_factory=list)
    busy: bool = False
    healthy: bool = True
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    max_concurrent_jobs: int = 1


class WorkspaceSummary(BaseModel):
    workspace_id: str
    name: str
    role: str = "analyst"
    case_count: int = 0
    study_count: int = 0
