"""宠妃禁足 vs 年羹尧带人（rules.md 年羹尧条目 / 宠妃条目）。

裁定：禁足锁的是「当事人今夜自己做的决定」。年羹尧带人是死亡触发型技能里唯一
一个死人今夜仍要做的决定，因此**照常可被禁足挡下**——无论他是白天死的还是夜里
死的，两条路径口径必须一致。其余死亡触发（玉娆陪葬、孙答应连锁、敬妃死讯、
皇后继任、祺贵人白天检举导致的随机死亡）当事人今夜没有决定，禁足挡不下。

「不可连续禁足同一人」照常生效：宠妃上一夜已禁足过年羹尧，今夜可以再点他，
但禁足无效 → 带人照常落下。

运行：python tests/test_nian_jinzu.py （或 pytest tests/）
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402
from tests.test_night_order import make_game, seat_of_role  # noqa: E402

ROLES = ["niangengyao", "jinxi", "xiaoyunzi", "qifei", "zhenhuan",
         "huafei", "anlingrong", "huangshang"]


def fill_all(g, picks):
    """把当夜每一步都录入：picks 里指定的用给定目标，其余一律「无行动」。"""
    for s in g["night_state"]["steps"]:
        if s["id"] in picks:
            engine.record_action(g, s["id"],
                                 {"no_action": False, "targets": picks[s["id"]]})
        else:
            engine.record_action(g, s["id"], {"no_action": True, "targets": []})


def night2(day_death=True):
    """夜 2 盘面：齐妃已是宠妃；年羹尧按 day_death 决定死法。

    day_death=True  → 上个白天被处决，带人入 pending_night_events（今夜再问）；
    day_death=False → 今夜活着进入夜晚，由恶魔刀走破晓结算那条路径。
    """
    g = engine.start_game(make_game(ROLES))
    g["night_number"] = 2
    fav = seat_of_role(g, "qifei")
    fav.update({"is_favored": True, "alignment": "evil"})
    if day_death:
        g["phase"] = "day"
        engine._kill(g, {"deaths": [], "notes": [], "packets": []},
                     seat_of_role(g, "niangengyao")["seat"], "execution")
        assert [ev["type"] for ev in g["pending_night_events"]] == ["nian_takealong"], \
            "白天处决年羹尧应留下 nian_takealong 待办"
        g["phase"] = "night"
    engine.build_night_steps(g)
    return g


def seats(g, *rids):
    return [seat_of_role(g, r)["seat"] for r in rids]


def resolve(g, picks, answers):
    fill_all(g, picks)
    result, ng = engine.resolve_night(g, answers, commit=True)
    assert "pending" not in result, f"结算不应挂起: {result}"
    return result["report"], ng


def died(rpt, seat):
    return any(d["seat"] == seat for d in rpt["deaths"])


def test_day_death_jinzu_blocks_takealong():
    g = night2()
    nian, victim = seats(g, "niangengyao", "jinxi")
    rpt, ng = resolve(g, {"jinzu": [nian]}, {f"nian_{nian}": victim})
    assert not died(rpt, victim), \
        f"年羹尧被禁足，带人应落空（notes: {rpt['notes']}）"
    assert nian in rpt["voided"], "禁足死者也要进权威名单（带人是他今夜的行动）"
    assert any("被宠妃禁足" in x and "年羹尧带人" in x for x in rpt["notes"]), \
        f"应有一条落空备注: {rpt['notes']}"
    # 技能照常消耗：复活以外不再有第二次机会
    assert engine.get_p(ng, nian)["flags"]["takealong_used"] is True


def test_day_death_other_jinzu_target_lets_takealong_through():
    g = night2()
    nian, victim, other = seats(g, "niangengyao", "jinxi", "xiaoyunzi")
    rpt, _ = resolve(g, {"jinzu": [other]}, {f"nian_{nian}": victim})
    assert died(rpt, victim), f"宠妃禁足了别人，带人应照常落下（notes: {rpt['notes']}）"


def test_no_favored_takealong_still_works():
    """没有宠妃时行为不变（回归：带人的结算从夜初挪到了禁足之后）。"""
    g = engine.start_game(make_game(ROLES))
    g["night_number"] = 2
    g["phase"] = "day"
    nian, victim = seats(g, "niangengyao", "jinxi")
    engine._kill(g, {"deaths": [], "notes": [], "packets": []}, nian, "execution")
    g["phase"] = "night"
    engine.build_night_steps(g)
    assert not any(s["id"] == "jinzu" for s in g["night_state"]["steps"])
    rpt, _ = resolve(g, {}, {f"nian_{nian}": victim})
    assert died(rpt, victim), f"无宠妃时带人应照常落下（notes: {rpt['notes']}）"


def test_consecutive_jinzu_is_void_so_takealong_lands():
    """连续两晚禁足同一人 → 禁足无效 → 带人照常落下（用户明确裁定）。"""
    g = night2()
    nian, victim = seats(g, "niangengyao", "jinxi")
    seat_of_role(g, "qifei")["flags"]["last_jinzu"] = nian
    rpt, _ = resolve(g, {"jinzu": [nian]}, {f"nian_{nian}": victim})
    assert rpt["voided"] == [], "连禁同一人时禁足无效，voided 必须为空"
    assert died(rpt, victim), f"禁足无效，带人应照常落下（notes: {rpt['notes']}）"
    assert any("连续两晚禁足同一人" in x for x in rpt["notes"]), rpt["notes"]


def test_impaired_favored_jinzu_is_void_so_takealong_lands():
    """宠妃自己醉/毒 → 禁足无效 → 带人照常落下。"""
    g = night2()
    nian, victim = seats(g, "niangengyao", "jinxi")
    engine.add_status(seat_of_role(g, "qifei"), "drunk", "dunqinwang", "dawn", 3)
    rpt, _ = resolve(g, {"jinzu": [nian]}, {f"nian_{nian}": victim})
    assert rpt["voided"] == [], "宠妃醉酒时禁足无效，voided 必须为空"
    assert died(rpt, victim), f"禁足无效，带人应照常落下（notes: {rpt['notes']}）"


def test_night_death_jinzu_blocks_takealong():
    """夜里被刀那条路径（破晓结算里的 _kill）口径必须与白天死一致。"""
    g = night2(day_death=False)
    nian, victim = seats(g, "niangengyao", "jinxi")
    rpt, _ = resolve(g, {"jinzu": [nian], "kill": [nian]},
                     {f"nian_{nian}": victim})
    assert died(rpt, nian), "年羹尧应被刀死（禁足不防刀）"
    assert not died(rpt, victim), \
        f"年羹尧被禁足，带人应落空（notes: {rpt['notes']}）"


def test_night_death_without_jinzu_takealong_lands():
    g = night2(day_death=False)
    nian, victim, other = seats(g, "niangengyao", "jinxi", "xiaoyunzi")
    rpt, _ = resolve(g, {"jinzu": [other], "kill": [nian]},
                     {f"nian_{nian}": victim})
    assert died(rpt, nian) and died(rpt, victim), \
        f"没禁足年羹尧，带人应照常落下（notes: {rpt['notes']}）"


def test_committed_game_is_json_serializable():
    """_voided 是 set，落地前必须清掉，否则 games.json 写不出去。"""
    g = night2()
    nian, victim = seats(g, "niangengyao", "jinxi")
    _, ng = resolve(g, {"jinzu": [nian]}, {f"nian_{nian}": victim})
    assert "_voided" not in ng and "_answers" not in ng
    json.dumps(ng, ensure_ascii=False)


if __name__ == "__main__":
    for name in sorted(n for n in dir() if n.startswith("test_")):
        globals()[name]()
        print(f"✓ {name}")
    print("禁足 vs 年羹尧带人符合 docs/rules.md 裁定")
