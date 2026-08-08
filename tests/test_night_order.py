"""夜晚行动顺序漂移测试：docs/rules.md「七、夜晚行动顺序」是唯一权威。

文档是被编辑的对象，本测试只是绊线（docs-first）：解析该节每行末尾的
<!-- step:xxx --> 注释得到权威顺序，然后

1. 构造含全部相关角色的对局，断言 build_night_steps 产出的步骤 id
   （首夜与非首夜）都是权威顺序的子序列，且覆盖各自应有的步骤；
2. 扫描 engine._run_night 源码，断言各步骤结算代码块的先后与权威顺序一致。

运行：python tests/test_night_order.py （或 pytest tests/）
"""

import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import engine  # noqa: E402


def doc_order():
    text = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    m = re.search(r"## 七、夜晚行动顺序(.*?)(?:\n## |\Z)", text, re.S)
    assert m, "rules.md 缺少「七、夜晚行动顺序」小节"
    ids = re.findall(r"<!--\s*step:([a-z_]+)\s*-->", m.group(1))
    assert ids, "「七、夜晚行动顺序」里没有任何 step: 注释"
    assert len(ids) == len(set(ids)), f"step 注释有重复: {ids}"
    return ids


def make_game(roles):
    seats = [{"seat": i + 1, "name": f"P{i + 1}", "role": r}
             for i, r in enumerate(roles)]
    return {"player_count": len(roles), "seats": seats, "phase": "prepare"}


# 覆盖所有会产生夜晚步骤的角色（人数配比与胜负无关，start_game 不校验）
ALL_ROLES = ["jinxi", "guojunwang", "wenshichu", "huanbi", "yurao", "jingfei",
             "sanage", "yelanyi", "xiaoyunzi", "qifei", "zhenhuan",
             "huafei", "anlingrong", "huangshang"]


def seat_of_role(g, rid):
    return next(p for p in g["seats"] if p["role"] == rid)


def assert_subsequence(sub, full, label):
    it = iter(full)
    for x in sub:
        assert x in full, f"{label}: 引擎步骤 {x} 不在文档顺序里"
        for y in it:
            if y == x:
                break
        else:
            raise AssertionError(
                f"{label}: 步骤顺序与文档不符（{x} 越位）\n引擎: {sub}\n文档: {full}")


def test_first_night_order():
    order = doc_order()
    g = engine.start_game(make_game(ALL_ROLES))
    ids = [s["id"] for s in g["night_state"]["steps"]]
    assert_subsequence(ids, order, "首夜")
    for expect in ["evil_info", "alr_info", "alr_poison", "check", "protect",
                   "yulu", "sanage", "huanbi_info", "jingfei_info", "bind",
                   "xiaoyunzi_info", "jinxi", "huafei"]:
        assert expect in ids, f"首夜缺少步骤 {expect}（实际: {ids}）"
    assert "kill" not in ids, "首夜不应有刀"


def test_other_night_order():
    order = doc_order()
    g = engine.start_game(make_game(ALL_ROLES))
    g["night_number"] = 2
    # 制造一名宠妃与一具尸体，让 jinzu / revive 步骤也出现
    fav = seat_of_role(g, "qifei")
    fav["is_favored"] = True
    fav["alignment"] = "evil"
    seat_of_role(g, "huanbi")["alive"] = False
    engine.build_night_steps(g)
    ids = [s["id"] for s in g["night_state"]["steps"]]
    assert_subsequence(ids, order, "夜2")
    for expect in ["jinzu", "alr_info", "alr_poison", "check", "protect",
                   "yulu", "kill", "sanage", "revive", "xiaoyunzi_info",
                   "jinxi", "huafei"]:
        assert expect in ids, f"夜2缺少步骤 {expect}（实际: {ids}）"
    for absent in ["evil_info", "bind", "huanbi_info", "jingfei_info"]:
        assert absent not in ids, f"夜2不应有首夜步骤 {absent}"


# 每个步骤在 _run_night 里的结算锚点（源码文本，需唯一出现在该函数内）
RESOLUTION_ANCHORS = [
    ("jinzu", 'collected("jinzu")'),
    ("alr_info", 'collected("alr_info")'),
    ("alr_poison", 'collected("alr_poison")'),
    ("check", 'collected("check")'),
    ("protect", 'collected("protect")'),
    ("yulu", 'collected("yulu")'),
    ("baby", 'collected("baby")'),
    ("kill", 'collected("kill")'),
    ("sanage", 'collected("sanage")'),
    ("revive", 'collected("revive")'),
    ("huanbi_info", 'role_in_play(g, "huanbi")'),
    ("jingfei_info", 'role_in_play(g, "jingfei")'),
    ("bind", 'collected("bind")'),
    ("xiaoyunzi_info", 'role_in_play(g, "xiaoyunzi")'),
    ("jinxi", 'collected("jinxi")'),
    ("huafei", 'collected("huafei")'),
]


def test_resolution_source_order():
    order = doc_order()
    src = inspect.getsource(engine._run_night)
    pos = {}
    for sid, anchor in RESOLUTION_ANCHORS:
        i = src.find(anchor)
        assert i >= 0, f"_run_night 里找不到 {sid} 的结算锚点（{anchor}）"
        pos[sid] = i
    ordered = [sid for sid in order if sid in pos]
    indices = [pos[sid] for sid in ordered]
    assert indices == sorted(indices), \
        f"_run_night 的结算顺序与文档不符（按文档应为: {ordered}）"


if __name__ == "__main__":
    for name in sorted(n for n in dir() if n.startswith("test_")):
        globals()[name]()
        print(f"✓ {name}")
    print("夜晚顺序与 docs/rules.md 一致")
