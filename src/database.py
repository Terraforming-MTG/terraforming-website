from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column,
    relationship
)

class Base(DeclarativeBase):
    ...

class Card(Base):
    __tablename__ = "cards"
    
    oracle_id: Mapped[str] = mapped_column(
        String(36), primary_key=True
    )
    
    list_entries = relationship(
        "FormatListCard",
        back_populates="card"
    )
    
class ScryfallCardCache(Base):
    __tablename__ = "scryfall_card_cache"

    oracle_id: Mapped[str] = mapped_column(
        String(36), primary_key=True
    )
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    scryfall_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

class FormatList(Base):
    __tablename__ = "format_lists"
    
    id: Mapped[int] = mapped_column(
        primary_key=True
    )
    
    name: Mapped[str] = mapped_column(String)
    slug: Mapped[str] = mapped_column(
        String, unique=True
    )
    
    cards = relationship(
        "FormatListCard",
        back_populates="format_list"
    )

class FormatListCard(Base):
    __tablename__ = "format_list_cards"
    
    list_id: Mapped[int] = mapped_column(
        ForeignKey("format_lists.id"),
        primary_key=True
    )
    
    oracle_id: Mapped[str] = mapped_column(
        ForeignKey("cards.oracle_id"),
        primary_key=True
    )
    
    reason: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    
    card = relationship(
        "Card", back_populates="list_entries"
    )
    
    format_list = relationship(
        "FormatList", back_populates="cards"
    )

banlist = FormatList(
    name="Banlist",
    slug="banlist"
)

restricted = FormatList(
    name="Restricted",
    slug="restricted"
)
