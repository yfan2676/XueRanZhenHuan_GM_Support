/* 血战甄嬛传 GM 助手 —— 前端逻辑 */

const app = document.getElementById("app");

const state = {
  meta: null,          // {min_players, max_players, roles, team_names}
  roleById: {},
  game: null,          // 后端 game 对象（含 seats）
  analysis: null,
  setups: [],
  chosenSetup: "",     // "" = 随机
  selectedSeat: null,  // 交换模式中第一个选中的座位 index
};

const TEAMS = ["townsfolk", "outsider", "minion", "demon"];

// ---------- 工具 ----------

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = `请求失败 (${res.status})`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  if (typeof attrs === "string" || attrs?.nodeType) {
    children.unshift(attrs);
    attrs = {};
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    el.append(c.nodeType ? c : document.createTextNode(c));
  }
  return el;
}

/* 角色画像：static/img/roles/<角色id>.jpg，可直接替换文件。加载失败时自动隐藏。 */
function roleAvatar(roleId, cls = "sm") {
  if (!roleId) return null;
  return h("img", {
    class: `avatar ${cls}`,
    src: `/img/roles/${roleId}.jpg`,
    alt: "",
    onerror: (e) => e.target.remove(),
  });
}

/* 布局断点与 style.css 保持一致：
   isCompact —— 圆桌已摊平成卡片网格、侧栏移到座位下方（影响提示文案与滚动定位）
   isPhone   —— 真·小屏触屏细节（如不自动弹键盘） */
const isCompact = () => window.matchMedia("(max-width: 980px)").matches;
const isPhone = () => window.matchMedia("(max-width: 700px)").matches;

/* 圆桌半径随人数增长：座位卡宽 120px，相邻两张卡的中心距（弦长）不小于
   SEAT_CHORD 才不至于叠成一坨。260 是 15 人及以下的历史值，保持不变。 */
const SEAT_CHORD = 108;

function tableGeom(n) {
  const radius = Math.max(260, Math.round(SEAT_CHORD / 2 / Math.sin(Math.PI / n)));
  return { radius, size: radius * 2 + 120 };   // size 640 ⟺ radius 260
}

function seatPosition(i, n) {
  const { radius, size } = tableGeom(n);
  const angle = -Math.PI / 2 + (i * 2 * Math.PI) / n;
  return {
    left: `${size / 2 + radius * Math.cos(angle)}px`,
    top: `${size / 2 + radius * Math.sin(angle)}px`,
  };
}

/* 窄屏下圆桌摊平成网格，宽高由 CSS 接管，这里只给桌面端的绝对定位圆桌用 */
function tableSizeStyle(n) {
  return `--table-size:${tableGeom(n).size}px`;
}

/* ---------- 窄屏「环形圆桌」----------
   640px 的圆桌在手机上放不下，但邻座关系必须一眼可见，
   所以把座位摆在网格四条边上（跑道形），中间留出一张小桌子：
   1 号在顶边正中，顺时针绕一圈，与电脑端圆桌方向一致。 */

const RING_MIN_SEAT = 118;   // 单个座位卡最小宽度（含 8px 间距一起算）

/* 在不超过 maxCols 的前提下挑列数：优先列多行少（少滚动），且空位 ≤2 */
function pickRing(n, maxCols) {
  const hi = Math.max(3, Math.min(maxCols, Math.floor(n / 2)));
  let fallback = null;
  for (let cols = hi; cols >= 3; cols--) {
    const rows = Math.max(3, Math.ceil((n + 4 - 2 * cols) / 2));
    const extra = 2 * cols + 2 * rows - 4 - n;   // 环上多出来的空格子
    if (extra < 0) continue;
    if (extra <= 2) return { cols, rows };
    fallback = fallback || { cols, rows };
  }
  return fallback || { cols: 3, rows: Math.max(3, Math.ceil((n - 2) / 2)) };
}

/* 顺时针列出环上的格子；多余的空位留在底边正中（说书人的位置） */
function ringCells(cols, rows, n) {
  const cells = [];
  for (let c = 0; c < cols; c++) cells.push([0, c]);                 // 顶边 →
  for (let r = 1; r < rows - 1; r++) cells.push([r, cols - 1]);      // 右边 ↓
  for (let c = cols - 1; c >= 0; c--) cells.push([rows - 1, c]);     // 底边 ←
  for (let r = rows - 2; r >= 1; r--) cells.push([r, 0]);            // 左边 ↑
  const extra = cells.length - n;
  if (extra > 0)
    cells.splice(cols + (rows - 2) + Math.floor((cols - extra) / 2), extra);
  const k = Math.floor(cols / 2);   // 旋转到 1 号在顶边正中
  return cells.slice(k).concat(cells.slice(0, k));
}

function applyRing(table) {
  if (!table || !table.isConnected || table.classList.contains("names")) return;
  const seats = [...table.querySelectorAll(".seat")];
  const center = table.querySelector(".table-center");
  if (!isCompact()) {   // 电脑端仍是绝对定位的圆桌，清掉行内网格属性
    table.classList.remove("ring");
    table.style.gridTemplateColumns = "";
    for (const el of [...seats, center]) {
      if (!el) continue;
      el.style.gridRow = el.style.gridColumn = "";
    }
    return;
  }
  const width = table.clientWidth || window.innerWidth - 20;
  const maxCols = Math.max(3, Math.floor((width + 8) / (RING_MIN_SEAT + 8)));
  const { cols, rows } = pickRing(seats.length, maxCols);
  const cells = ringCells(cols, rows, seats.length);
  table.classList.add("ring");
  table.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  seats.forEach((el, i) => {
    const [r, c] = cells[i];
    el.style.gridRow = String(r + 1);
    el.style.gridColumn = String(c + 1);
  });
  if (center) {                      // 中间那张“桌子”占满环内空地
    center.style.gridRow = `2 / ${rows}`;
    center.style.gridColumn = `2 / ${cols}`;
  }
}

/* 圆桌容器：插入 DOM 后量一次宽度决定环形排布 */
function ringTable(center, seats, cls = "round-table") {
  const el = h("div", { class: cls, style: tableSizeStyle(seats.length) }, center, ...seats);
  requestAnimationFrame(() => applyRing(el));
  return el;
}

let ringTick = 0;
window.addEventListener("resize", () => {
  cancelAnimationFrame(ringTick);
  // 就地重排而不是整屏重绘：避免旋转屏幕时丢掉输入焦点/未提交的内容
  ringTick = requestAnimationFrame(() =>
    document.querySelectorAll(".round-table").forEach(applyRing));
});

// ---------- 主页：对局管理 / 恢复 ----------

