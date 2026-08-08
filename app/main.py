"""血战甄嬛传说书人辅助 —— FastAPI 后端。"""

import random
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, auto, engine
from .roles import ROLE_BY_ID, ROLES, TEAM_NAMES
from .setups import MAX_PLAYERS, MIN_PLAYERS, analyze, generate, setups_for
from .store import store

app = FastAPI(title="血战甄嬛传 GM 助手")

# 共享口令保护（设置 GM_PASSWORD 后生效）；须在挂载 static 前装，才能覆盖静态文件
auth.install(app)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------- 请求模型 ----------

class CreateGameBody(BaseModel):
    player_count: int = Field(ge=MIN_PLAYERS, le=MAX_PLAYERS)


class PlayersBody(BaseModel):
    names: list[str]


class AssignBody(BaseModel):
    setup_id: str | None = None


class RolesBody(BaseModel):
    roles: list[str]


class NightActionBody(BaseModel):
    step_id: str
    collected: dict | None = None


class ResolveBody(BaseModel):
    answers: dict = {}
    commit: bool = False


class DayActionBody(BaseModel):
    seat: int
    action: str
    params: dict = {}


class NominateBody(BaseModel):
    nominator: int
    nominee: int


class VoteBody(BaseModel):
    votes: list[int]


# ---------- API ----------

@app.get("/api/meta")
def get_meta():
    return {
        "min_players": MIN_PLAYERS,
        "max_players": MAX_PLAYERS,
        "roles": ROLES,
        "team_names": TEAM_NAMES,
    }


@app.get("/api/setups")
def get_setups(count: int):
    if count < MIN_PLAYERS or count > MAX_PLAYERS:
        raise HTTPException(400, f"人数须在 {MIN_PLAYERS}-{MAX_PLAYERS} 之间")
    return {"setups": setups_for(count)}


@app.post("/api/games")
def create_game(body: CreateGameBody):
    return store.create(body.player_count)


