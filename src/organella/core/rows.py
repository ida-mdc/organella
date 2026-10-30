from enum import IntEnum, StrEnum


class RowType(StrEnum):
    OBJECT = "object"
    ENTITY = "entity"
    INSTANCE = "instance"
    DISTANCE = "distance"
    BASELINE = "baseline"
    CONTACT = "contact"


class _Depth(IntEnum):
    OBJECT = 0
    ENTITY = 1
    DEEP = 2


OBS_LEVEL: dict[RowType, int] = {
    RowType.OBJECT: _Depth.OBJECT,
    RowType.ENTITY: _Depth.ENTITY,
    RowType.INSTANCE: _Depth.DEEP,
    RowType.DISTANCE: _Depth.DEEP,
    RowType.BASELINE: _Depth.DEEP,
    RowType.CONTACT: _Depth.DEEP,
}