const PHASE_LABEL = (g) => {
  if (!g.phase || g.phase === "prepare") return "备局中";
  if (g.phase === "night") return `第 ${g.night_number} 夜`;
  if (g.phase === "day") return `第 ${g.day_number} 天`;
  if (g.phase === "ended") return g.winner === "good" ? "已结束 · 善良胜" : "已结束 · 邪恶胜";
  return g.phase;
};

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

async function renderHomeScreen() {
  let games = [];
  try {
    games = (await api("/api/games")).games;
  } catch (e) { toast(e.message); }

  const idInput = h("input", {
    class: "gid-input", placeholder: "输入对局 ID 恢复……",
    onkeydown: (e) => { if (e.key === "Enter") resumeGame(idInput.value.trim()); },
  });

  const rows = games.map((g) => h("div", { class: "session-row" },
    h("div", { class: "session-main" },
      h("div", {},
        h("span", { class: "session-id" }, g.id),
        h("span", { class: `session-phase ${g.phase}` }, PHASE_LABEL(g))),
      h("div", { class: "session-sub" },
        `创建于 ${fmtTime(g.created_at)} · ${g.player_count} 人`
        + (g.setup_name ? ` · ${g.setup_name}` : "")
        + (g.names.length ? ` · ${g.names.slice(0, 4).join("、")}${g.names.length > 4 ? "…" : ""}` : ""))),
    h("div", { class: "session-btns" },
      h("button", { class: "small primary", onclick: () => resumeGame(g.id) }, "恢复"),
      h("button", {
        class: "small danger",
        onclick: async () => {
          if (!confirm(`确认删除对局 ${g.id}（${fmtTime(g.created_at)}）？此操作不可撤销。`)) return;
          try {
            await api(`/api/games/${g.id}`, { method: "DELETE" });
            toast(`已删除 ${g.id}`);
            renderHomeScreen();
          } catch (e) { toast(e.message); }
        },
      }, "删除"))
  ));

  app.replaceChildren(
    h("h2", { class: "screen-title" }, "对局管理"),
    h("div", { class: "side-panel wide center-panel" },
      h("div", { class: "actions", style: "justify-content:flex-start" },
        h("button", { class: "primary", onclick: renderCountScreen }, "＋ 开新对局"),
        idInput,
        h("button", { onclick: () => resumeGame(idInput.value.trim()) }, "恢复")),
      h("h3", `历史对局（${games.length}）`),
      ...(rows.length ? rows : [h("div", { class: "log-item" }, "暂无对局，点击「开新对局」开始。")]),
      h("div", { class: "hint", style: "text-align:left;margin-top:8px" },
        "没有说书人？试试 ",
        h("a", { href: "/play.html", style: "color:var(--gold)" }, "无说书人模式（机器人担任 GM，玩家各自入座）"))
    )
  );
}

async function resumeGame(gid) {
  if (!gid) return toast("请输入对局 ID");
  try {
    const view = await api(`/api/games/${gid}/state`);
    state.game = view;
    state.view = view;
    state.answers = {};
    state.nightSel = [];
    state.selectedSeat = null;
    if (!view.phase || view.phase === "prepare") {
      const { setups } = await api(`/api/setups?count=${view.player_count}`);
      state.setups = setups;
      state.chosenSetup = view.setup_id || "";
      const allRoles = view.seats.every((s) => s.role);
      if (allRoles) {
        const g = await api(`/api/games/${gid}`);
        state.game = g;
        state.analysis = g.analysis;
        renderRoleScreen();
      } else {
        renderNameScreen();
      }
    } else {
      renderGameScreen();
    }
  } catch (e) { toast(e.message); }
}

// ---------- 屏幕 1：人数选择 ----------

function renderCountScreen() {
  app.replaceChildren(
    h("h2", { class: "screen-title" }, "第一步 · 选择玩家人数"),
    h("div", { class: "actions" }, h("button", { onclick: renderHomeScreen }, "← 返回对局管理")),
    h("p", { class: "hint" }, "括号内为 村民 / 外来者 / 爪牙 / 恶魔 的基础配置（苏培盛在场时外来者 +1）"),
    h("div", { class: "count-grid" },
      ...range(state.meta.min_players, state.meta.max_players).map((n) => {
        const d = BASE_DIST[n];
        return h("button", {
          class: "count-btn",
          onclick: () => createGame(n),
        }, String(n), h("span", { class: "dist" }, `${d[0]}/${d[1]}/${d[2]}/1`));
      })
    )
  );
}

const BASE_DIST = {
  7: [5, 0, 1], 8: [5, 1, 1], 9: [5, 2, 1],
  10: [7, 0, 2], 11: [7, 1, 2], 12: [7, 2, 2],
  13: [9, 1, 2], 14: [9, 2, 2], 15: [9, 3, 2],
  16: [10, 3, 2],
};

function range(a, b) {
  return Array.from({ length: b - a + 1 }, (_, i) => a + i);
}

async function createGame(n) {
  try {
    state.game = await api("/api/games", {
      method: "POST",
      body: JSON.stringify({ player_count: n }),
    });
    const { setups } = await api(`/api/setups?count=${n}`);
    state.setups = setups;
    state.chosenSetup = "";
    renderNameScreen();
  } catch (e) { toast(e.message); }
}

// ---------- 屏幕 2：圆桌录入姓名 ----------

function renderNameScreen() {
  const n = state.game.player_count;
  const seats = state.game.seats.map((s, i) => {
    const pos = seatPosition(i, n);
    return h("div", { class: "seat", style: `left:${pos.left};top:${pos.top}` },
      h("div", { class: "seat-no" }, `${s.seat} 号`),
      h("input", {
        placeholder: "玩家姓名",
        value: s.name || "",
        "data-i": i,
        oninput: (e) => { state.game.seats[i].name = e.target.value; },
        onkeydown: (e) => {
          if (e.key === "Enter") {
            const next = document.querySelector(`input[data-i="${(i + 1) % n}"]`);
            next && next.focus();
          }
        },
      })
    );
  });

  app.replaceChildren(
    h("h2", { class: "screen-title" }, "第二步 · 录入玩家姓名"),
    h("p", { class: "hint" }, "按座位顺时针填写，回车跳到下一位。座位顺序影响邻座类技能（小允子、三阿哥、浣碧）。"),
    h("div", { class: "table-wrap" },
      h("div", { class: "round-table names", style: tableSizeStyle(n) },
        h("div", { class: "table-center" },
          h("div", { class: "big" }, `${n} 名玩家`),
          h("div", { class: "comp" }, "填写完毕后分配角色")
        ),
        ...seats
      )
    ),
    h("div", { class: "actions" },
      h("button", { onclick: renderCountScreen }, "← 返回"),
      h("button", {
        onclick: () => {
          state.game.seats.forEach((s, i) => { if (!s.name.trim()) s.name = `玩家${i + 1}`; });
          renderNameScreen();
        },
      }, "空位自动命名"),
      h("button", { class: "primary", onclick: submitNames }, "确认名单，分配角色 →")
    )
  );
  // 手机上不自动聚焦：否则一进页面就弹键盘、顶掉半屏内容
  if (!isPhone()) {
    const first = document.querySelector('input[data-i="0"]');
    first && first.focus();
  }
}

