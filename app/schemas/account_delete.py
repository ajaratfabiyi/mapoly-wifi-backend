from typing import Literal

from pydantic import BaseModel, ConfigDict


class DeleteAccountIn(BaseModel):
    """The admin must type the account's email again, so a misclick cannot delete it."""

    model_config = ConfigDict(extra="forbid")

    confirm_email: str


class ResetDatabaseIn(BaseModel):
    """Only the exact word RESET is accepted."""

    model_config = ConfigDict(extra="forbid")

    confirm: Literal["RESET"]


class ResetDatabaseOut(BaseModel):
    accounts_deleted: int
