"""クラロワの対戦ログを定期取得して JSON に貯めるスクリプト。

GitHub Actions から定期実行される前提。公式APIは直近25試合しか返さないので、
実行のたびに「まだ持っていない試合」だけを追記していく。

追跡する人は players.json で管理する。トークンは自分の1つで足りる
(プレイヤー情報は公開データなので、相手のトークンは不要)。

サイト側でランキングを軽く描けるよう、期間別の集計(今日/3日/7日の
試合数と勝率)はここで計算して index.json に入れておく。

必要な環境変数:
    CR_API_TOKEN  developer.clashroyale.com で発行したトークン
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 公式APIはトークンにIP制限がかかる。GitHub ActionsのIPは毎回変わるので、
# 固定IPを持つRoyaleAPIの中継プロキシを経由する。
# (トークンの許可IP欄に 45.79.218.79 を登録しておくこと)
API_BASE = "https://proxy.royaleapi.dev/v1"

# 「今日」の判定は日本時間で行う
JST = timezone(timedelta(hours=9))

# ---- データの保持期間 ----
# 放っておくと際限なく増えるので、古い分は間引く。
# 環境変数で上書きできるようにしておく。
KEEP_BATTLE_DAYS = int(os.environ.get("KEEP_BATTLE_DAYS", "180"))   # 対戦履歴を残す日数
MAX_BATTLES = int(os.environ.get("MAX_BATTLES", "3000"))            # 1人あたりの上限試合数
# レート推移はシーズンが変わるたびに捨てるので、シーズン内(最長5週ほど)は
# 全部残しておける。念のため上限だけ持たせる。
FULL_TROPHY_DAYS = int(os.environ.get("FULL_TROPHY_DAYS", "45"))    # レート推移を全点残す日数
KEEP_TROPHY_DAYS = int(os.environ.get("KEEP_TROPHY_DAYS", "60"))    # それ以前は1日1点にして残す日数

# ---- シーズンの区切り ----
# クラロワのシーズンは「第1月曜の17時ごろ」から次の第1月曜まで。
# ここが変わるとレートがリセットされ、全員マスターIから始まる。
SEASON_RESET_HOUR = int(os.environ.get("SEASON_RESET_HOUR", "17"))
RESET_LEAGUE = 1  # リセット後のリーグ = マスターI

ROOT = Path(__file__).parent
PLAYERS_FILE = ROOT / "players.json"
DATA_DIR = ROOT / "data"
INDEX_FILE = DATA_DIR / "index.json"


def inspect_token(token: str):
    """トークンの見た目をチェックして診断情報を表示する(中身は漏らさない)。"""
    print("--- トークン診断 ---")
    print(f"  長さ: {len(token)} 文字")
    if len(token) < 20:
        print("  警告: 短すぎます。コピーが途中で切れている可能性大")
    if len(token) >= 8:
        print(f"  先頭4文字: {token[:4]}... / 末尾4文字: ...{token[-4:]}")

    bad_chars = [c for c in token if c in (" ", "\n", "\t", "\r")]
    if bad_chars:
        print(f"  警告: トークン内部に空白/改行らしき文字が {len(bad_chars)} 個あります")
    else:
        print("  内部の空白/改行: なし(OK)")

    if re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        print("  形式: JWTらしき3分割構造(OK)")
    else:
        print("  警告: 想定したJWT形式(xxx.yyy.zzz)に見えません。貼り付けミスの可能性")
    print("--------------------")


def api_get(path: str, token: str):
    """APIを叩いてJSONを返す。取得できなければ None。"""
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            # RoyaleAPIの中継サーバーはCloudflareの背後にあり、Pythonの
            # デフォルトUser-Agent (Python-urllib/3.x) だと bot 扱いで
            # 弾かれる (error_1010 / browser_signature_banned)。
            # ブラウザっぽい値にして回避する。
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass

        if e.code == 403:
            print("  --- 403の詳細 ---")
            print(f"  リクエストURL: {API_BASE}{path}")
            print(f"  APIが返した中身: {body[:500]}")
            print("  ------------------")
            raise SystemExit(
                "403: トークン/IP設定、またはCloudflareのbot判定"
                "(browser_signature_banned等)が原因の可能性があります"
            )
        if e.code == 404:
            print(f"  スキップ: タグが見つかりません ({path})", file=sys.stderr)
            return None
        if e.code == 429:
            print("  スキップ: レート制限に達しました", file=sys.stderr)
            return None
        print(f"  スキップ: APIエラー {e.code} ({path}) / 返答: {body[:200]}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"  スキップ: 通信エラー {e.reason} ({path})", file=sys.stderr)
        return None


def load_json(path: Path, default):
    """既存のJSONを読む。無ければ default を返す。"""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  警告: {path.name} が壊れているため初期化します ({e})", file=sys.stderr)
        return default


def save_json(path: Path, data):
    """JSONを整形して書き出す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def battle_key(battle: dict) -> str:
    """試合を一意に識別するキー。開始時刻＋自分のタグで十分区別できる。"""
    team = battle.get("team") or [{}]
    return f"{battle.get('battleTime', '')}_{team[0].get('tag', '')}"