async function submitNames() {
  const names = state.game.seats.map((s) => (s.name || "").trim());
  if (names.some((x) => !x)) return toast("还有空的玩家名（可点「空位自动命名」）");
  try {
    state.game = await api(`/api/games/${state.game.id}/players`, {
      method: "PUT",
      body: JSON.stringify({ names }),
    });
    await assignRoles();
  } catch (e) { toast(e.message); }
}

// ---------- 屏幕 3：角色分配与调整 ----------

async function assignRoles() {
  try {
    const data = await api(`/api/games/${state.game.id}/assign`, {
      method: "POST",
      body: JSON.stringify({ setup_id: state.chosenSetup || null }),
    });
    state.game = data;
    state.analysis = data.analysis;
    state.selectedSeat = null;
    renderRoleScreen();
  } catch (e) { toast(e.message); }
}

async function pushRoles(roles) {
  const data = await api(`/api/games/${state.game.id}/roles`, {
    method: "PUT",
    body: JSON.stringify({ roles }),
  });
  state.game = data;
  state.analysis = data.analysis;
}

function renderRoleScreen() {
  const n = state.game.player_count;
  const seats = state.game.seats.map((s, i) => {
    const role = state.roleById[s.role];
    const pos = seatPosition(i, n);
    const cls = [
      "seat", "clickable",
      role ? `team-${role.team}` : "",
      state.selectedSeat === i ? "selected" : "",
    ].join(" ");
    return h("div", {
      class: cls,
      style: `left:${pos.left};top:${pos.top}`,
      title: role ? role.ability : "",
      onclick: () => onSeatClick(i),
    },
      roleAvatar(s.role, "seat-avatar"),
      h("div", { class: "seat-no" }, `${s.seat} 号 · ${s.name}`),
      h("div", { class: "role-name" }, role ? role.name : "—"),
      h("div", { class: "role-title" }, role ? `${TEAM_LABEL(role.team)} · ${role.gender === "F" ? "♀" : "♂"} ${role.title}` : "")
    );
  });

  const comp = state.analysis?.composition || {};
  const expected = state.analysis?.expected || {};
  const compChips = TEAMS.map((t) => {
    const off = expected[t] != null && comp[t] !== expected[t];
    return h("span", { class: `comp-chip ${t} ${off ? "off" : ""}` },
      `${state.meta.team_names[t]} ${comp[t] ?? 0}${expected[t] != null ? ` / ${expected[t]}` : ""}`);
  });

  const selected = state.selectedSeat != null ? state.game.seats[state.selectedSeat] : null;
  const selectedRole = selected ? state.roleById[selected.role] : null;

  app.replaceChildren(
    h("h2", { class: "screen-title" }, "第三步 · 角色分配"),
    h("p", { class: "hint" },
      isCompact()
        ? "点一个座位选中，再点另一个座位交换角色；选中后可在下方面板更换为未使用的角色。"
        : "点击一个座位选中，再点另一个座位交换角色；选中后也可在右侧更换为未使用的角色。悬停座位可查看技能。"),
    h("div", { class: "table-wrap" },
      ringTable(
        h("div", { class: "table-center" },
          h("div", { class: "big" }, state.game.setup_name ? `预设：${state.game.setup_name}` : "自定义配置"),
          h("div", { class: "comp" }, `${n} 名玩家`),
          h("div", { class: "comp-bar", style: "justify-content:center" }, ...compChips)
        ),
        seats
      ),
      h("div", { class: "side-panel" },
        h("h3", "预设板子"),
        h("select", {
          onchange: (e) => { state.chosenSetup = e.target.value; },
        },
          h("option", { value: "" }, "🎲 随机预设"),
          ...state.setups.map((s) =>
            h("option", { value: s.id, ...(state.chosenSetup === s.id ? { selected: "" } : {}) },
              `${s.name}（恶魔：${s.demon}）`))
        ),
        h("div", { class: "setup-desc" },
          state.setups.find((s) => s.id === state.chosenSetup)?.description || "随机从适用于当前人数的预设中抽取一套。"),
        h("button", { onclick: assignRoles }, "🎲 重新分配"),
        selected ? h("div", { class: "detail-box" },
          h("div", { class: "detail-head" },
            roleAvatar(selected.role, "md"),
            h("div", { class: "detail-role" }, `${selected.seat} 号 ${selected.name} — ${selectedRole?.name || ""}`)),
          h("div", {}, selectedRole?.ability || ""),
          h("div", { class: "actions", style: "margin-top:8px; justify-content:flex-start" },
            h("button", { class: "small", onclick: () => openRolePicker(state.selectedSeat) }, "更换角色"),
            h("button", { class: "small", onclick: () => { state.selectedSeat = null; renderRoleScreen(); } }, "取消选中")
          )
        ) : null,
        h("h3", "说书人提示"),
        h("div", { class: "warnings" },
          ...(state.analysis?.warnings?.length
            ? state.analysis.warnings.map((w) =>
                h("div", { class: `warning ${w.startsWith("配置偏离") || w.includes("没有恶魔") || w.includes("多名恶魔") ? "" : "info"}` }, w))
            : [h("div", { class: "warning info" }, "暂无提示")])
        ),
        h("div", { class: "actions", style: "justify-content:flex-start" },
          h("button", { onclick: renderNameScreen }, "← 修改名单"),
          h("button", { class: "primary", onclick: startGame }, "开始游戏 →")
        )
      )
    )
  );
  // 窄屏侧栏在座位网格下方，选中座位后把详情滚进视野
  if (isCompact() && state.selectedSeat != null)
    document.querySelector(".detail-box")?.scrollIntoView({ block: "center", behavior: "smooth" });
}

const TEAM_LABEL = (t) => state.meta.team_names[t] || t;

