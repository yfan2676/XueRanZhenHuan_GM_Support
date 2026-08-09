"""人数配置漂移测试：docs/rules.md「建议板子配置」表是唯一权威。

BASE_DIST 在三处出现：rules.md 的表格、app/setups.py、static/app.js。
本测试解析文档表格作为权威，断言两份代码副本与之一致；
并对每套预设板子做可行性与生成健全性检查。

运行：python tests/test_setups.py （或 pytest tests/）
"""

import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import setups  # noqa: E402


def doc_dist():
    text = (ROOT / "docs" / "rules.md").read_text(encoding="utf-8")
    m = re.search(r"### 建议板子配置(.*?)(?:\n### |\n## |\Z)", text, re.S)
    assert m, "rules.md 缺少「建议板子配置」小节"
    rows = re.findall(
        r"^\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
        m.group(1), re.M)
    dist = {}
    for count, t, o, mi, d in ((int(x) for x in row) for row in rows):
        assert d == 1, f"{count} 人档恶魔应为 1"
        assert t + o + mi + d == count, f"{count} 人档四列之和 ≠ {count}"
        dist[count] = (t, o, mi)
    assert dist, "「建议板子配置」表里没有解析到任何行"
    return dist


def js_dist():
    text = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const BASE_DIST = \{(.*?)\};", text, re.S)
    assert m, "static/app.js 缺少 BASE_DIST"
    rows = re.findall(r"(\d+):\s*\[(\d+),\s*(\d+),\s*(\d+)\]", m.group(1))
    return {int(c): (int(t), int(o), int(mi)) for c, t, o, mi in rows}


def test_base_dist_matches_doc():
    assert setups.BASE_DIST == doc_dist(), \
        "app/setups.py 的 BASE_DIST 与 rules.md「建议板子配置」表不一致"


def test_frontend_dist_matches_doc():
    assert js_dist() == doc_dist(), \
        "static/app.js 的 BASE_DIST 与 rules.md「建议板子配置」表不一致"


def test_template_ids_unique():
    ids = [t["id"] for t in setups.TEMPLATES]
    assert len(ids) == len(set(ids)), f"板子 id 重复: {ids}"


def test_every_template_feasible_somewhere():
    for t in setups.TEMPLATES:
        counts = [n for n in setups.BASE_DIST if setups.feasible(t, n)]
        assert counts, f"板子「{t['name']}」在任何人数下都不可用"


def test_generate_composition():
    rng = random.Random(42)
    for count in setups.BASE_DIST:
        for t in setups.TEMPLATES:
            if not setups.feasible(t, count):
                continue
            result = setups.generate(count, t["id"], rng)
            roles = result["roles"]
            assert len(roles) == count
            assert len(set(roles)) == count, f"{t['id']}@{count}: 角色重复"
            report = setups.analyze(roles)
            bad = [w for w in report["warnings"] if w.startswith("配置偏离")]
            assert not bad, f"{t['id']}@{count}: {bad}"
            assert report["composition"]["demon"] == 1


if __name__ == "__main__":
    for name in sorted(n for n in dir() if n.startswith("test_")):
        globals()[name]()
        print(f"✓ {name}")
    print("人数配置与 docs/rules.md 一致")
