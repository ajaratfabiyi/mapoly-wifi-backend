from pydantic import BaseModel, ConfigDict, Field


class DeviceCreate(BaseModel):
    """A device is identified by its MAC, which is NOT NULL and unique in user_devices.
    So adding a device requires its MAC. The draft's name-only form cannot be stored."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    mac: str = Field(max_length=32)


class DevicePriority(BaseModel):
    """Device ids in the order the student wants plans to link to them. Listed first = first."""

    model_config = ConfigDict(extra="forbid")

    device_ids: list[int] = Field(max_length=50)


class DeviceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    auto_renew: bool | None = None