async function onSeatClick(i) {
  if (state.selectedSeat == null) {
    state.selectedSeat = i;
    renderRoleScreen();
    return;
  }
  if (state.selectedSeat === i) {
    state.selectedSeat = null;
    renderRoleScreen();
    return;
  }
  // 交换两个座位的角色
  const roles = state.game.seats.map((s) => s.role);
  [roles[state.selectedSeat], roles[i]] = [roles[i], roles[state.selectedSeat]];
  try {
    await pushRoles(roles);
    state.selectedSeat = null;
    renderRoleScreen();
  } catch (e) { toast(e.message); }
}

function openRolePicker(seatIdx) {
  const used = new Set(state.game.seats.map((s) => s.role));
  const current = state.game.seats[seatIdx].role;

  const groups = TEAMS.map((team) =>
    h("div", { class: `role-group ${team}` },
      h("h4", state.meta.team_names[team]),
      h("div", { class: "role-options" },
        ...state.meta.roles.filter((r) => r.team === team && !r.virtual).map((r) => {
          const isUsed = used.has(r.id) && r.id !== current;
          return h("div", {
            class: `role-opt ${isUsed ? "used" : ""}`,
            title: r.ability,
            onclick: async () => {
              if (isUsed) return toast(`${r.name} 已被使用，请先与该座位交换`);
              const roles = state.game.seats.map((s) => s.role);
              roles[seatIdx] = r.id;
              try {
                await pushRoles(roles);
                state.selectedSeat = null;
                document.querySelector(".modal-mask")?.remove();
                renderRoleScreen();
              } catch (e) { toast(e.message); }
            },
          }, roleAvatar(r.id, "xs"), r.name, h("span", { class: "gender" }, r.gender === "F" ? "♀" : "♂"));
        })
      )
    )
  );

  const mask = h("div", { class: "modal-mask", onclick: (e) => { if (e.target === mask) mask.remove(); } },
    h("div", { class: "modal" },
      h("h3", `为 ${state.game.seats[seatIdx].seat} 号 ${state.game.seats[seatIdx].name} 更换角色`),
      ...groups
    )
  );
  document.body.appendChild(mask);
}

// ---------- 对局进行中 ----------

async function startGame() {
  try {
    state.view = await api(`/api/games/${state.game.id}/start`, { method: "POST" });
    state.answers = {};
    renderGameScreen();
  } catch (e) { toast(e.message); }
}

async function refreshGame() {
  state.view = await api(`/api/games/${state.game.id}/state`);
  renderGameScreen();
}

function renderGameScreen() {
  const v = state.view;
  if (!v) return;
  if (v.phase === "ended") return renderEndScreen();
  if (v.phase === "night") return renderNightScreen();
  if (v.phase === "day") return renderDayScreen();
  renderRoleScreen();
}

function statusBadges(p) {
  const b = [];
  for (const s of p.statuses || []) {
    if (s.type === "drunk") b.push(["醉", "b-drunk", `醉酒（${s.source}）`]);
    if (s.type === "poisoned") b.push(["毒", "b-poison", `中毒（${s.source}）`]);
    if (s.type === "silenced") b.push(["禁", "b-silence", "被齐妃禁言"]);
    if (s.type === "marked") b.push(["红", "b-marked", "被赐一丈红：明天提名即暴毙"]);
  }
  if (p.is_favored) b.push(["宠", "b-fav", "宠妃（邪恶阵营）"]);
  if (p.is_demon) b.push(["魔", "b-demon", "现任恶魔"]);
  if (!p.alive && p.ghost_vote) b.push(["票", "b-ghost", "幽灵票未使用"]);
  return b.map(([t, c, tip]) => h("span", { class: `badge ${c}`, title: tip }, t));
}

function gameSeatCard(p, i, n, opts = {}) {
  const role = state.roleById[p.role];
  const pos = seatPosition(i, n);
  const cls = ["seat", role ? `team-${role.team}` : "", p.alive ? "" : "dead",
    opts.clickable ? "clickable" : "", opts.selected ? "selected" : ""].join(" ");
  const origNote = p.original_role && p.original_role !== p.role
    ? `（原${state.roleById[p.original_role]?.name || ""}）` : "";
  return h("div", {
    class: cls, style: `left:${pos.left};top:${pos.top}`,
    title: role ? role.ability : "",
    ...(opts.onclick ? { onclick: opts.onclick } : {}),
  },
    roleAvatar(p.role, "seat-avatar"),
    h("div", { class: "seat-no" }, `${p.seat} 号 · ${p.name}${p.alive ? "" : " ☠"}`),
    h("div", { class: "role-name" }, (role ? role.name : "—") + origNote),
    h("div", { class: "badges" }, ...statusBadges(p))
  );
}

function logPanel(v, count = 10) {
  const items = (v.log || []).slice(-count).reverse();
  return h("div", { class: "log-panel" },
    h("h3", "事件记录"),
    ...items.map((x) => h("div", { class: "log-item" }, x)));
}

// 一组备忘的正文：【行动录】（谁选了谁，很长）折叠起来，【结算判定】平铺直显。
// 两个分节标记由 engine.night_record / resolve_night 写入，这里只负责排版。
function memoBody(items) {
  const out = [];
  let fold = null;
  for (const x of items) {
    if (x.startsWith("【行动录】")) {
      fold = h("details", { class: "memo-fold" },
        h("summary", {}, x.replace("【行动录】", "▸ 行动录：")));
      out.push(fold);
      continue;
    }
    if (x.startsWith("【结算判定】")) {
      fold = null;
      continue;
    }
    const line = h("div", { class: "log-item" }, x);
    if (fold) fold.append(line);
    else out.push(line);
  }
  return out;
}

// 说书人备忘（保密）：与"事件记录"分开——事件记录是流水账，这里是当晚的完整行动录
// 与结算判定细节。按事件成组（夜1 / 白天1 …），组倒序，组内保持结算先后。
function memoPanel(v, groupCount = 4) {
  const all = v.memos || [];
  const groups = all.slice(-groupCount).reverse();
  return h("div", { class: "log-panel memo-panel" },
    h("h3", "说书人备忘（保密）"),
    ...(groups.length
      ? groups.map((g) => h("div", { class: "memo-group" },
          h("div", { class: "memo-tag" }, g.tag),
          ...memoBody(g.items)))
      : [h("div", { class: "log-item" }, "暂无备忘")]),
    all.length > groups.length
      ? h("div", { class: "memo-more" }, `⋯ 更早还有 ${all.length - groups.length} 组`)
      : null);
}