def parse_battle_time(raw: str):
    """20260806T083000.000Z 形式を datetime に変換する。"""
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def battle_result(battle: dict, tag: str):
    """その試合が勝ちかどうかを返す。判定できなければ None。"""
    team = battle.get("team") or []
    opponent = battle.get("opponent") or []
    if not team or not opponent:
        return None

    me = next(
        (p for p in team if str(p.get("tag", "")).lstrip("#").upper() == tag), team[0]
    )
    my_crowns = me.get("crowns")
    opp_crowns = opponent[0].get("crowns")
    if my_crowns is None or opp_crowns is None:
        return None
    if my_crowns == opp_crowns:
        return None  # 引き分けは勝率の母数から外す
    return my_crowns > opp_crowns


def season_start(year: int, month: int) -> datetime:
    """その月のシーズン開始時刻(第1月曜の17時 日本時間)を返す。"""
    d = datetime(year, month, 1, tzinfo=JST)
    while d.weekday() != 0:  # 0 = 月曜
        d += timedelta(days=1)
    return d.replace(hour=SEASON_RESET_HOUR)


def season_key(now: datetime = None) -> str:
    """今がどのシーズンかを "YYYY-MM" で返す。"""
    now = (now or datetime.now(timezone.utc)).astimezone(JST)
    if now < season_start(now.year, now.month):
        # まだ今月のシーズンが始まっていない = 先月のシーズンの続き
        y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        return f"{y:04d}-{m:02d}"
    return f"{now.year:04d}-{now.month:02d}"


def looks_stale(pol: dict, player: dict, pending: dict) -> bool:
    """シーズンが変わったのに、APIがまだ前シーズンの値を返しているか。

    リセット直前の値と完全に一致していれば、まだ更新されていないとみなす。
    1試合でもすれば battleCount が動くので、そこで解除される。
    """
    if not pending:
        return False
    return (
        pol.get("leagueNumber") == pending.get("leagueNumber")
        and pol.get("trophies") == pending.get("rating")
        and player.get("battleCount") == pending.get("battleCount")
    )


def period_stats(history, tag: str):
    """今日/3日/7日それぞれの試合数と勝率を集計する。"""
    now = datetime.now(timezone.utc)
    today_start = (
        now.astimezone(JST)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )

    windows = {
        "today": today_start,
        "d3": now - timedelta(days=3),
        "d7": now - timedelta(days=7),
    }

    stats = {}
    for name, since in windows.items():
        total = 0
        wins = 0
        decided = 0
        for b in history:
            when = parse_battle_time(b.get("battleTime", ""))
            if when is None or when < since:
                continue
            total += 1
            result = battle_result(b, tag)
            if result is None:
                continue
            decided += 1
            if result:
                wins += 1
        stats[name] = {
            "battles": total,
            "wins": wins,
            # 引き分けを除いた勝率。判定できる試合が無ければ None
            "winRate": round(wins / decided * 100, 1) if decided else None,
        }
    return stats


