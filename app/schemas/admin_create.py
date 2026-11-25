from pydantic import BaseModel, ConfigDict, Field


class AdminCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=255)
    full_name: str = Field(min_length=2, max_length=100)
    temporary_password: str = Field(min_length=8, max_length=256)


class AdminCreated(BaseModel):
    id: int
    email: str