@app.get("/api/games")
def list_games():
    out = []
    for g in store.games.values():
        if g.get("mode") == "auto":
            continue
        out.append({
            "id": g["id"],
            "created_at": g.get("created_at"),
            "phase": g.get("phase") or "prepare",
            "player_count": g["player_count"],
            "setup_name": g.get("setup_name"),
            "names": [s["name"] for s in g["seats"] if s.get("name")],
            "night_number": g.get("night_number"),
            "day_number": g.get("day_number"),
            "winner": g.get("winner"),
        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {"games": out}


@app.delete("/api/games/{gid}")
def delete_game(gid: str):
    if not store.delete(gid):
        raise HTTPException(404, "对局不存在")
    return {"deleted": gid}


@app.get("/api/games/{gid}")
def get_game(gid: str):
    game = store.get(gid)
    if game is None:
        raise HTTPException(404, "对局不存在")
    result = dict(game)
    roles = [s["role"] for s in game["seats"]]
    if all(roles):
        result["analysis"] = analyze(roles)
    return result


@app.put("/api/games/{gid}/players")
def set_players(gid: str, body: PlayersBody):
    game = store.get(gid)
    if game is None:
        raise HTTPException(404, "对局不存在")
    if len(body.names) != game["player_count"]:
        raise HTTPException(400, "名单人数与对局人数不符")
    names = [n.strip() for n in body.names]
    if any(not n for n in names):
        raise HTTPException(400, "存在空的玩家名")
    if len(set(names)) != len(names):
        raise HTTPException(400, "玩家名重复")
    for seat, name in zip(game["seats"], names):
        seat["name"] = name
    store.update(game)
    return game


@app.post("/api/games/{gid}/assign")
def assign_roles(gid: str, body: AssignBody):
    game = store.get(gid)
    if game is None:
        raise HTTPException(404, "对局不存在")
    try:
        result = generate(game["player_count"], body.setup_id, random.Random())
    except ValueError as e:
        raise HTTPException(400, str(e))
    for seat, role_id in zip(game["seats"], result["roles"]):
        seat["role"] = role_id
    game["setup_id"] = result["setup_id"]
    game["setup_name"] = result["setup_name"]
    store.update(game)
    return {**game, "analysis": analyze(result["roles"])}


@app.put("/api/games/{gid}/roles")
def set_roles(gid: str, body: RolesBody):
    """说书人手动调整角色（交换/替换）后整表提交。"""
    game = store.get(gid)
    if game is None:
        raise HTTPException(404, "对局不存在")
    if len(body.roles) != game["player_count"]:
        raise HTTPException(400, "角色数与座位数不符")
    unknown = [r for r in body.roles if r not in ROLE_BY_ID]
    if unknown:
        raise HTTPException(400, f"未知角色: {unknown}")
    virtual = [r for r in body.roles if ROLE_BY_ID[r].get("virtual")]
    if virtual:
        names = "、".join(ROLE_BY_ID[r]["name"] for r in virtual)
        raise HTTPException(400, f"{names} 为衍生身份，不可在备局时分配（rules.md 板子配置）")
    dup = [r for r in set(body.roles) if body.roles.count(r) > 1]
    if dup:
        names = "、".join(ROLE_BY_ID[r]["name"] for r in dup)
        raise HTTPException(400, f"角色重复: {names}")
    for seat, role_id in zip(game["seats"], body.roles):
        seat["role"] = role_id
    store.update(game)
    return {**game, "analysis": analyze(body.roles)}


def _game_or_404(gid: str):
    game = store.get(gid)
    if game is None:
        raise HTTPException(404, "对局不存在")
    return game


def _wrap(fn, *args):
    try:
        return fn(*args)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/games/{gid}/start")
def start_game(gid: str):
    game = _game_or_404(gid)
    _wrap(engine.start_game, game)
    store.update(game)
    return engine.game_view(game)


@app.get("/api/games/{gid}/state")
def game_state(gid: str):
    return engine.game_view(_game_or_404(gid))


@app.post("/api/games/{gid}/night/action")
def night_action(gid: str, body: NightActionBody):
    game = _game_or_404(gid)
    _wrap(engine.record_action, game, body.step_id, body.collected)
    store.update(game)
    return engine.game_view(game)


@app.post("/api/games/{gid}/night/resolve")
def night_resolve(gid: str, body: ResolveBody):
    game = _game_or_404(gid)
    try:
        result, new_game = engine.resolve_night(game, body.answers, body.commit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if new_game is not None:
        store.update(new_game)
        result["game"] = engine.game_view(new_game)
    return result


@app.post("/api/games/{gid}/day/action")
def do_day_action(gid: str, body: DayActionBody):
    game = _game_or_404(gid)
    result = _wrap(engine.day_action, game, body.seat, body.action, body.params)
    store.update(game)
    result["game"] = engine.game_view(game)
    return result


@app.post("/api/games/{gid}/day/nominate")
def do_nominate(gid: str, body: NominateBody):
    game = _game_or_404(gid)
    result = _wrap(engine.nominate, game, body.nominator, body.nominee)
    store.update(game)
    result["game"] = engine.game_view(game)
    return result


@app.post("/api/games/{gid}/day/vote")
def do_vote(gid: str, body: VoteBody):
    game = _game_or_404(gid)
    result = _wrap(engine.vote, game, body.votes)
    store.update(game)
    result["game"] = engine.game_view(game)
    return result


@app.post("/api/games/{gid}/day/end")
def do_end_day(gid: str):
    game = _game_or_404(gid)
    _wrap(engine.end_day, game)
    store.update(game)
    return engine.game_view(game)


# ---------- 无说书人模式（自动 GM 房间） ----------
# 注意：全部为 async def，使处理串行化在事件循环上，避免多玩家并发写同一房间。

class RoomCreateBody(BaseModel):
    player_count: int = Field(ge=MIN_PLAYERS, le=MAX_PLAYERS)


class RoomJoinBody(BaseModel):
    name: str
    seat: int


class RoomReadyBody(BaseModel):
    name: str
    ready: bool = True


class RoomNightBody(BaseModel):
    name: str
    step_id: str
    no_action: bool = False
    targets: list[int] = []


class RoomDecisionBody(BaseModel):
    name: str
    value: int | str


class RoomDayBody(BaseModel):
    name: str
    action: str
    params: dict = {}


class RoomNominateBody(BaseModel):
    name: str
    nominee: int


class RoomVoteBody(BaseModel):
    name: str
    vote: bool


class RoomNameBody(BaseModel):
    name: str


def _room_or_404(rid: str):
    room = auto.get_room(rid)
    if room is None:
        raise HTTPException(404, "房间不存在")
    return room


@app.post("/api/rooms")
async def create_room(body: RoomCreateBody):
    room = auto.create_room(body.player_count)
    return {"id": room["id"], "view": auto.view(room)}


@app.get("/api/rooms/{rid}/view")
async def room_view(rid: str, name: str | None = None):
    return auto.view(_room_or_404(rid), name)


@app.post("/api/rooms/{rid}/join")
async def room_join(rid: str, body: RoomJoinBody):
    room = _wrap(auto.join, _room_or_404(rid), body.name, body.seat)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/ready")
async def room_ready(rid: str, body: RoomReadyBody):
    room = _wrap(auto.set_ready, _room_or_404(rid), body.name, body.ready)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/night/action")
async def room_night_action(rid: str, body: RoomNightBody):
    room = _wrap(auto.night_action, _room_or_404(rid), body.name,
                 body.step_id, body.no_action, body.targets)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/night/decision")
async def room_night_decision(rid: str, body: RoomDecisionBody):
    room = _wrap(auto.night_decision, _room_or_404(rid), body.name, body.value)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/day/action")
async def room_day_action(rid: str, body: RoomDayBody):
    room = _wrap(auto.day_action, _room_or_404(rid), body.name, body.action, body.params)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/day/nominate")
async def room_nominate(rid: str, body: RoomNominateBody):
    room = _wrap(auto.nominate, _room_or_404(rid), body.name, body.nominee)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/day/vote")
async def room_vote(rid: str, body: RoomVoteBody):
    room = _wrap(auto.cast_vote, _room_or_404(rid), body.name, body.vote)
    return auto.view(room, body.name)


@app.post("/api/rooms/{rid}/day/end")
async def room_end_day(rid: str, body: RoomNameBody):
    room = _wrap(auto.toggle_end_day, _room_or_404(rid), body.name)
    return auto.view(room, body.name)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
