"""预设板子与半随机分配逻辑。

BASE_DIST[人数] = (村民, 外来者, 爪牙)，恶魔恒为 1。
苏培盛入选爪牙时：外来者 +1、村民 -1（见 rules.md）。
"""

import random

from .roles import ROLE_BY_ID, ids_by_team

BASE_DIST = {
    7: (5, 0, 1),
    8: (5, 1, 1),
    9: (5, 2, 1),
    10: (7, 0, 2),
    11: (7, 1, 2),
    12: (7, 2, 2),
    13: (9, 0, 3),
    14: (9, 1, 3),
    15: (9, 2, 3),
}

MIN_PLAYERS = min(BASE_DIST)
MAX_PLAYERS = max(BASE_DIST)

# 预设模板：demon = 恶魔；required_minions 是模板的核心爪牙（必进），
# 剩余爪牙位从 MINION_POOL 随机补足；required_* 必进；
# outsider_priority 填充顺序；其余村民随机补足。
TEMPLATES = [
    {
        "id": "jingdian",
        "name": "经典后宫",
        "description": "标准配置：信息位齐全、爪牙随机，适合首次接触本 Mod。",
        "demon": "huangshang",
        "required_minions": [],
        "required_townsfolk": [],
        "required_outsiders": [],
        "outsider_priority": ["zhenhuan", "longyue", "qifei"],
    },
    {
        "id": "duishi",
        "name": "对食疑云",
        "description": "苏培盛与槿汐对食：好人以为的信息位开局即是爪牙，外来者+1。",
        "demon": "huangshang",
        "required_minions": ["supeisheng"],
        "required_townsfolk": ["jinxi"],
        "required_outsiders": [],
        "outsider_priority": ["qifei", "sundaying", "zhenhuan"],
    },
    {
        "id": "yulu",
        "name": "雨露均沾",
        "description": "转化流：皇上侍寝拉人、华妃一丈红压提名，三阿哥与果郡王是反制关键。",
        "demon": "huangshang",
        "required_minions": ["huafei"],
        "required_townsfolk": ["sanage", "guojunwang"],
        "required_outsiders": ["zhenhuan"],
        "outsider_priority": ["longyue", "qifei"],
    },
    {
        "id": "taihou",
        "name": "太后垂帘",
        "description": "太后双选一杀，说书人握生死；皇后必在场，太后死后可继任。甄嬛反杀失效。",
        "demon": "taihou",
        "required_minions": ["huanghou"],
        "required_townsfolk": [],
        "required_outsiders": [],
        "outsider_priority": ["longyue", "qifei", "zhenhuan"],
    },
    {
        "id": "huihun",
        "name": "回魂夜",
        "description": "回魂太上皇可自刀传位：上个白天私聊过说书人的玩家继任皇上，私聊有风险；华妃压提名。",
        "demon": "taishanghuang",
        "required_minions": ["huafei"],
        "required_townsfolk": [],
        "required_outsiders": [],
        "outsider_priority": ["qifei", "zhenhuan", "longyue"],
    },
    {
        "id": "jiemei",
        "name": "姐妹情深",
        "description": "甄氏姐妹羁绊：玉娆绑定、浣碧数间距、甄嬛反杀，情报网与陪葬风险并存。",
        "demon": "huangshang",
        "required_minions": [],
        "required_townsfolk": ["yurao", "huanbi"],
        "required_outsiders": ["zhenhuan"],
        "outsider_priority": ["longyue", "qifei"],
    },
    {
        "id": "huoluan",
        "name": "祸乱后宫",
        "description": "狂徒+孙答应双炸弹：一死俱死、全村醉酒，说书人手握捉奸开关。",
        "demon": "huangshang",
        "required_minions": ["supeisheng"],
        "required_townsfolk": [],
        "required_outsiders": ["kuangtu", "sundaying"],
        "outsider_priority": ["qifei"],
    },
    {
        "id": "xiangfen",
        "name": "香粉暗毒",
        "description": "安陵容每晚下毒，温实初是唯一解药：好人信息的真假全看太医验毒的手速。",
        "demon": "huangshang",
        "required_minions": ["anlingrong"],
        "required_townsfolk": ["wenshichu"],
        "required_outsiders": [],
        "outsider_priority": ["qifei", "longyue", "zhenhuan"],
    },
    {
        "id": "anxiang",
        "name": "暗香惑主",
        "description": "太后握生死、安陵容毒信息位：敬妃的数字与浣碧的间距都可能是假的。",
        "demon": "taihou",
        "required_minions": ["anlingrong"],
        "required_townsfolk": ["jingfei", "huanbi"],
        "required_outsiders": [],
        "outsider_priority": ["longyue", "zhenhuan", "qifei"],
    },
]

TEMPLATE_BY_ID = {t["id"]: t for t in TEMPLATES}

# 苏培盛会改变村民/外来者配额，若参与随机补位会让"某个板子是否适用于 N 人"
# 变得不确定，因此他只在模板显式声明时入场，不进随机池。
MINION_POOL = [m for m in ids_by_team("minion") if m != "supeisheng"]


def _slots(count: int, minions: list[str]) -> tuple[int, int, int]:
    t, o, m = BASE_DIST[count]
    if "supeisheng" in minions:
        t, o = t - 1, o + 1
    return t, o, m


