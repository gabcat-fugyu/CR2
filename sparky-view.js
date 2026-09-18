/* スパーキー使いランキングの表示部品。
 *
 * 仲間内アプリ(index.html)と、誰でも見られる単体ページ(sparky.html)の
 * 両方から読み込む。同じ画面を2箇所に書くと片方だけ直し忘れるので、
 * 見た目も処理もここにまとめてある。
 *
 * 使い方:
 *   SparkyView.mount(要素, { repo: "owner/name", branch: "main" });
 *   SparkyView.reload();   // 更新ボタンから
 */
window.SparkyView = (function () {
  // カードのレベル。APIの level はレアリティ内の相対値なので、
  // maxLevel から実際の表示レベルを逆算する(カンストは全レアリティ16)
  const MAX_LEVEL = 16;
  const cardLevel = (c) =>
    typeof c.level !== "number" ? null
      : Math.min(MAX_LEVEL, c.level + (MAX_LEVEL - (typeof c.maxLevel === "number" ? c.maxLevel : MAX_LEVEL)));

  // デッキ左3枠の役割。進化が乗るのは1枠目とワイルドの3枠目
  const SLOT_ROLES = ["進化", "ヒーロー", "ワイルド"];
  const EVO_SLOTS = [0, 2];

  const DROP = `<svg viewBox="0 0 10 13" aria-hidden="true"><path d="M5 0C5 0 0 5.6 0 8.6A5 5 0 0 0 10 8.6C10 5.6 5 0 5 0z"/></svg>`;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function ago(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const m = Math.floor((Date.now() - d.getTime()) / 60000);
    if (m < 1) return "たった今";
    if (m < 60) return m + "分前";
    const h = Math.floor(m / 60);
    return h < 24 ? h + "時間前" : Math.floor(h / 24) + "日前";
  }

  // ---- 見た目 ----
  // 置かれるページの配色を使う。単体で読まれた時のために既定値も持たせる。
  const CSS = `
  .sv-rail { display: flex; gap: 7px; overflow-x: auto; margin-bottom: 12px; padding: 2px 0 5px; }
  .sv-rail::-webkit-scrollbar { display: none; }
  .sv-btn {
    flex: 0 0 auto; font-family: inherit; font-weight: 800; font-size: 12px;
    color: var(--ink, #fff); cursor: pointer; white-space: nowrap;
    background: linear-gradient(180deg, var(--arena-lite, #1c4699), var(--arena-mid, #12306e));
    border: 2px solid var(--panel-edge, #3a6fd8); border-bottom-width: 4px;
    border-radius: 11px; padding: 7px 14px;
    text-shadow: 0 1.5px 0 rgba(4,12,32,0.6);
  }
  .sv-btn:active { transform: translateY(2px); border-bottom-width: 2px; }
  .sv-btn.on {
    background: linear-gradient(180deg, var(--gold-lite, #ffe89a), var(--gold, #ffd23f) 50%, #e6a400);
    border-color: var(--gold-deep, #b8790a); color: #3d2600;
    text-shadow: 0 1px 0 rgba(255,255,255,0.5);
  }
  .sv-btn:focus-visible { outline: 3px solid var(--gold-lite, #ffe89a); outline-offset: 2px; }

  .sv-lede {
    background: rgba(6,16,40,0.4); border: 1px solid rgba(120,170,240,0.25);
    border-radius: 11px; padding: 9px 11px; margin-bottom: 12px;
    font-size: 10.5px; color: var(--ink-dim, #a9c0ea); line-height: 1.7;
  }
  .sv-lede b { color: var(--gold-lite, #ffe89a); }

  .sv-row {
    border-radius: 13px; margin-bottom: 8px; overflow: hidden;
    border: 2px solid rgba(96,150,235,0.3); border-bottom-width: 3px;
    border-left: 5px solid var(--elixir, #d24ce0);
    background: linear-gradient(180deg, rgba(26,62,135,0.85), rgba(11,30,72,0.85));
  }
  .sv-row.g1 { border-color: var(--gold, #ffd23f); border-left-color: var(--gold, #ffd23f);
               box-shadow: 0 0 22px -6px rgba(255,210,63,0.5); }
  .sv-row.g2 { border-color: #cdd8ec; border-left-color: #cdd8ec; }
  .sv-row.g3 { border-color: #d08a4a; border-left-color: #d08a4a; }

  .sv-face {
    display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 9px 11px; background: none; border: 0; color: inherit;
    font-family: inherit; cursor: pointer; text-align: left;
  }
  .sv-medal {
    width: 30px; height: 30px; flex-shrink: 0; border-radius: 50%;
    display: grid; place-items: center; font-size: 14px; font-weight: 800;
    background: linear-gradient(180deg, #2a5cb8, #16336e);
    border: 2px solid rgba(120,170,240,0.45); color: var(--ink-dim, #a9c0ea);
  }
  .g1 .sv-medal { background: linear-gradient(180deg, var(--gold-lite, #ffe89a), #e0a300); border-color: #8a5c00; color: #4a2f00; }
  .g2 .sv-medal { background: linear-gradient(180deg, #ffffff, #b9c6dc); border-color: #8593ad; color: #33405a; }
  .g3 .sv-medal { background: linear-gradient(180deg, #f0b183, #b96a2c); border-color: #7d4413; color: #40200a; }

  .sv-body { flex: 1; min-width: 0; }
  .sv-name {
    display: block; font-weight: 800; font-size: 14px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; text-shadow: 0 1.5px 0 rgba(4,12,32,0.55);
  }
  .sv-sub { display: block; font-size: 10.5px; color: var(--ink-dim, #a9c0ea); margin-top: 1px; }
  .sv-bar {
    display: block; height: 3px; border-radius: 2px; margin-top: 4px;
    background: rgba(255,255,255,0.12); overflow: hidden;
  }
  .sv-bar span { display: block; height: 100%; background: var(--elixir, #d24ce0); }
  .sv-value {
    flex-shrink: 0; text-align: right; font-size: 20px; font-weight: 800;
    color: var(--gold, #ffd23f); text-shadow: 0 2px 0 rgba(4,12,32,0.55);
  }
  .sv-chev { flex-shrink: 0; color: var(--ink-dim, #a9c0ea); font-size: 11px; transition: transform 0.18s; }
  .sv-row.open .sv-chev { transform: rotate(180deg); }

  .sv-decks { padding: 2px 11px 12px; }
  .sv-deck-head { display: flex; align-items: center; justify-content: space-between; font-size: 10.5px; margin-bottom: 6px; }
  .sv-who { font-weight: 800; color: var(--elixir, #d24ce0); }
  .sv-avg { display: inline-flex; align-items: center; gap: 3px; color: var(--ink-dim, #a9c0ea); }
  .sv-avg svg { width: 10px; height: 13px; fill: var(--elixir, #d24ce0); }
  .sv-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; }
  .sv-slot { position: relative; border-radius: 7px; overflow: hidden; background: rgba(0,0,0,0.22); }
  .sv-slot img { display: block; width: 100%; aspect-ratio: 302/363; object-fit: cover; }
  .sv-lv {
    position: absolute; left: 0; right: 0; bottom: 0;
    font-size: 8.5px; font-weight: 800; text-align: center; padding: 1px 0;
    background: rgba(6,16,40,0.82); color: var(--ink-dim, #a9c0ea);
  }
  .sv-lv.max { background: linear-gradient(180deg, var(--gold, #ffd23f), #d99a00); color: #3d2600; }
  .sv-slot.r0 { box-shadow: 0 0 0 2px rgba(176,76,255,0.55) inset; }
  .sv-slot.r1 { box-shadow: 0 0 0 2px rgba(255,168,56,0.7) inset; }
  .sv-slot.r2 { box-shadow: 0 0 0 2px rgba(216,122,190,0.6) inset; }
  .sv-slot.r0.evo, .sv-slot.r2.evo { box-shadow: 0 0 0 2px #b04cff inset; }
  .sv-role {
    position: absolute; top: 2px; right: 2px;
    font-size: 7.5px; font-weight: 800; line-height: 1;
    padding: 2px 3px; border-radius: 4px;
    background: rgba(6,16,40,0.82); color: var(--ink-dim, #a9c0ea);
  }
  .sv-slot.r0 .sv-role { color: #d79bff; }
  .sv-slot.r1 .sv-role { color: #ffc27a; }
  .sv-slot.r2 .sv-role { color: #f0a8d8; }
  .sv-support { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
  .sv-support-cap { font-size: 9.5px; color: var(--ink-dim, #a9c0ea); flex-shrink: 0; }
  .sv-support-grid { display: flex; gap: 5px; }
  .sv-support-grid .sv-slot { width: 46px; }
  .sv-empty { font-size: 10.5px; color: var(--ink-dim, #a9c0ea); padding: 6px 0; }

  .sv-more {
    width: 100%; margin-top: 4px; padding: 11px;
    font-family: inherit; font-weight: 700; font-size: 12px;
    color: var(--ink-dim, #a9c0ea);
    background: rgba(20,48,110,0.7); border: 2px dashed rgba(96,150,235,0.35);
    border-radius: 12px; cursor: pointer;
  }
  .sv-more:active { transform: translateY(1px); }

  .sv-notice { text-align: center; padding: 46px 18px; color: var(--ink-dim, #a9c0ea); font-size: 13px; line-height: 1.9; }
  .sv-notice b { color: var(--gold-lite, #ffe89a); }
  .sv-updated { font-size: 10.5px; color: var(--ink-dim, #a9c0ea); margin-top: 14px; padding-left: 3px; }
  `;

  function injectCss() {
    if (document.getElementById("sparky-view-css")) return;
    const el = document.createElement("style");
    el.id = "sparky-view-css";
    el.textContent = CSS;
    document.head.appendChild(el);
  }

  // ---- 状態 ----
  let host = null, conf = null;
  let data = null, failed = false, region = "jp", busy = false;
  const opened = new Set();

  // 一度に全員ぶん描くと重いので少しずつ出す
  const PAGE = 30;
  let shown = PAGE;

  /** 置かれている場所からリポジトリを推測する。
   *  例) https://name.github.io/REPO/ → name/REPO
   *  自分のリポジトリのデータを読むだけなら、これで設定いらずになる。 */
  function guessRepo() {
    const owner = (location.hostname.split(".")[0] || "").trim();
    const first = location.pathname.split("/").filter(Boolean)[0];
    return owner && first ? `${owner}/${first}` : null;
  }

  async function loadJson(path) {
    // raw配信はキャッシュが効くので、まずキャッシュを挟まないAPI経由を試す
    const api = `https://api.github.com/repos/${conf.repo}/contents/data`;
    const raw = `https://raw.githubusercontent.com/${conf.repo}/${conf.branch}/data`;
    try {
      const res = await fetch(`${api}/${path}?ref=${conf.branch}&t=${Date.now()}`, {
        cache: "no-store", headers: { Accept: "application/vnd.github.raw" },
      });
      if (res.ok) return await res.json();
    } catch { /* APIが使えなければ下へ */ }
    const res = await fetch(`${raw}/${path}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    return await res.json();
  }

  function cardSlot(c, i) {
    const evo = EVO_SLOTS.includes(i) && c.evolutionLevel != null;
    const url = (evo && c.iconUrls?.evolutionMedium) || c.iconUrls?.medium || "";
    const lv = cardLevel(c);
    const maxed = lv === MAX_LEVEL;
    const role = SLOT_ROLES[i];
    return `<div class="sv-slot${role ? ` r${i}` : ""}${evo ? " evo" : ""}">
      ${url ? `<img src="${esc(url)}" alt="${esc(c.name || "")}" loading="lazy">` : ""}
      ${role ? `<span class="sv-role">${esc(role)}</span>` : ""}
      ${lv != null ? `<span class="sv-lv${maxed ? " max" : ""}">${maxed ? "MAX" : "Lv" + lv}</span>` : ""}
    </div>`;
  }

  function deckHtml(p) {
    const cards = p.deck || [];
    const support = p.supportCards || [];
    if (!cards.length && !support.length) return `<div class="sv-empty">デッキ情報なし</div>`;
    const costs = cards.map(c => c.elixirCost).filter(v => typeof v === "number");
    const avg = costs.length ? (costs.reduce((a, b) => a + b, 0) / costs.length).toFixed(1) : null;
    return `
      <div class="sv-deck-head">
        <span class="sv-who">${esc(p.name || "")} のデッキ</span>
        ${avg ? `<span class="sv-avg">${DROP} 平均 ${avg}</span>` : ""}
      </div>
      ${cards.length ? `<div class="sv-grid">${cards.map(cardSlot).join("")}</div>` : ""}
      ${support.length ? `<div class="sv-support">
        <span class="sv-support-cap">タワー</span>
        <div class="sv-support-grid">${support.map(c => cardSlot(c, -1)).join("")}</div>
      </div>` : ""}`;
  }

  function render() {
    if (!host) return;

    if (busy && !data) { host.innerHTML = `<div class="sv-notice">読み込み中…</div>`; return; }
    if (failed) {
      host.innerHTML = `<div class="sv-notice">
        まだ集計されていません。<br>しばらくしてから開き直してください。</div>`;
      return;
    }
    if (!data) { host.innerHTML = `<div class="sv-notice">読み込み中…</div>`; return; }

    const regions = data.regions || [];
    const r = regions.find(x => x.key === region) || regions[0];
    if (!r) { host.innerHTML = `<div class="sv-notice">集計結果がありません。</div>`; return; }

    const tabs = regions.length > 1
      ? `<div class="sv-rail">${regions.map(x =>
          `<button class="sv-btn${x.key === r.key ? " on" : ""}" data-sv-region="${esc(x.key)}">${esc(x.label)}</button>`
        ).join("")}</div>` : "";

    const list = r.players || [];
    const rows = list.slice(0, shown).map(p => {
      const key = `${r.key}:${p.tag}`;
      const isOpen = opened.has(key);
      const cls = p.rank === 1 ? " g1" : p.rank === 2 ? " g2" : p.rank === 3 ? " g3" : "";
      return `<div class="sv-row${cls}${isOpen ? " open" : ""}">
        <button class="sv-face" data-sv-key="${esc(key)}" aria-expanded="${isOpen}">
          <span class="sv-medal">${p.rank}</span>
          <span class="sv-body">
            <span class="sv-name">${esc(p.name)}</span>
            <span class="sv-sub">使用率 ${p.usageRate}%${
              p.winRate != null ? ` · 勝率 ${p.winRate}%` : ""}${p.clan ? ` · ${esc(p.clan)}` : ""}</span>
            <span class="sv-bar"><span style="width:${Math.min(100, p.usageRate)}%"></span></span>
          </span>
          <span class="sv-value">${p.rating != null ? esc(p.rating) : "—"}</span>
          <span class="sv-chev">▼</span>
        </button>
        ${isOpen ? `<div class="sv-decks">${deckHtml(p)}</div>` : ""}
      </div>`;
    }).join("");

    host.innerHTML = `
      ${tabs}
      <div class="sv-lede">
        ${esc(r.label)}の上位<b>${esc(r.scanned ?? "?")}</b>人のうち、直近の対戦の
        <b>${Math.round((data.threshold ?? 0.5) * 100)}%以上</b>で
        ${esc(data.card || "スパーキー")}を使っていた<b>${list.length}</b>人。レートの高い順。
        <br>タップでデッキが見られます。
      </div>
      ${list.length ? rows : `<div class="sv-notice">該当する人がいませんでした。</div>`}
      ${shown < list.length
        ? `<button class="sv-more" data-sv-more="1">さらに${Math.min(PAGE, list.length - shown)}人</button>`
        : ""}
      ${data.updatedAt ? `<div class="sv-updated">最終更新 ${esc(ago(data.updatedAt))}</div>` : ""}
    `;

    host.querySelectorAll("[data-sv-region]").forEach(b => {
      b.onclick = () => { region = b.dataset.svRegion; opened.clear(); shown = PAGE; render(); };
    });
    host.querySelectorAll("[data-sv-more]").forEach(b => {
      b.onclick = () => { shown += PAGE; render(); };
    });
    host.querySelectorAll("[data-sv-key]").forEach(b => {
      b.onclick = () => {
        const k = b.dataset.svKey;
        opened.has(k) ? opened.delete(k) : opened.add(k);
        render();
      };
    });
  }

  async function load() {
    busy = true;
    render();
    try {
      const raw = await loadJson("sparky.json");
      // 古い形式(地域分けなし)でも読めるようにしておく
      data = raw.regions ? raw
        : { ...raw, regions: [{ key: "jp", label: "日本", scanned: raw.scanned, players: raw.players || [] }] };
      failed = false;
    } catch {
      failed = true;
    }
    busy = false;
    render();
  }

  return {
    /** 要素にランキングを描く。
     *  repo を省くと、そのページが置かれているリポジトリのデータを読む。 */
    mount(el, options) {
      injectCss();
      host = el;
      const opts = options || {};
      const repo = opts.repo || guessRepo();
      if (!repo) { host.innerHTML = `<div class="sv-notice">読み込み先が分かりません。</div>`; return; }
      conf = { repo, branch: opts.branch || "main" };
      if (data || failed) render();
      else load();
    },
    /** 更新ボタンから。取り直して描き直す。 */
    reload() {
      if (!host) return Promise.resolve();
      data = null; failed = false; opened.clear(); shown = PAGE;
      return load();
    },
    /** 画面を離れる時に呼ぶ。読み込んだ内容は残す。 */
    unmount() { host = null; },
  };
})();