def prune_battles(battles, now):
    """古すぎる試合と、上限を超えた分を落とす。battlesは新しい順。"""
    cutoff = now - timedelta(days=KEEP_BATTLE_DAYS)
    kept = []
    for b in battles:
        when = parse_battle_time(b.get("battleTime", ""))
        # 時刻が読めないものは判断できないので残す
        if when is None or when >= cutoff:
            kept.append(b)
    return kept[:MAX_BATTLES]


def compact_trophies(points, now):
    """レート推移を間引く。

    直近 FULL_TROPHY_DAYS は全部残す。それより古いものは1日1点だけ
    (その日の最後の記録) に減らし、KEEP_TROPHY_DAYS より古いものは捨てる。
    """
    full_from = now - timedelta(days=FULL_TROPHY_DAYS)
    keep_from = now - timedelta(days=KEEP_TROPHY_DAYS)

    recent = []
    per_day = {}  # 日付 -> その日の最後の1点

    for p in points:
        try:
            when = datetime.fromisoformat(p["time"])
        except (KeyError, ValueError, TypeError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)

        if when >= full_from:
            recent.append((when, p))
        elif when >= keep_from:
            day = when.astimezone(JST).date()
            prev = per_day.get(day)
            if prev is None or when >= prev[0]:
                per_day[day] = (when, p)

    merged = list(per_day.values()) + recent
    merged.sort(key=lambda x: x[0])
    return [p for _, p in merged]


def same_snapshot(a, b):
    """レート推移の記録が前回と同じ中身かどうか。"""
    if not a or not b:
        return False
    keys = ("trophies", "rating", "leagueNumber", "battleCount", "wins", "losses")
    return all(a.get(k) == b.get(k) for k in keys)


def probe_leaderboards(token: str):
    """【一時的な診断】どんなリーダーボードがあるか調べて表示する。

    2v2リーグの順位が取れるか確認したい。公式ドキュメントに載っていなくても
    実際には返ってくることがあるので、叩いてみる。
    """
    print("  ===== リーダーボード診断 =====")
    data = api_get("/leaderboards", token)
    if data is None:
        print("    /leaderboards が取得できませんでした(未対応の可能性)")
        print("  ==============================")
        return

    items = data.get("items") or []
    print(f"    {len(items)}件")
    for item in items:
        name = str(item.get("name", ""))
        mark = " ★2v2かも" if any(h in name.lower() for h in ("2v2", "duo", "2 v 2")) else ""
        print(f"    id={item.get('id')}  {name}{mark}")
    print("  ==============================")


def probe_fields(player: dict, token: str):
    """【一時的な診断】2v2リーグのデータがAPIのどこにあるか調べる。

    1人目のときだけ呼ばれる。分かったらこの関数と呼び出しは消してよい。
    """
    print("  ===== 2v2の調査(1人目のみ) =====")

    # --- プレイヤー情報に2v2用のフィールドがあるか ---
    print("  [1] プレイヤー情報のキー:")
    for k in sorted(player.keys()):
        v = player[k]
        kind = type(v).__name__
        if isinstance(v, list):
            kind = f"list[{len(v)}]"
        elif isinstance(v, dict):
            kind = f"dict{sorted(v.keys())}"
        print(f"      {k} : {kind}")

    hints = ("duo", "2v2", "two", "rating", "league", "season", "rank", "elo")
    print("  [2] それらしいキーの中身:")
    found = False
    for k, v in player.items():
        if k in ("badges", "cards", "supportCards", "currentDeck", "achievements"):
            continue  # 数が多いので飛ばす
        if any(h in k.lower() for h in hints):
            print(f"      {k} = {json.dumps(v, ensure_ascii=False)[:400]}")
            found = True
    if not found:
        print("      (見当たりませんでした)")

    # --- リーダーボードの一覧に2v2があるか ---
    print("  [3] リーダーボード一覧:")
    boards = api_get("/leaderboards", token)
    items = (boards or {}).get("items")
    if not items:
        print("      取得できませんでした(未対応か、権限がない可能性)")
    else:
        print(f"      全{len(items)}件")
        for b in items:
            name = str(b.get("name", ""))
            mark = "  ★" if any(h in name.lower() for h in ("2v2", "duo", "ダブル")) else "    "
            print(f"    {mark}id={b.get('id')} : {name}")

    print("  ================================")