// 全桌一览：说书人在任何画面都能看到整桌身份/存活/状态（夜晚、裁定、结算报告都会带上）
function seatStripPanel(v) {
  return h("div", { class: "log-panel seat-strip" },
    h("h3", "全桌一览"),
    ...v.seats.map((p) => {
      const role = state.roleById[p.role];
      const shownRole = p.is_favored ? "宠妃" : (role?.name || "—");
      const origNote = p.is_favored
        ? `（原${role?.name || ""}）`
        : (p.original_role && p.original_role !== p.role
            ? `（原${state.roleById[p.original_role]?.name || ""}）` : "");
      return h("div", { class: `strip-row ${p.alive ? "" : "dead"}` },
        h("span", { class: "strip-seat" }, `${p.seat}号`),
        h("span", { class: "strip-name" }, `${p.name}${p.alive ? "" : " ☠"}`),
        h("span", { class: `strip-role team-${p.is_favored ? "minion" : role?.team || ""}` },
          shownRole + origNote),
        h("span", { class: "badges" }, ...statusBadges(p)));
    }));
}

// ---------- 夜晚向导 ----------

// 从选项里随机抽 count 个：优先只在 avoid !== true 的选项里抽（死者不进池子，
// 给死人发情报是白给）；若池子不够则退回全部选项。
// 只用于 gm 决策（说书人自己选发什么情报 / 结算裁定），玩家指的目标一律不代掷。
function rollOptions(options, count) {
  const pool = options.filter((o) => !o.avoid);
  const bag = (pool.length >= count ? pool : options).slice();
  const out = [];
  while (out.length < count && bag.length)
    out.push(bag.splice(Math.floor(Math.random() * bag.length), 1)[0]);
  return out;
}

// 需要在收行动途中当场传达的信息（rules.md 一.14「本人信息先于本人决定」）：
// 录到该步骤时，把预览结算里对应 kind 的 packet 先念给玩家听，再收他的选择
const STEP_INFO_KIND = { alr_poison: "alr_info", baby: "yulu", kill: "yulu" };

// 后台预览结算：为夜晚收集流提供 voided（权威禁足名单）与提前发放的信息包。
// 遇到裁定点走 renderPending，答完回到夜晚收集流（而非报告页）。
async function refreshNightPreview() {
  if (state.previewBusy) return;
  state.previewBusy = true;
  try {
    const res = await api(`/api/games/${state.game.id}/night/resolve`, {
      method: "POST",
      body: JSON.stringify({ answers: state.answers, commit: false }),
    });
    state.previewBusy = false;
    if (res.pending)
      return renderPending(res.pending,
        () => { state.nightPreview = null; renderNightScreen(); });
    state.nightPreview = res.report;
    renderNightScreen();
  } catch (e) { state.previewBusy = false; toast(e.message); }
}

function renderNightScreen() {
  const v = state.view;
  const ns = v.night_state;
  const n = v.seats.length;
  const steps = ns.steps;
  const curIdx = steps.findIndex((s) => s.collected === null);
  const allDone = curIdx === -1;
  const cur = allDone ? null : steps[curIdx];

  // 入夜初始化：清空上一夜的裁定答案与「已当场传达」记录，并立刻做一次
  // 预览结算，让夜初遗留裁定（祺贵人、年羹尧）在收行动之前就浮出来
  if (state.nightNumber !== ns.number) {
    state.nightNumber = ns.number;
    state.answers = {};
    state.deliveredInfo = {};
    state.nightPreview = null;
  }
  if (!state.nightPreview && !state.previewBusy) refreshNightPreview();

  // 宠妃禁足排在最前，所以轮到被禁足者时这一步早已录入：直接在清单上标出来，
  // 免得说书人白白唤醒他、还照着假信息发情报。
  // 名单以引擎结算的 voided 为准——宠妃自己醉/毒、连禁同一人时禁足是无效的
  const voidedSeats = (state.nightPreview && state.nightPreview.voided) || [];
  const silenced = voidedSeats.length ? voidedSeats[0] : null;

  const stepList = h("div", { class: "step-list" },
    ...steps.map((s, i) => {
      const done = s.collected !== null;
      const voided = silenced != null && s.seat === silenced;
      const cls = ["step-item", done ? "done" : "", i === curIdx ? "current" : "",
                   voided ? "voided" : ""].join(" ");
      let summary = "";
      if (done) {
        if (s.collected.no_action) summary = "无行动";
        else if (s.pick.count > 0) summary = s.collected.targets
          .map((t) => `${t}号`).join("、");
        else summary = "已确认";
      }
      return h("div", {
        class: cls,
        onclick: async () => {
          if (!done) return;
          try {
            state.view = await api(`/api/games/${state.game.id}/night/action`, {
              method: "POST",
              body: JSON.stringify({ step_id: s.id, collected: null }),
            });
            state.nightSel = [];
            state.nightPreview = null;
            renderNightScreen();
          } catch (e) { toast(e.message); }
        },
      }, h("span", { class: "step-t" },
        s.seat ? roleAvatar(v.seats[s.seat - 1].role, "xs") : null, s.title,
        voided ? h("span", { class: "voided-tag" }, "🚫 已被禁足") : null),
        h("span", { class: "step-sum" }, summary));
    })
  );

  // 说书人回头改过早前步骤：已当场传达的信息若与最新结算不一致，必须提醒；
  // 说书人向玩家更正口径后点「已更正」清除警告
  let staleWarn = null;
  if (state.nightPreview) {
    const stale = Object.entries(state.deliveredInfo || {}).filter(([kind, lines]) =>
      lines !== JSON.stringify(
        state.nightPreview.packets.filter((p) => p.kind === kind).map((p) => p.lines)));
    if (stale.length) {
      staleWarn = h("div", { class: "night-warn" },
        h("p", {}, "⚠ 你修改过早前的步骤，之前已当场传达的信息与最新结算不一致——"
          + "请到对应玩家处更正口径："),
        ...stale.map(([kind]) => {
          const pkts = state.nightPreview.packets.filter((p) => p.kind === kind);
          return h("div", {}, ...(pkts.length
            ? pkts.map((p) => h("div", {}, `→ ${p.seat}号 现应为：${p.lines.join("；")}`))
            : [h("div", {}, `→ 原「${kind === "alr_info" ? "安陵容情报" : "侍寝反馈"}」现已不存在（撤回）`)]));
        }),
        h("div", { class: "actions" },
          h("button", {
            onclick: () => {
              for (const [kind] of stale)
                state.deliveredInfo[kind] = JSON.stringify(
                  state.nightPreview.packets.filter((p) => p.kind === kind).map((p) => p.lines));
              renderNightScreen();
            },
          }, "已向玩家更正，清除警告")));
    }
  }

  let card;
  if (cur) {
    state.nightSel = state.nightSel || [];
    const need = cur.pick.count;
    const opts = cur.pick.options.map((o) => {
      const on = state.nightSel.includes(o.seat);
      return h("button", {
        class: `chip ${on ? "on" : ""}`,
        onclick: () => {
          if (on) state.nightSel = state.nightSel.filter((x) => x !== o.seat);
          else {
            state.nightSel.push(o.seat);
            if (state.nightSel.length > need) state.nightSel.shift();
          }
          renderNightScreen();
        },
      }, o.label, o.note ? h("span", { class: "chip-note" }, ` ${o.note}`) : null);
    });
    const jinzuWarn = silenced != null && cur.seat === silenced
      ? h("p", { class: "night-warn" },
          "🚫 本步骤的行动者今夜已被宠妃禁足：照常录入即可，结算时会自动作废，"
          + "信息类技能今夜也不会给出情报。")
      : null;
    // 「本人信息先于本人决定」：录本步之前要当场传达的信息（安陵容情报 / 侍寝反馈）
    let infoBox = null;
    const infoKind = STEP_INFO_KIND[cur.id];
    if (infoKind && state.nightPreview) {
      const pkts = state.nightPreview.packets.filter((p) => p.kind === infoKind);
      if (pkts.length) {
        state.deliveredInfo[infoKind] = JSON.stringify(pkts.map((p) => p.lines));
        infoBox = h("div", { class: "night-warn", style: "border-color:var(--gold)" },
          h("div", { style: "font-weight:bold" },
            "📣 先当场告诉TA，再收本步的选择（本人信息先于本人决定）："),
          ...pkts.map((p) => h("div", { class: `detail-box ${p.malfunction ? "malf" : ""}` },
            h("div", { class: "detail-head" },
              h("div", { class: "detail-role" },
                `→ ${p.seat}号 ${v.seats[p.seat - 1].name}`,
                p.malfunction ? h("span", { class: "malf-tag" }, " 醉/毒：应给假信息！") : null)),
            ...p.lines.map((l) => h("div", {}, l)))));
      }
    }
    card = h("div", { class: "night-card" },
      h("div", { class: "detail-head" },
        cur.seat ? roleAvatar(v.seats[cur.seat - 1].role, "lg") : null,
        h("div", {},
          h("div", { class: "night-actor" }, cur.actor || "说书人"),
          h("h3", cur.title))),
      h("p", { class: "night-prompt" }, cur.prompt),
      jinzuWarn,
      staleWarn,
      infoBox,
      h("div", { class: "chips" }, ...opts),
      h("div", { class: "actions" },
        h("button", {
          class: "primary",
          onclick: () => submitNightAction(cur, false),
        }, need === 0 ? "确认" : `确认（已选 ${state.nightSel.length}/${need}）`),
        cur.gm && need > 0 && cur.pick.options.length
          ? h("button", {
              onclick: () => {
                state.nightSel = rollOptions(cur.pick.options, need).map((o) => o.seat);
                renderNightScreen();
              },
            }, need === 1 ? "🎲 随机" : `🎲 随机 ${need} 人`)
          : null,
        cur.optional ? h("button", { onclick: () => submitNightAction(cur, true) }, "无行动") : null
      )
    );
  } else {
    card = h("div", { class: "night-card" },
      h("h3", "所有行动已收集完毕"),
      staleWarn,
      h("p", { class: "night-prompt" }, "点击下方按钮按规则结算今夜所有行动。"),
      h("div", { class: "actions" },
        // 收行动途中的裁定答案（state.answers）要带进最终结算，不能清空
        h("button", { class: "primary", onclick: () => resolveLoop(false) }, "🌙 开始结算"))
    );
  }

  app.replaceChildren(
    h("h2", { class: "screen-title" }, `第 ${ns.number} 个夜晚${ns.number === 1 ? "（首夜）" : ""}`),
    h("p", { class: "hint" }, `对局 ID：${state.game.id}`),
    h("div", { class: "table-wrap" },
      h("div", { class: "night-left" }, h("h3", "夜晚行动顺序（完整唤醒清单）"), stepList),
      h("div", { class: "side-panel wide panel-first" }, card, seatStripPanel(v), logPanel(v))
    )
  );
}

