from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from findmejobs.domain.review import ReviewPacketModel, ReviewResultModel


class OpenClawClient(ABC):
    @abstractmethod
    def export_packet(self, packet: ReviewPacketModel) -> Path:
        raise NotImplementedError

    @abstractmethod
    def load_results(self) -> list[ReviewResultModel]:
        raise NotImplementedError


class FilesystemOpenClawClient(OpenClawClient):
    def __init__(self, outbox_dir: Path, inbox_dir: Path) -> None:
        self.outbox_dir = outbox_dir
        self.inbox_dir = inbox_dir
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def export_packet(self, packet: ReviewPacketModel) -> Path:
        if not isinstance(packet, ReviewPacketModel):
            raise TypeError("packet must be a ReviewPacketModel")
        target = self.outbox_dir / f"{packet.packet_id}.json"
        target.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
        return target

    def load_results(self) -> list[ReviewResultModel]:
        results: list[ReviewResultModel] = []
        for path in sorted(self.inbox_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            results.append(ReviewResultModel.model_validate(payload))
        return results