def collect_player(tag: str, label, groups, token: str, probe: bool = False):
    """1人分を取得して保存する。結果のサマリを返す。"""
    encoded = f"%23{tag}"
    player = api_get(f"/players/{encoded}", token)
    if player is None:
        return None

    if probe:
        probe_fields(player, token)

    battles = api_get(f"/players/{encoded}/battlelog", token)
    if not isinstance(battles, list):
        battles = []

    player_dir = DATA_DIR / tag

    # --- 対戦履歴のマージ ---
    history = load_json(player_dir / "history.json", [])
    known = {battle_key(b) for b in history}
    additions = [b for b in battles if battle_key(b) not in known]

    merged = additions + history
    merged.sort(key=lambda b: b.get("battleTime", ""), reverse=True)

    now = datetime.now(timezone.utc)

    before = len(merged)
    merged = prune_battles(merged, now)
    dropped = before - len(merged)

    # --- パス・オブ・レジェンド (天界) の情報 ---
    # シーズン中はここにレートとリーグ番号が入る。オフシーズンだと空のことがある。
    pol = player.get("currentPathOfLegendSeasonResult") or {}
    best_pol = player.get("bestPathOfLegendSeasonResult") or {}
    last_pol = player.get("lastPathOfLegendSeasonResult") or {}

    # --- シーズンの切り替わり判定 ---
    prev = load_json(player_dir / "player.json", {}) or {}
    current_season = season_key(now)
    changed = bool(prev.get("season")) and prev.get("season") != current_season

    if changed:
        # 新シーズン。APIが前シーズンの値を返し続けている間は
        # マスターI・レートなしとして扱う(実際のゲームもリセットされている)
        pending = {
            "leagueNumber": prev.get("rawLeagueNumber"),
            "rating": prev.get("rawRating"),
            "battleCount": prev.get("battleCount"),
        }
        print(f"    シーズン更新: {prev.get('season')} → {current_season} (レート推移をリセット)")
    else:
        pending = prev.get("resetPending")

    stale = looks_stale(pol, player, pending)
    if not stale:
        pending = None

    # 表示に使う値。まだ前シーズンの値なら マスターI に見せる
    show_rating = None if stale else pol.get("trophies")
    show_league = RESET_LEAGUE if stale else pol.get("leagueNumber")

    # --- トロフィー/レート推移の記録 ---
    # 10分おきに走るので、中身が前回と同じなら記録しない(無駄に増やさない)
    # シーズンが変わったら推移は捨てて、新シーズン分だけ貯め直す
    trophies = [] if changed else load_json(player_dir / "trophies.json", [])
    snapshot = {
        "time": now.isoformat(timespec="seconds"),
        "season": current_season,
        "trophies": player.get("trophies"),
        "rating": show_rating,
        "leagueNumber": show_league,
        "battleCount": player.get("battleCount"),
        "wins": player.get("wins"),
        "losses": player.get("losses"),
    }
    if not same_snapshot(trophies[-1] if trophies else None, snapshot):
        trophies.append(snapshot)
    trophies = compact_trophies(trophies, now)

    summary = {
        "updatedAt": now.isoformat(timespec="seconds"),
        "tag": player.get("tag"),
        "name": player.get("name"),
        "label": label or player.get("name"),
        "groups": groups or [],
        # 天界(パス・オブ・レジェンド)。表示用の値
        "rating": show_rating,
        "leagueNumber": show_league,
        "polRank": None if stale else pol.get("rank"),
        # APIが返した生の値と、シーズンの状態
        "rawRating": pol.get("trophies"),
        "rawLeagueNumber": pol.get("leagueNumber"),
        "season": current_season,
        "resetPending": pending,
        "bestRating": best_pol.get("trophies"),
        "bestLeagueNumber": best_pol.get("leagueNumber"),
        "lastRating": last_pol.get("trophies"),
        # 通常トロフィー
        "trophies": player.get("trophies"),
        "bestTrophies": player.get("bestTrophies"),
        "arena": (player.get("arena") or {}).get("name"),
        # 通算
        "battleCount": player.get("battleCount"),
        "wins": player.get("wins"),
        "losses": player.get("losses"),
        "clan": (player.get("clan") or {}).get("name"),
        "storedBattles": len(merged),
        # 期間別集計 (ランキング用)
        "periods": period_stats(merged, tag),
    }

    save_json(player_dir / "history.json", merged)
    save_json(player_dir / "trophies.json", trophies)
    save_json(player_dir / "player.json", summary)

    rating_text = summary["rating"] if summary["rating"] is not None else "レート無し"
    trimmed = f" / 古い試合を{dropped}件整理" if dropped else ""
    print(
        f"  {summary['label']}: {rating_text} / 新規 {len(additions)} / 累計 {len(merged)} 試合{trimmed}"
    )
    return summary