async function submitNightAction(step, noAction) {
  const need = step.pick.count;
  if (!noAction && need > 0 && (state.nightSel || []).length !== need)
    return toast(`需选择 ${need} 名目标`);
  const collected = noAction
    ? { no_action: true, targets: [] }
    : { targets: state.nightSel || [], no_action: false };
  try {
    state.view = await api(`/api/games/${state.game.id}/night/action`, {
      method: "POST",
      body: JSON.stringify({ step_id: step.id, collected }),
    });
    state.nightSel = [];
    state.nightPreview = null;
    renderNightScreen();
  } catch (e) { toast(e.message); }
}

async function resolveLoop(commit) {
  try {
    const res = await api(`/api/games/${state.game.id}/night/resolve`, {
      method: "POST",
      body: JSON.stringify({ answers: state.answers, commit }),
    });
    if (res.pending) return renderPending(res.pending);
    if (res.committed && res.game) {
      state.view = res.game;
      state.report = null;
      return renderGameScreen();
    }
    state.report = res.report;
    renderNightReport(res.report);
  } catch (e) { toast(e.message); }
}

// next：答完后回到哪条流。默认继续「开始结算」的报告流；
// 收行动途中预览触发的裁定则传入回夜晚收集流的回调
function renderPending(p, next = () => resolveLoop(false)) {
  app.replaceChildren(
    h("h2", { class: "screen-title" },
      p.gm ? "结算中 · 需要说书人裁定" : "结算中 · 需要问玩家本人"),
    h("div", { class: "side-panel wide center-panel" },
      h("h3", p.prompt),
      p.gm ? null : h("p", { class: "hint", style: "text-align:left" },
        "这是玩家自己的选择，请转达后按他的意思录入（不要替他掷骰）。"),
      h("div", { class: "chips" },
        ...p.options.map((o) => h("button", {
          class: "chip",
          onclick: () => { state.answers[p.key] = o.value; next(); },
        }, o.label))
      ),
      // 只有说书人自己的裁定能掷骰；年羹尧带人、太上皇传位本质是玩家的选择
      p.gm
        ? h("div", { class: "actions" },
            h("button", {
              onclick: () => {
                const pick = rollOptions(p.options, 1)[0];
                if (!pick) return;
                state.answers[p.key] = pick.value;
                next();
              },
            }, "🎲 随机裁定"))
        : null,
      seatStripPanel(state.view)
    )
  );
}

