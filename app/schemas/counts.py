from pydantic import BaseModel


class UserCounts(BaseModel):
    total: int
    by_type: dict[str, int]
    active: int
    disabled: int
    admins: int
    paid: int
    free: int
