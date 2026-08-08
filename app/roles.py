"""血战甄嬛传角色目录。

字段说明:
- team: townsfolk 村民 / outsider 外来者 / minion 爪牙 / demon 恶魔
- gender: F / M（用于安陵容下毒、雨露均沾、三阿哥染指等目标限制）
- non_male: 敬妃"非男性角色"计数口径（女性 + 苏培盛 + 温实初，见 rules.md）
"""

ROLES = [
    # ---- 村民 ----
    {"id": "jinxi", "name": "槿汐姑姑", "title": "打听小道消息", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "每晚选择两名玩家，得知其中是否有恶魔乔装。苏培盛在场时对食，开局即成为爪牙。"},
    {"id": "guojunwang", "name": "果郡王", "title": "守护", "team": "townsfolk", "gender": "M", "non_male": False,
     "ability": "每晚守护一人（免刀免睡），可守自己；连守同一人无效。"},
    {"id": "wenshichu", "name": "温实初", "title": "太医", "team": "townsfolk", "gender": "M", "non_male": True,
     "ability": "每晚查验一名玩家是否中毒/醉酒，若是可为其解除。"},
    {"id": "qiguiren", "name": "祺贵人", "title": "瓜六", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "白天公开检举一名玩家身份：猜对当晚随机死一人，猜错自己当晚暴毙。"},
    {"id": "huanbi", "name": "浣碧", "title": "首席丫鬟", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "首夜得知恶魔与最近爪牙的座位间距。邻座是果郡王时醉酒。"},
    {"id": "yurao", "name": "玉娆", "title": "姐妹影分身", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "首夜得知一名善良玩家身份；该玩家被恶魔夜杀时你陪葬。"},
    {"id": "dunqinwang", "name": "敦亲王", "title": "把酒言欢", "team": "townsfolk", "gender": "M", "non_male": False,
     "ability": "每个白天喊出台词，强制一名玩家醉酒一天一夜（公开）。"},
    {"id": "jingfei", "name": "敬妃", "title": "砖妃数砖", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "首夜得知场上非男性角色数量。被皇上杀死时得知存活邪恶玩家数。"},
    {"id": "sanage", "name": "三阿哥", "title": "弘时", "team": "townsfolk", "gender": "M", "non_male": False,
     "ability": "每晚染指左右一名存活邻座：可解除雨露均沾转化；与皇上撞人被捉奸处死；染指女皇当即处死。"},
    {"id": "niangengyao", "name": "年羹尧", "title": "护国大将军", "team": "townsfolk", "gender": "M", "non_male": False,
     "ability": "死亡当晚可带走一名玩家（全局一次）。"},
    {"id": "chunyuan", "name": "纯元皇后", "title": "白月光", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "首次被提名时，若提名者是村民，提名者立即被处决（当天入夜）。"},
    {"id": "xiaoyunzi", "name": "小允子", "title": "有些功夫在身上", "team": "townsfolk", "gender": "M", "non_male": False,
     "ability": "每晚得知左右存活邻座是否同一阵营。"},
    {"id": "yelanyi", "name": "叶澜依", "title": "异域妃子", "team": "townsfolk", "gender": "F", "non_male": True,
     "ability": "夜晚祝福一名死亡玩家：破晓复活、仅一次技能重置，当夜不行动（全局一次）。"},
    # ---- 外来者 ----
    {"id": "qifei", "name": "齐妃", "title": "翠果，打烂她的嘴", "team": "outsider", "gender": "F", "non_male": True,
     "ability": "每个白天禁言一名玩家（不可连续同一人）；被禁言者不得提名，违规说话者被直接处决并入夜。"},
    {"id": "longyue", "name": "胧月", "title": "皇女", "team": "outsider", "gender": "F", "non_male": True,
     "ability": "若因处决而死，你的阵营失败。"},
    {"id": "sundaying", "name": "孙答应", "title": "后妃", "team": "outsider", "gender": "F", "non_male": True,
     "ability": "狂徒死亡当晚你随之死亡；你死亡时全体村民醉酒一天一夜。"},
    {"id": "zhenhuan", "name": "甄嬛", "title": "注意隐藏身份", "team": "outsider", "gender": "F", "non_male": True,
     "ability": "夜晚被皇上/皇后杀死时反杀，成为女皇（新恶魔，每晚一刀）。"},
    {"id": "kuangtu", "name": "狂徒", "title": "祸乱后宫", "team": "outsider", "gender": "M", "non_male": False,
     "ability": "身怀孙答应的赤色肚兜，随时可能被说书人捉奸伏诛；身份暴露即被直接处决。"},
    # ---- 爪牙 ----
    {"id": "supeisheng", "name": "苏培盛", "title": "太监", "team": "minion", "gender": "M", "non_male": True,
     "ability": "场上外来者+1；槿汐姑姑在场时与其对食，槿汐成为爪牙。"},
    {"id": "huanghou", "name": "皇后", "title": "掌管后宫", "team": "minion", "gender": "F", "non_male": True,
     "ability": "存活≥5时：皇上死→继任恶魔（基础刀）；太后死→继任太后。女皇上任则技能失效。"},
    {"id": "huafei", "name": "华妃", "title": "贱人就是矫情", "team": "minion", "gender": "F", "non_male": True,
     "ability": "每晚赐一人一丈红：次日其发起提名即暴毙。仅剩3人时失效。"},
    {"id": "anlingrong", "name": "安陵容", "title": "香粉下毒", "team": "minion", "gender": "F", "non_male": True,
     "ability": "每晚得知一名女性角色；每晚可对一名女性角色下毒至下个黄昏。"},
    # ---- 恶魔 ----
    {"id": "huangshang", "name": "皇上", "title": "君要臣死", "team": "demon", "gender": "M", "non_male": False,
     "ability": "每晚杀一人；雨露均沾（全局一次）转化一名女性角色为宠妃，可同晚使用。"},
    {"id": "taihou", "name": "太后", "title": "宁枉勿纵", "team": "demon", "gender": "F", "non_male": True,
     "ability": "每晚选两名玩家，由说书人决定其中几人死亡。"},
    {"id": "taishanghuang", "name": "回魂太上皇", "title": "心狠手辣", "team": "demon", "gender": "M", "non_male": False,
     "ability": "每晚杀一人，可自刀：上个白天与说书人私聊过的一名玩家继任皇上。"},
    # ---- 衍生身份（不可在备局时分配）----
    # accusable：可以作为祺贵人「检举」的猜测对象。被转化/上位的玩家，现身份才是正确答案，
    # 猜其原角色一律算错（见 rules.md 祺贵人条目）。
    {"id": "nvhuang", "name": "女皇", "title": "甄嬛上位", "team": "demon", "gender": "F", "non_male": True,
     "virtual": True, "accusable": True,
     "ability": "每晚杀一名玩家。不可被三阿哥染指（违者处死）；皇后技能失效，女皇死亡即善良获胜。"},
    {"id": "chongfei", "name": "宠妃", "title": "雨露均沾", "team": "minion", "gender": "F", "non_male": True,
     "virtual": True, "accusable": True,
     "ability": "被皇上侍寝转化：失去原技能，改为每晚禁足一人（连续同一人无效）；存在满三夜后皇上获得宝宝。"},
]

ROLE_BY_ID = {r["id"]: r for r in ROLES}
TEAM_ORDER = ["townsfolk", "outsider", "minion", "demon"]
TEAM_NAMES = {"townsfolk": "村民", "outsider": "外来者", "minion": "爪牙", "demon": "恶魔"}


def ids_by_team(team: str) -> list[str]:
    return [r["id"] for r in ROLES if r["team"] == team and not r.get("virtual")]
