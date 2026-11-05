"""Dashboard audio controls: what devices exist, and the microphone input volume.
Which devices ekko uses is saved in the general settings (input_device /
output_device); this router only reads hardware and moves the volume."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import audio_control

router = APIRouter(prefix="/api/audio", tags=["audio"])


class VolumeIn(BaseModel):
    level: int | None = Field(None, ge=0, le=100)
    muted: bool | None = None


@router.get("")
def get_audio() -> dict:
    return {**audio_control.list_devices(), "volume": audio_control.get_volume()}


@router.put("/volume")
def put_volume(body: VolumeIn) -> dict:
    vol = audio_control.set_volume(body.level, body.muted)
    if vol is None:
        raise HTTPException(501, "Volume control isn't available on this system.")
    return vol