function renderNightReport(rpt) {
  const v = state.view;
  const deaths = rpt.deaths.length
    ? rpt.deaths.map((d) => h("div", { class: "warning" },
        `☠ ${d.seat}号 ${d.name}（${state.roleById[d.role]?.name || d.role}）`))
    : [h("div", { class: "warning info" }, "今夜平安无事（无人死亡）")];
  const revived = rpt.revived.map((r) =>
    h("div", { class: "warning info" }, `❀ ${r.seat}号 ${r.name} 复活`));
  const pktBox = (p) => {
    const seat = v.seats[p.seat - 1];
    return h("div", { class: `detail-box ${p.malfunction ? "malf" : ""}` },
      h("div", { class: "detail-head" },
        roleAvatar(p.role, "sm"),
        h("div", { class: "detail-role" },
          `→ ${p.seat}号 ${seat.name}（${state.roleById[p.role]?.name || p.role}）`,
          p.malfunction ? h("span", { class: "malf-tag" }, " 醉/毒：应给假信息！") : null)),
      ...p.lines.map((l) => h("div", {}, l)));
  };
  // 收行动途中已当场传达过的信息（安陵容情报、侍寝反馈）不再列入唤醒清单，
  // 折叠起来仅供复核（rules.md 一.14）
  const deliveredKinds = new Set(Object.keys(state.deliveredInfo || {}));
  const packets = rpt.packets.filter((p) => !deliveredKinds.has(p.kind)).map(pktBox);
  const delivered = rpt.packets.filter((p) => deliveredKinds.has(p.kind)).map(pktBox);
  const notes = rpt.notes.map((x) => h("div", { class: "log-item" }, x));
  // 行动录：今夜每个角色选了谁。结算后 night_state 就没了，这份会一起存进备忘，
  // 事后向玩家复盘全靠它（预览折叠起来，避免挤掉真正要看的判定）。
  const record = (rpt.record || []).slice(1);

  app.replaceChildren(
    h("h2", { class: "screen-title" }, "夜晚结算报告（预览）"),
    h("div", { class: "report-grid" },
      h("div", { class: "side-panel" },
        h("h3", "黎明公布（公开信息）"), ...deaths, ...revived),
      h("div", { class: "side-panel" },
        h("h3", "逐位传递信息（依次唤醒）"),
        ...(packets.length ? packets : [h("div", { class: "log-item" }, "无")]),
        delivered.length
          ? h("details", {},
              h("summary", {}, `已于收行动时当场传达（${delivered.length} 条，复核用）`),
              ...delivered)
          : null),
      h("div", { class: "side-panel" },
        h("h3", "说书人备忘（保密）"),
        ...(notes.length ? notes : [h("div", { class: "log-item" }, "无")]),
        record.length
          ? h("details", { style: "margin-top:8px" },
              h("summary", {}, `今夜行动录（${record.length} 条，将存入备忘）`),
              ...record.map((x) => h("div", { class: "log-item" }, x)))
          : null,
        seatStripPanel(v))
    ),
    h("div", { class: "actions" },
      h("button", { onclick: renderNightScreen }, "← 返回修改行动"),
      h("button", { class: "primary", onclick: () => resolveLoop(true) },
        rpt.winner ? "确认结算（游戏结束）" : "☀ 确认，进入白天")
    )
  );
}

// ---------- 白天 ----------

function renderDayScreen() {
  const v = state.view;
  const n = v.seats.length;
  const ds = v.day_state;
  const seats = v.seats.map((p, i) => gameSeatCard(p, i, n, {
    clickable: true,
    onclick: () => openDayMenu(p),
  }));

  app.replaceChildren(
    h("h2", { class: "screen-title" }, `第 ${v.day_number} 个白天`),
    h("p", { class: "hint" },
      `对局 ID：${state.game.id} · 存活 ${v.alive_count} 人 · 处决门槛 ${v.threshold} 票 · 点击玩家查看/执行白天行动`),
    h("div", { class: "table-wrap" },
      ringTable(
        h("div", { class: "table-center" },
          h("div", { class: "big" }, `第 ${v.day_number} 天`),
          h("div", { class: "comp" }, `存活 ${v.alive_count} · 门槛 ${v.threshold}`),
          ds.ended
            ? h("div", { class: "comp", style: "color:var(--danger)" }, "今日已处决，等待入夜")
            : null
        ),
        seats
      ),
      h("div", { class: "side-panel panel-first" },
        h("div", { class: "actions", style: "justify-content:flex-start" },
          h("button", {
            class: "primary",
            ...(ds.ended ? { disabled: "" } : {}),
            onclick: openNominateModal,
          }, "⚖ 发起提名"),
          h("button", { class: "primary", onclick: endDay }, "🌙 进入夜晚"),
          h("button", { onclick: renderHomeScreen }, "⌂")
        ),
        ds.silenced ? h("div", { class: "warning" },
          `${ds.silenced}号 被齐妃禁言中（违规说话 → 点击该玩家执行处决）`) : null,
        ds.private_chats?.length
          ? h("div", { class: "warning info" },
              "今日私聊过说书人：" + ds.private_chats.map((s) => `${s}号`).join("、"))
          : null,
        h("h3", "提名记录"),
        ...(ds.nominations || []).map((x) => h("div", { class: "log-item" },
          `${x.nominator}号 提名 ${x.nominee}号：${x.votes.length} 票 ${x.passed ? "✔ 处决" : "✘ 未过"}`)),
        memoPanel(v),
        logPanel(v)
      )
    )
  );
}

function openDayMenu(p) {
  const v = state.view;
  const menu = (v.day_menus || {})[p.seat] || [];
  const role = state.roleById[p.role];
  if (!menu.length) return toast(`${p.seat}号 已死亡，无可用行动`);

  const body = h("div", {});
  const mask = h("div", { class: "modal-mask", onclick: (e) => { if (e.target === mask) mask.remove(); } },
    h("div", { class: "modal" },
      h("div", { class: "detail-head", style: "margin-bottom:10px" },
        roleAvatar(p.role, "md"),
        h("h3", { style: "margin-bottom:0" }, `${p.seat}号 ${p.name} — ${role?.name || ""}`)),
      h("div", { class: "setup-desc" }, role?.ability || ""),
      body));

  for (const act of menu) {
    const row = h("div", { class: "day-act" });
    const btn = h("button", { class: act.danger ? "danger" : "" }, act.label);
    let targetSel = null, roleSel = null;
    if (act.needs_target) {
      // 死者同样可选（如祺贵人检举一名已死玩家的身份）：只标注，不拦截
      targetSel = h("select", {},
        ...v.seats.filter((q) => q.seat !== p.seat)
          .map((q) => h("option", { value: q.seat },
            `${q.seat}号 ${q.name}${q.alive ? "" : " ☠已死"}`)));
      row.append(targetSel);
    }
    if (act.needs_role_guess) {
      // 宠妃/女皇是转化出来的现身份，检举必须能猜中它们（猜原角色一律算错）
      roleSel = h("select", {},
        ...state.meta.roles.filter((r) => !r.virtual || r.accusable)
          .map((r) => h("option", { value: r.id }, `${r.name}（${state.meta.team_names[r.team]}）`)));
      row.append(roleSel);
    }
    btn.addEventListener("click", async () => {
      if (act.danger && !confirm(`确认执行：${act.label}？`)) return;
      const params = {};
      if (targetSel) params.target = Number(targetSel.value);
      if (roleSel) params.role_guess = roleSel.value;
      try {
        const res = await api(`/api/games/${state.game.id}/day/action`, {
          method: "POST",
          body: JSON.stringify({ seat: p.seat, action: act.id, params }),
        });
        state.view = res.game;
        mask.remove();
        (res.events || []).forEach((x, i) => setTimeout(() => toast(x), i * 350));
        renderGameScreen();
      } catch (e) { toast(e.message); }
    });
    row.prepend(btn);
    body.append(row);
  }
  document.body.appendChild(mask);
}

