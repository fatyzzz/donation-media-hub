from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from donation_media_hub.models import Track
from donation_media_hub.queue_manager import QueueManager


class QueueTableModel(QAbstractTableModel):
    COLS = ("Src", "Title", "Status")

    def __init__(self, queue: QueueManager) -> None:
        super().__init__()
        self.queue = queue

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.queue.tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self.COLS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.COLS[section]
        return str(section + 1)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        r = index.row()
        c = index.column()
        if r < 0 or r >= len(self.queue.tracks):
            return None
        t = self.queue.tracks[r]

        if role == Qt.DisplayRole:
            if c == 0:
                return t.source
            if c == 1:
                return t.title
            if c == 2:
                return t.status
        if role == Qt.TextAlignmentRole:
            if c in (0, 2):
                return int(Qt.AlignCenter)
            return int(Qt.AlignVCenter | Qt.AlignLeft)
        return None

    def refresh(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def track_at(self, row: int) -> Track | None:
        if 0 <= row < len(self.queue.tracks):
            return self.queue.tracks[row]
        return None

    def row_of_track_id(self, track_id: str | None) -> int:
        if not track_id:
            return -1
        for i, t in enumerate(self.queue.tracks):
            if t.track_id == track_id:
                return i
        return -1
