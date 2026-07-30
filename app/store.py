"""内存 + JSON 文件持久化的对局存储（仅限私人局，无鉴权）。"""

import json
import uuid
from datetime import datetime
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "games.json"


class GameStore:
    def __init__(self, path: Path = DATA_FILE):
        self.path = path
        self.games: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            self.games = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.games, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def create(self, player_count: int, gid: str | None = None) -> dict:
        gid = gid or uuid.uuid4().hex[:8]
        game = {
            "id": gid,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "player_count": player_count,
            "phase": "prepare",
            "seats": [
                {"seat": i + 1, "name": "", "role": None} for i in range(player_count)
            ],
            "setup_id": None,
            "setup_name": None,
        }
        self.games[gid] = game
        self._save()
        return game

    def get(self, gid: str) -> dict | None:
        return self.games.get(gid)

    def update(self, game: dict) -> None:
        self.games[game["id"]] = game
        self._save()

    def delete(self, gid: str) -> bool:
        if gid in self.games:
            del self.games[gid]
            self._save()
            return True
        return False


store = GameStore()