def read_players():
    """players.json から追跡対象を読む。[(タグ, 表示名, グループ一覧), ...] を返す。

    グループはランキングを分ける単位(例: チャンネルメンバーの所属)。
    掛け持ちできるよう、書き方は次のどちらでもよい。
        "group": "スパ研"
        "group": ["スパ研", "SMAP"]
    キー名は group でも groups でも受け付ける。
    """
    raw = load_json(PLAYERS_FILE, None)
    if raw is None:
        raise SystemExit("players.json が見つからないか、内容が壊れています")

    entries = []
    for item in raw.get("players", []):
        tag = str(item.get("tag", "")).strip().upper().lstrip("#")
        if not tag:
            continue

        value = item.get("groups", item.get("group"))
        if isinstance(value, str):
            value = [value]
        elif not isinstance(value, list):
            value = []
        groups = []
        for g in value:
            g = str(g).strip()
            if g and g not in groups:
                groups.append(g)

        entries.append((tag, item.get("name") or None, groups))
    return entries


def main():
    token = os.environ.get("CR_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("CR_API_TOKEN を設定してください")

    inspect_token(token)

    entries = read_players()
    if not entries:
        raise SystemExit("players.json に1人も登録されていません")

    print(f"{len(entries)}人分を取得します")

    summaries = []
    for i, (tag, label, groups) in enumerate(entries):
        # 1人目だけフィールドの中身を覗く(2v2リーグの調査用。不要になったら消す)
        result = collect_player(tag, label, groups, token, probe=(i == 0))
        if result:
            summaries.append(result)
        if i < len(entries) - 1:
            time.sleep(1)

    # グループの一覧。players.json に書いた順を保つ
    all_groups = []
    for _, _, groups in entries:
        for g in groups:
            if g not in all_groups:
                all_groups.append(g)

    save_json(
        INDEX_FILE,
        {
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "groups": all_groups,
            "players": summaries,
        },
    )

    if not summaries:
        raise SystemExit("1人も取得できませんでした。players.json を確認してください")

    print(f"完了: {len(summaries)}/{len(entries)}人")


if __name__ == "__main__":
    main()