function openNominateModal() {
  const v = state.view;
  const ds = v.day_state;
  const aliveSeats = v.seats.filter((q) => q.alive);
  const nomSel = h("select", {},
    ...aliveSeats.filter((q) => !ds.nominators.includes(q.seat))
      .map((q) => h("option", { value: q.seat }, `${q.seat}号 ${q.name}`)));
  const tgtSel = h("select", {},
    ...aliveSeats.filter((q) => !ds.nominees.includes(q.seat))
      .map((q) => h("option", { value: q.seat }, `${q.seat}号 ${q.name}`)));
  const mask = h("div", { class: "modal-mask", onclick: (e) => { if (e.target === mask) mask.remove(); } },
    h("div", { class: "modal" },
      h("h3", "发起提名"),
      h("div", { class: "day-act" }, h("span", {}, "提名者："), nomSel),
      h("div", { class: "day-act" }, h("span", {}, "被提名者："), tgtSel),
      h("p", { class: "setup-desc" },
        "提交后自动判定：一丈红暴毙 / 纯元皇后触发；通过后进入同时举手投票。"),
      h("div", { class: "actions" },
        h("button", {
          class: "primary",
          onclick: async () => {
            try {
              const res = await api(`/api/games/${state.game.id}/day/nominate`, {
                method: "POST",
                body: JSON.stringify({ nominator: Number(nomSel.value), nominee: Number(tgtSel.value) }),
              });
              state.view = res.game;
              mask.remove();
              (res.events || []).forEach((x, i) => setTimeout(() => toast(x), i * 350));
              if (res.proceed_to_vote) openVoteModal(Number(nomSel.value), Number(tgtSel.value));
              else renderGameScreen();
            } catch (e) { toast(e.message); }
          },
        }, "提交提名"))
    ));
  document.body.appendChild(mask);
}

function openVoteModal(nominator, nominee) {
  const v = state.view;
  const threshold = v.threshold;
  const sel = new Set();
  const ne = v.seats[nominee - 1];

  const counter = h("h3", {}, "");
  const chipsBox = h("div", { class: "chips" });

  function redraw() {
    counter.textContent = `已投 ${sel.size} 票 / 门槛 ${threshold} 票`;
    chipsBox.replaceChildren(...v.seats.map((q) => {
      const dead = !q.alive;
      const canVote = q.alive || q.ghost_vote;
      const on = sel.has(q.seat);
      return h("button", {
        class: `chip ${on ? "on" : ""} ${dead ? "ghost" : ""}`,
        ...(canVote ? {} : { disabled: "" }),
        title: dead ? (q.ghost_vote ? "死亡玩家：投票将消耗唯一的幽灵票" : "幽灵票已用完") : "",
        onclick: () => { on ? sel.delete(q.seat) : sel.add(q.seat); redraw(); },
      }, roleAvatar(q.role, "xs"), `${q.seat}号 ${q.name}${dead ? "（幽灵票）" : ""}`);
    }));
  }
  redraw();

  const mask = h("div", { class: "modal-mask" },
    h("div", { class: "modal" },
      h("h3", `对 ${nominee}号 ${ne.name} 的处决投票（全场同时举手）`),
      counter,
      h("p", { class: "setup-desc" },
        "点选所有举手的玩家。死亡玩家举手将当场消耗其幽灵票（即使投票未通过）。达标即立即处决并入夜。"),
      chipsBox,
      h("div", { class: "actions" },
        h("button", {
          class: "primary",
          onclick: async () => {
            try {
              const res = await api(`/api/games/${state.game.id}/day/vote`, {
                method: "POST",
                body: JSON.stringify({ votes: [...sel] }),
              });
              state.view = res.game;
              mask.remove();
              (res.events || []).forEach((x, i) => setTimeout(() => toast(x), i * 350));
              renderGameScreen();
            } catch (e) { toast(e.message); }
          },
        }, "结算投票"))
    ));
  document.body.appendChild(mask);
}

async function endDay() {
  if (!confirm("确认进入夜晚？")) return;
  try {
    state.view = await api(`/api/games/${state.game.id}/day/end`, { method: "POST" });
    renderGameScreen();
  } catch (e) { toast(e.message); }
}

// ---------- 终局 ----------

function renderEndScreen() {
  const v = state.view;
  const good = v.winner === "good";
  app.replaceChildren(
    h("h2", { class: "screen-title" }, "游戏结束"),
    h("div", { class: `end-banner ${good ? "good" : "evil"}` },
      h("div", { class: "end-title" }, good ? "☀ 善良阵营获胜" : "🌑 邪恶阵营获胜"),
      h("div", {}, v.win_reason || "")),
    h("div", { class: "side-panel wide center-panel" },
      h("h3", "身份公开"),
      h("div", { class: "chips" },
        ...v.seats.map((p) => {
          const r = state.roleById[p.role];
          return h("span", { class: `chip ghost team-${r?.team}` },
            roleAvatar(p.role, "xs"),
            `${p.seat}号 ${p.name}：${r?.name}${p.alive ? "" : " ☠"}`);
        })),
      logPanel(v, 50)),
    h("div", { class: "actions" },
      h("button", { onclick: renderHomeScreen }, "⌂ 对局管理"),
      h("button", { class: "primary", onclick: renderCountScreen }, "开新对局"))
  );
}

// ---------- 启动 ----------

(async function init() {
  try {
    state.meta = await api("/api/meta");
    for (const r of state.meta.roles) state.roleById[r.id] = r;
    renderHomeScreen();
  } catch (e) {
    app.textContent = `初始化失败：${e.message}`;
  }
})();
