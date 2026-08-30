"""Google Calendar event payload models (skeleton)."""

from typing import Any

from pydantic import BaseModel, Field


class GoogleEventPayload(BaseModel):
    """Minimal shape for events.insert / events.update."""

    summary: str = ""
    description: str = ""
    location: str = ""
    color_id: str = "5"
    transparency: str = ""
    status: str = ""
    start: dict[str, Any] = Field(default_factory=dict)
    end: dict[str, Any] = Field(default_factory=dict)
    attendees: list[dict[str, str]] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    extended_properties: dict[str, dict[str, str]] = Field(default_factory=dict)
    event_id: str | None = Field(default=None, description="Google Calendar event ID for events.insert")

    def to_calendar_body(self) -> dict[str, Any]:
        """Google Calendar API event resource (events.insert / events.update)."""
        body: dict[str, Any] = {
            "summary": self.summary,
            "description": self.description,
            "location": self.location,
            "colorId": self.color_id,
        }
        if self.transparency:
            body["transparency"] = self.transparency
        if self.status:
            body["status"] = self.status
        if self.start:
            body["start"] = self.start
        if self.end:
            body["end"] = self.end
        if self.attendees:
            body["attendees"] = self.attendees
        if self.source:
            body["source"] = self.source
        if self.extended_properties:
            body["extendedProperties"] = self.extended_properties
        return body

    @staticmethod
    def google_event_id(record_id: int | str, event: str | None = None) -> str:
        """Детерминированный id для Calendar API."""
        rid = str(record_id).strip()
        if not rid.isdigit():
            raise ValueError(
                f"record_id должен быть числом для Google event id, получено: {record_id!r}"
            )
        rid_zfilled = rid.zfill(5) if len(rid) < 5 else rid
        
        # Добавляем префикс "booked" для webhook-записей
        if event and event.startswith("event-"):
            return f"booked{rid_zfilled}"
        
        return rid_zfilled
