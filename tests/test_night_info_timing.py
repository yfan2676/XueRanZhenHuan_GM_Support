"""夜间信息时机测试：本人信息先于本人决定（rules.md 通用裁定 13/14）。

三件事：

1. 前缀不变性——只录了前缀行动时直接调 resolve_night（不 commit）得到的
   packet，与全部录完后的全量结算逐字段一致，且不修改传入的 game。
   这是「收行动途中当场发信息」（安陵容情报、皇上侍寝反馈）的正确性基石。
2. rpt["voided"] 是权威禁足名单——宠妃醉/毒、连禁同一人时禁足无效、名单为空；
   前端不得用 jinzu 原始录入自行推断。
3. 无说书人模式的两道「门」：安陵容下毒在情报送达前锁定、皇上放宝宝/选刀在
   侍寝反馈送达前锁定；提前发放的私信破晓不重复发。

运行：python tests/test_night_info_timing.py （或 pytest tests/）
"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402
from tests.test_night_order import ALL_ROLES, make_game, seat_of_role  # noqa: E402


def fill(g, step_id, targets=None, no_action=False):
    s = next(x for x in g["night_state"]["steps"] if x["id"] == step_id)
    if no_action:
        collected = {"no_action": True, "targets": []}
    elif targets is None:
        pool = [o["seat"] for o in s["pick"]["options"] if not o.get("avoid")]
        collected = {"no_action": False, "targets": pool[:s["pick"]["count"]]}
    else:
        collected = {"no_action": False, "targets": targets}
    engine.record_action(g, step_id, collected)


def fill_upto(g, upto, overrides=None):
    """按夜序把 upto（含）之前的步骤全部录入；overrides: step_id -> targets。"""
    overrides = overrides or {}
    for s in g["night_state"]["steps"]:
        fill(g, s["id"], targets=overrides.get(s["id"]))
        if s["id"] == upto:
            return
    raise AssertionError(f"当夜没有步骤 {upto}")


def pkt_of(rpt, kind):
    return [p for p in rpt["packets"] if p["kind"] == kind]


def night2_game():
    """夜 2 盘面：有宠妃（祺妃）→ 有 jinzu；有尸体 → 有 revive；有 kill。"""
    g = engine.start_game(make_game(ALL_ROLES))
    g["night_number"] = 2
    fav = seat_of_role(g, "qifei")
    fav["is_favored"] = True
    fav["alignment"] = "evil"
    seat_of_role(g, "huanbi")["alive"] = False
    engine.build_night_steps(g)
    return g


# 各步目标避开相互影响：查验/守护指向果郡王系男性（不会被香粉毒到），
# 避免前缀结算里意外弹出 cure_* 裁定
def _safe_overrides(g):
    gjw = seat_of_role(g, "guojunwang")["seat"]
    sng = seat_of_role(g, "sanage")["seat"]
    return {"check": [sng], "protect": [gjw]}


def test_prefix_resolve_alr_info():
    g = engine.start_game(make_game(ALL_ROLES))
    fill_upto(g, "alr_info")
    before = json.dumps(g, ensure_ascii=False, sort_keys=True)

    result, ng = engine.resolve_night(g, {}, commit=False)
    assert ng is None and "pending" not in result, f"前缀结算不应挂起: {result}"
    prefix = pkt_of(result["report"], "alr_info")
    assert len(prefix) == 1, "前缀结算应产出安陵容情报 packet"
    assert result["report"]["deaths"] == [] and result["report"]["revived"] == []
    assert json.dumps(g, ensure_ascii=False, sort_keys=True) == before, \
        "resolve_night 不得修改传入的 game"

    # 录完剩余全部步骤后全量结算，安陵容情报必须逐字段一致（前缀不变性）
    fill_upto(g, g["night_state"]["steps"][-1]["id"], _safe_overrides(g))
    full, _ = engine.resolve_night(g, {}, commit=False)
    assert "pending" not in full, f"全量结算不应挂起: {full}"
    assert pkt_of(full["report"], "alr_info") == prefix


def test_prefix_resolve_yulu_feedback():
    g = night2_game()
    ov = _safe_overrides(g)
    # 禁足指向小允子（不在本测试的信息链上），侍寝指向男性 → 必得「失败」反馈
    ov["jinzu"] = [seat_of_role(g, "xiaoyunzi")["seat"]]
    ov["yulu"] = [seat_of_role(g, "sanage")["seat"]]
    fill_upto(g, "yulu", ov)

    result, _ = engine.resolve_night(g, {}, commit=False)
    assert "pending" not in result, f"前缀结算不应挂起: {result}"
    prefix = pkt_of(result["report"], "yulu")
    assert len(prefix) == 1, "前缀结算应产出侍寝反馈 packet"
    assert "侍寝失败" in prefix[0]["lines"][0]

    fill_upto(g, g["night_state"]["steps"][-1]["id"], ov)
    full, _ = engine.resolve_night(g, {}, commit=False)
    assert "pending" not in full, f"全量结算不应挂起: {full}"
    assert pkt_of(full["report"], "yulu") == prefix


def test_voided_field():
    alr = None

    def resolve_with_fav(prep):
        g = night2_game()
        nonlocal alr
        alr = seat_of_role(g, "anlingrong")["seat"]
        prep(g)
        fill(g, "jinzu", [alr])
        fill(g, "alr_info")
        result, _ = engine.resolve_night(g, {}, commit=False)
        assert "pending" not in result, result
        return result["report"]

    # 正常禁足安陵容：voided 权威名单含她，且她的情报不生成（不唤醒）
    rpt = resolve_with_fav(lambda g: None)
    assert rpt["voided"] == [alr]
    assert pkt_of(rpt, "alr_info") == [], "被禁足者不应有情报 packet"

    # 宠妃醉酒 → 禁足无效，voided 必须为空（前端不得按录入推断）
    rpt = resolve_with_fav(lambda g: engine.add_status(
        seat_of_role(g, "qifei"), "drunk", "test", "dawn", 3))
    assert rpt["voided"] == []
    assert len(pkt_of(rpt, "alr_info")) == 1, "禁足无效时情报照常"

    # 连续两晚禁足同一人 → 禁足无效
    rpt = resolve_with_fav(lambda g: seat_of_role(g, "qifei")["flags"]
                           .__setitem__("last_jinzu", alr))
    assert rpt["voided"] == []
    assert len(pkt_of(rpt, "alr_info")) == 1


def _auto_room(night2=False):
    """手搓一个无说书人房间（内存假 store，杜绝写 data/games.json）。"""
    from app import auto

    class _MemStore:
        def update(self, room):
            return room

    auto.store = _MemStore()
    room = make_game(ALL_ROLES)
    room.update({"mode": "auto", "announcements": [], "private_log": {},
                 "hidden_log": [], "night_answers": {}, "night_pending": None,
                 "vote_state": None, "end_day_votes": [], "ready": {}})
    engine.start_game(room)
    if night2:
        room["night_number"] = 2
        fav = seat_of_role(room, "qifei")
        fav["is_favored"] = True
        fav["alignment"] = "evil"
        seat_of_role(room, "huanbi")["alive"] = False
        engine.build_night_steps(room)
    return auto, auto.prep_night(room)


def _priv(room, rid, needle):
    seat = seat_of_role(room, rid)["seat"]
    entries = room.get("private_log", {}).get(str(seat), [])
    return [ln for e in entries for ln in e["lines"] if needle in ln]


def _my_step_ids(auto, room, rid):
    v = auto.view(room, seat_of_role(room, rid)["name"])
    return {s["id"] for s in v["night"]["my_steps"]}


def _raises(fn, needle):
    try:
        fn()
    except ValueError as e:
        assert needle in str(e), f"报错文案不符: {e}"
    else:
        raise AssertionError(f"应当报错（{needle}）")


def test_auto_first_night_alr_info_immediate():
    auto, room = _auto_room()
    # 首夜无禁足：安陵容情报在开夜（任何人行动之前）就应送达
    assert "alr_info" in room["night_delivered"]
    assert _priv(room, "anlingrong", "告知安陵容"), "开夜即应有安陵容情报私信"


def test_auto_gates():
    auto, room = _auto_room(night2=True)
    name = {rid: seat_of_role(room, rid)["name"]
            for rid in ["qifei", "anlingrong", "huangshang", "wenshichu",
                        "guojunwang", "sanage", "yelanyi", "jinxi", "huafei"]}
    seat = {rid: seat_of_role(room, rid)["seat"]
            for rid in ["xiaoyunzi", "jingfei", "guojunwang", "wenshichu",
                        "sanage", "huanbi", "huafei"]}

    # 门1：禁足未定 → 安陵容情报未发、下毒锁定（不下发、提交报错）
    assert "alr_info" not in room["night_delivered"]
    assert "alr_poison" not in _my_step_ids(auto, room, "anlingrong")
    _raises(lambda: auto.night_action(room, name["anlingrong"], "alr_poison",
                                      targets=[seat["jingfei"]]),
            "先等待你的夜间信息")

    # 宠妃禁足小允子 → 门1立即发放情报、下毒解锁
    room = auto.night_action(room, name["qifei"], "jinzu",
                             targets=[seat["xiaoyunzi"]])
    assert "alr_info" in room["night_delivered"]
    assert _priv(room, "anlingrong", "告知安陵容")
    assert "alr_poison" in _my_step_ids(auto, room, "anlingrong")

    # 门2：侍寝反馈未发 → 皇上的刀锁定
    _raises(lambda: auto.night_action(room, name["huangshang"], "kill",
                                      targets=[seat["huafei"]]),
            "先等待你的夜间信息")
    room = auto.night_action(room, name["anlingrong"], "alr_poison",
                             targets=[seat["jingfei"]])
    room = auto.night_action(room, name["wenshichu"], "check",
                             targets=[seat["guojunwang"]])
    room = auto.night_action(room, name["guojunwang"], "protect",
                             targets=[seat["wenshichu"]])
    assert "kill" not in _my_step_ids(auto, room, "huangshang")

    # 皇上交侍寝（选男性 → 必失败）→ 门2发放反馈、刀解锁
    room = auto.night_action(room, name["huangshang"], "yulu",
                             targets=[seat["sanage"]])
    assert "yulu" in room["night_delivered"]
    assert _priv(room, "huangshang", "侍寝失败")
    assert "kill" in _my_step_ids(auto, room, "huangshang")

    # 收尾整夜，破晓后已发放的信息不得重复
    room = auto.night_action(room, name["huangshang"], "kill",
                             targets=[seat["huafei"]])
    for rid, sid in [("sanage", "sanage"), ("yelanyi", "revive"),
                     ("jinxi", "jinxi"), ("huafei", "huafei")]:
        step = next(s for s in room["night_state"]["steps"] if s["id"] == sid)
        pool = [o["seat"] for o in step["pick"]["options"] if not o.get("avoid")]
        room = auto.night_action(room, name[rid], sid,
                                 targets=pool[:step["pick"]["count"]])
    assert room.get("phase") == "day", f"夜晚应已结算（phase={room.get('phase')}）"
    assert len(_priv(room, "anlingrong", "告知安陵容")) == 1, "情报不得重复发放"
    assert len(_priv(room, "huangshang", "侍寝失败")) == 1, "侍寝反馈不得重复发放"


def test_auto_jailed_anlingrong_not_woken():
    auto, room = _auto_room(night2=True)
    alr = seat_of_role(room, "anlingrong")
    room = auto.night_action(room, seat_of_role(room, "qifei")["name"], "jinzu",
                             targets=[alr["seat"]])
    # 被禁足：无情报、下毒代答无行动（整夜不唤醒），且不阻塞夜晚推进
    assert "alr_info" in room["night_delivered"]
    assert not _priv(room, "anlingrong", "告知安陵容")
    poison = next(s for s in room["night_state"]["steps"]
                  if s["id"] == "alr_poison")
    assert poison["collected"] == {"no_action": True, "targets": [],
                                   "jailed": True}
    assert "alr_poison" not in _my_step_ids(auto, room, "anlingrong")


if __name__ == "__main__":
    for name in sorted(n for n in dir() if n.startswith("test_")):
        globals()[name]()
        print(f"✓ {name}")
    print("夜间信息时机符合 rules.md 裁定 13/14")
