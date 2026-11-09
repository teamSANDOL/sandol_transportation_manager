"""정류장과 노선 테이블."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Stop(Base):
    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(primary_key=True)
    station_number: Mapped[str] = mapped_column(Text, unique=True)
    station_id: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(SmallInteger)
    polled: Mapped[bool] = mapped_column(Boolean)

    stop_routes: Mapped[list["StopRoute"]] = relationship(
        back_populates="stop", order_by="(StopRoute.route_id, StopRoute.sta_order)"
    )


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    # 01 문서 개요표의 경유지 1, 2. 표에 없는 노선은 null 이다.
    via: Mapped[str | None] = mapped_column(Text)


class StopRoute(Base):
    __tablename__ = "stop_routes"
    __table_args__ = (CheckConstraint("direction IN ('commute', 'return', 'both')"),)

    # 같은 노선이 한 정류장에 두 번 서면(회차 전후) sta_order 가 다른 두 행이다.
    stop_id: Mapped[int] = mapped_column(ForeignKey("stops.id"), primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"), primary_key=True)
    sta_order: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    direction: Mapped[str] = mapped_column(Text)
    gbis_route_id: Mapped[str] = mapped_column(Text)
    dest_name: Mapped[str] = mapped_column(Text)

    stop: Mapped[Stop] = relationship(back_populates="stop_routes")
    route: Mapped[Route] = relationship()