def _pick_minions(template: dict, m_slots: int, rng: random.Random) -> list[str]:
    """必进爪牙 + 从随机池补足到 m_slots。"""
    minions = list(template["required_minions"][:m_slots])
    pool = [m for m in MINION_POOL if m not in minions]
    rng.shuffle(pool)
    return minions + pool[: m_slots - len(minions)]


def feasible(template: dict, count: int) -> bool:
    if count not in BASE_DIST:
        return False
    m_slots = BASE_DIST[count][2]
    required = template["required_minions"]
    if len(required) > m_slots:
        return False
    if len(set(required) | set(MINION_POOL)) < m_slots:
        return False
    # 苏培盛不进随机池，所以配额只由 required_minions 决定，与随机结果无关。
    t_slots, o_slots, _ = _slots(count, required)
    if len(template["required_outsiders"]) > o_slots:
        return False
    if len(template["required_townsfolk"]) > t_slots:
        return False
    return True


def setups_for(count: int) -> list[dict]:
    return [
        {"id": t["id"], "name": t["name"], "description": t["description"],
         "demon": ROLE_BY_ID[t["demon"]]["name"]}
        for t in TEMPLATES if feasible(t, count)
    ]


def generate(count: int, template_id: str | None = None, rng: random.Random | None = None) -> dict:
    """返回 {setup_id, setup_name, roles: [role_id 按座位顺序]}。"""
    rng = rng or random.Random()
    candidates = [t for t in TEMPLATES if feasible(t, count)]
    if not candidates:
        raise ValueError(f"无适用于 {count} 人的预设")
    if template_id:
        template = TEMPLATE_BY_ID.get(template_id)
        if template is None or not feasible(template, count):
            raise ValueError(f"预设 {template_id} 不适用于 {count} 人")
    else:
        template = rng.choice(candidates)

    minions = _pick_minions(template, BASE_DIST[count][2], rng)
    t_slots, o_slots, _ = _slots(count, minions)

    outsiders = list(template["required_outsiders"])
    for oid in template["outsider_priority"]:
        if len(outsiders) >= o_slots:
            break
        if oid not in outsiders:
            outsiders.append(oid)
    remaining_out = [o for o in ids_by_team("outsider") if o not in outsiders]
    rng.shuffle(remaining_out)
    outsiders += remaining_out[: o_slots - len(outsiders)]

    townsfolk = list(template["required_townsfolk"])
    remaining_town = [t for t in ids_by_team("townsfolk") if t not in townsfolk]
    rng.shuffle(remaining_town)
    townsfolk += remaining_town[: t_slots - len(townsfolk)]

    roles = townsfolk + outsiders + minions + [template["demon"]]
    assert len(roles) == count, (len(roles), count)
    rng.shuffle(roles)
    return {"setup_id": template["id"], "setup_name": template["name"], "roles": roles}


def analyze(role_ids: list[str]) -> dict:
    """校验 + 生成给说书人的提示。返回 {composition, expected, warnings}。"""
    count = len(role_ids)
    comp = {"townsfolk": 0, "outsider": 0, "minion": 0, "demon": 0}
    for rid in role_ids:
        comp[ROLE_BY_ID[rid]["team"]] += 1

    warnings: list[str] = []
    expected = None
    if count in BASE_DIST:
        t, o, m = BASE_DIST[count]
        if "supeisheng" in role_ids:
            t, o = t - 1, o + 1
        expected = {"townsfolk": t, "outsider": o, "minion": m, "demon": 1}
        for team, want in expected.items():
            if comp[team] != want:
                from .roles import TEAM_NAMES
                warnings.append(f"配置偏离：{TEAM_NAMES[team]}应为 {want}，当前 {comp[team]}")

    if comp["demon"] == 0:
        warnings.append("场上没有恶魔！")
    if comp["demon"] > 1:
        warnings.append("场上有多名恶魔，请确认这是有意为之")

    has = set(role_ids)
    if {"supeisheng", "jinxi"} <= has:
        warnings.append("苏培盛+槿汐同场：槿汐开局即为爪牙（邪恶阵营），首夜与恶魔互认")
    if "supeisheng" in has:
        warnings.append("苏培盛在场：外来者 +1、村民 -1 已计入预期配置")
    if {"kuangtu", "sundaying"} <= has:
        warnings.append("狂徒+孙答应同场：狂徒死→孙答应死→全村民醉酒的连锁")
    elif "sundaying" in has:
        warnings.append("孙答应在场：她死亡时全体村民醉酒一天一夜")
    if "zhenhuan" in has and ("huangshang" in has or "huanghou" in has):
        warnings.append("甄嬛在场：被皇上/皇后夜杀将反杀成为女皇")
    if "chunyuan" in has:
        warnings.append("纯元皇后在场：留意首次提名触发处决")
    if "sanage" in has:
        warnings.append("三阿哥在场：准备成功/失败手势，注意与皇上撞人捉奸")
    if "taihou" in has and "huanghou" in has:
        warnings.append("太后+皇后同场：太后死亡且存活≥5 时皇后继任太后")

    # 座位相关：浣碧邻座果郡王 → 醉酒
    if {"huanbi", "guojunwang"} <= has:
        hi = role_ids.index("huanbi")
        gi = role_ids.index("guojunwang")
        if abs(hi - gi) in (1, count - 1):
            warnings.append("浣碧与果郡王相邻：浣碧醉酒，首夜间距信息可为假")
        else:
            warnings.append("浣碧与果郡王同场但不相邻：当前不醉酒（若换座请重新检查）")

    return {"composition": comp, "expected": expected, "warnings": warnings}
