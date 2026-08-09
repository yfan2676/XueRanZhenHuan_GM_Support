"""两条本桌裁定的回归测试（docs/rules.md 为权威）：

1. **捉奸只看侍寝**：三阿哥与皇上同晚撞上同一个人，只有「侍寝成功」才算捉奸；
   恶魔的刀口与捉奸无关——皇上刀谁、三阿哥染指谁，撞上了只是巧合。
2. **信息按天黑时的盘面**：当夜的信息类技能（小允子、浣碧、敬妃、槿汐）一律按
   夜初快照计算，不受今夜雨露均沾转化、三阿哥解转化的影响——不管成功与否。

运行：python tests/test_night_info_board.py （或 pytest tests/）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402
from tests.test_night_order import make_game, seat_of_role  # noqa: E402
from tests.test_night_info_timing import fill, pkt_of  # noqa: E402


def build(roles, night=1):
    g = engine.start_game(make_game(roles))
    if night > 1:
        g["night_number"] = night
        engine.build_night_steps(g)
    return g


def resolve(g, answers=None):
    """结算未收齐的夜晚：没录入的步骤按「无行动」跳过。"""
    result, _ = engine.resolve_night(g, answers or {}, commit=False)
    assert "pending" not in result, f"不应挂起裁定: {result}"
    return result["report"]


def dead_seats(rpt):
    return {d["seat"] for d in rpt["deaths"]}


def line_of(rpt, kind):
    pkts = pkt_of(rpt, kind)
    assert len(pkts) == 1, f"应恰有一个 {kind} packet，实得 {pkts}"
    return pkts[0]["lines"][0]


# 1皇上 2敬妃 3三阿哥 4小允子 …… 三阿哥的邻座固定为 2 号与 4 号
SANAGE_SEATING = ["huangshang", "jingfei", "sanage", "xiaoyunzi",
                  "guojunwang", "huafei", "jinxi", "zhenhuan"]


def test_kill_same_target_is_not_caught():
    """皇上刀 X、三阿哥染指 X：不算捉奸，三阿哥照常拿手势、照常活着。"""
    g = build(SANAGE_SEATING, night=2)
    jf, sag = seat_of_role(g, "jingfei")["seat"], seat_of_role(g, "sanage")["seat"]
    fill(g, "kill", [jf])
    fill(g, "sanage", [jf])
    rpt = resolve(g)

    assert sag not in dead_seats(rpt), f"撞刀口不应处死三阿哥: {rpt['notes']}"
    assert jf in dead_seats(rpt), "皇上的刀照常落下"
    assert not any("捉奸" in x for x in rpt["notes"]), rpt["notes"]
    assert "成功" in line_of(rpt, "sanage"), "目标是善良女性 → 成功手势"


def test_yulu_success_same_target_is_caught():
    """侍寝**成功**且撞上同一目标：捉奸成立，三阿哥被皇上杀死。"""
    g = build(SANAGE_SEATING)  # 首夜：无刀，雨露均沾可用
    jf, sag = seat_of_role(g, "jingfei")["seat"], seat_of_role(g, "sanage")["seat"]
    fill(g, "yulu", [jf])
    fill(g, "sanage", [jf])
    rpt = resolve(g)

    assert sag in dead_seats(rpt), f"侍寝撞人应处死三阿哥: {rpt['notes']}"
    assert any("捉奸" in x for x in rpt["notes"]), rpt["notes"]


def test_yulu_failure_same_target_is_not_caught():
    """侍寝**失败**（目标为男性角色）撞上同一目标：两人各自失败，无人死亡。"""
    g = build(SANAGE_SEATING)
    xyz, sag = seat_of_role(g, "xiaoyunzi")["seat"], seat_of_role(g, "sanage")["seat"]
    fill(g, "yulu", [xyz])
    fill(g, "sanage", [xyz])
    rpt = resolve(g)

    assert dead_seats(rpt) == set(), f"不应有人死亡: {rpt['notes']}"
    assert "侍寝失败" in line_of(rpt, "yulu")
    assert "失败" in line_of(rpt, "sanage"), "目标是男性角色 → 失败手势"


# 1华妃(邪恶) 2小允子 3敬妃(善良) …… 小允子的邻座固定为 1 号与 3 号
XYZ_SEATING = ["huafei", "xiaoyunzi", "jingfei", "sanage", "huangshang",
               "guojunwang", "jinxi", "zhenhuan"]


def test_xiaoyunzi_ignores_tonight_conversion():
    """今夜才被侍寝转化的邻座，对小允子仍按天黑时的阵营计。"""
    g = build(XYZ_SEATING)
    jf = seat_of_role(g, "jingfei")["seat"]
    fill(g, "yulu", [jf])
    rpt = resolve(g)

    assert any("转化为宠妃" in x for x in rpt["notes"]), rpt["notes"]
    assert "不同阵营" in line_of(rpt, "xiaoyunzi"), \
        "天黑时 1号华妃邪恶、3号敬妃善良 → 不同阵营（转化发生在夜里，今夜不算）"


def test_xiaoyunzi_ignores_tonight_restore():
    """今夜才被三阿哥解转化的邻座宠妃，对小允子仍按天黑时的阵营（邪恶）计。"""
    g = build(XYZ_SEATING, night=2)
    jf = seat_of_role(g, "jingfei")
    jf.update({"is_favored": True, "alignment": "evil", "favored_nights": 1})
    engine.build_night_steps(g)

    fill(g, "sanage", [jf["seat"]])
    rpt = resolve(g)

    assert any("恢复原阵营" in x for x in rpt["notes"]), rpt["notes"]
    assert "同一阵营" in line_of(rpt, "xiaoyunzi"), \
        "天黑时 1号华妃与 3号宠妃同为邪恶 → 同一阵营（解转化发生在夜里，今夜不算）"


# 1皇上 2浣碧 3华妃：恶魔与最近爪牙间距 2（浣碧不与果郡王为邻，故不醉酒）
HUANBI_SEATING = ["huangshang", "huanbi", "huafei", "sanage", "jingfei",
                  "jinxi", "zhenhuan", "xiaoyunzi"]


def test_huanbi_distance_is_pre_conversion():
    """浣碧的间距按天黑时的盘面给出：今夜的侍寝转化不改变它（不管成功与否）。"""
    base = line_of(resolve(build(HUANBI_SEATING)), "huanbi_dist")
    assert "：2" in base, f"1号皇上与 3号华妃间距应为 2，实得 {base}"

    g = build(HUANBI_SEATING)
    fill(g, "yulu", [seat_of_role(g, "huafei")["seat"]])  # 把最近的爪牙转成宠妃
    rpt = resolve(g)
    assert any("转化为宠妃" in x for x in rpt["notes"]), rpt["notes"]
    assert line_of(rpt, "huanbi_dist") == base, "间距不得因今夜的转化而改变"


# ---------------- 说书人备忘：完整行动录 ----------------

def test_memo_records_every_choice():
    """夜晚落地后，备忘里必须能查到今夜每个被唤醒角色选了谁、拿到了什么情报。

    night_state 在 _commit_night 时被清空，行动录是事后向玩家复盘的唯一依据。
    """
    g = build(SANAGE_SEATING, night=2)
    seat = {rid: seat_of_role(g, rid)["seat"]
            for rid in ["huangshang", "jingfei", "sanage", "guojunwang",
                        "jinxi", "huafei", "xiaoyunzi"]}
    fill(g, "protect", [seat["guojunwang"]])
    fill(g, "kill", [seat["jingfei"]])
    fill(g, "sanage", no_action=True)          # 无行动同样要留痕
    fill(g, "jinxi", [seat["huangshang"], seat["huafei"]])
    fill(g, "xiaoyunzi_info")
    fill(g, "huafei", [seat["jinxi"]])

    result, ng = engine.resolve_night(g, {}, commit=True)
    assert ng is not None and result["committed"]
    assert ng.get("night_state") is None, "落地后 night_state 已清空——行动录必须先存好"

    group = ng["memos"][-1]
    assert group["tag"] == "夜2"
    text = "\n".join(group["items"])

    assert "【行动录】" in text and "【结算判定】" in text, text
    # 每一个当夜步骤都要有一行（含 gm 步骤与被动信息步骤）
    steps_n = len(result["report"]["record"]) - 1  # 去掉【行动录】表头
    assert steps_n >= 6, f"行动录条数不足：{result['report']['record']}"
    for want in ["果郡王 · 守护", "皇上 · 君要臣死", "三阿哥 · 染指",
                 "槿汐姑姑 · 打听小道消息", "小允子 · 邻座情报（被动）", "华妃 · 一丈红"]:
        assert want in text, f"行动录缺少「{want}」：\n{text}"
    assert "三阿哥 · 染指｜" in text and "→ 无行动" in text, "无行动也要留痕"
    # 目标写的是具体是谁
    assert f"{seat['jingfei']}号" in text and f"{seat['jinxi']}号" in text, text
    # 情报贴在对应步骤上：槿汐验的这两人里有恶魔（皇上）
    assert "情报：" in text and "有恶魔" in text, text
    # 敬妃被皇上刀死 → 破晓补发的死讯情报也要记
    assert "破晓补发" in text and "邪恶玩家存活" in text, text


def test_memo_records_gm_choice_and_jail():
    """说书人代选（安陵容情报）标注来源；被禁足者留「未唤醒」而不是凭空消失。"""
    g = build(["qifei", "anlingrong", "huangshang", "jingfei", "guojunwang",
               "jinxi", "zhenhuan", "xiaoyunzi"], night=2)
    fav = seat_of_role(g, "qifei")
    fav.update({"is_favored": True, "alignment": "evil"})
    engine.build_night_steps(g)
    xyz = seat_of_role(g, "xiaoyunzi")["seat"]

    fill(g, "jinzu", [xyz])                     # 禁足小允子：他整夜不唤醒
    fill(g, "alr_info", [seat_of_role(g, "jingfei")["seat"]])
    fill(g, "alr_poison", no_action=True)
    fill(g, "xiaoyunzi_info")
    result, ng = engine.resolve_night(g, {}, commit=True)

    text = "\n".join(ng["memos"][-1]["items"])
    assert f"宠妃 · 禁足｜{fav['seat']}号" in text and f"{xyz}号" in text, text
    assert "（说书人选定）" in text, f"gm 步骤应标注来源：\n{text}"
    assert "安陵容 · 香粉下毒｜" in text and "→ 无行动" in text, text
    # 被禁足者的步骤照常留一行（说明为什么他今晚什么都没拿到）
    assert "小允子 · 邻座情报（被动）｜" in text and "被宠妃禁足，未唤醒" in text, text
    assert "被禁足：今晚不结算其信息" in text, f"结算判定应记下禁足落空：\n{text}"


if __name__ == "__main__":
    for name in sorted(n for n in dir() if n.startswith("test_")):
        globals()[name]()
        print(f"✓ {name}")
    print("捉奸口径与夜间信息盘面符合 docs/rules.md")
