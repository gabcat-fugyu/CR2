"""日本のリーダーボードから「スパーキー使い」を抜き出してランキングにする。

やっていること:
  1. /locations から日本のロケーションIDを探す
  2. 日本の上位プレイヤー(既定1000人)を取得
  3. 1人ずつ対戦ログを見て、直近の試合のうち何割でスパーキーを使ったか数える
  4. 半分以上で使っていた人だけを残し、レートの高い順に並べて保存

collect.py の通信処理(Cloudflare対策のUser-Agentなど)を使い回すので、
同じフォルダに collect.py がある前提。

必要な環境変数:
    CR_API_TOKEN  developer.clashroyale.com で発行したトークン
"""

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from collect import api_get, battle_result, load_json, save_json

# 対象のカード。名前で判定する
TARGET_CARD = os.environ.get("TARGET_CARD", "Sparky")

# 何人まで調べるか
SCAN_LIMIT = int(os.environ.get("SPARKY_SCAN", "1000"))

# 「使い」と認める下限。0.5 なら直近の半分以上で使っていれば該当
THRESHOLD = float(os.environ.get("SPARKY_THRESHOLD", "0.5"))

# APIを叩く間隔(秒)。短くすると速いがレート制限に当たりやすい
DELAY = float(os.environ.get("SPARKY_DELAY", "0.3"))

ROOT = Path(__file__).parent
OUT_FILE = ROOT / "data" / "sparky.json"


def norm(s) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


TARGET_KEY = norm(TARGET_CARD)


def find_location(token, country_code="JP"):
    """国コードからロケーションIDを探す。見つからなければ None。"""
    data = api_get("/locations?limit=1000", token)
    for item in (data or {}).get("items", []):
        if item.get("isCountry") and item.get("countryCode") == country_code:
            print(f"  ロケーション: {item.get('name')} (id={item.get('id')})")
            return item.get("id")
    return None


def fetch_leaderboard(loc_id, token, limit):
    """上位プレイヤーを取得する。

    ランキングのエンドポイントはゲームの仕様変更で変わってきているので、
    候補を順に試して最初に返ってきたものを使う。
    """
    candidates = [
        f"/locations/{loc_id}/pathoflegend/players?limit={limit}",
        f"/locations/{loc_id}/rankings/players?limit={limit}",
    ]
    for path in candidates:
        data = api_get(path, token)
        items = (data or {}).get("items") or []
        if items:
            print(f"  リーダーボード: {path.split('?')[0]} から {len(items)}人")
            return items, path.split("?")[0]
    return [], None


def entry_rating(entry):
    """リーダーボードの項目からレート相当の数値を取り出す。"""
    for key in ("eloRating", "rating", "trophies"):
        v = entry.get(key)
        if isinstance(v, int):
            return v, key
    return None, None


def deck_of(battle, tag):
    """その試合で自分が使ったデッキ(カード一覧)を返す。"""
    team = battle.get("team") or []
    me = next(
        (p for p in team if str(p.get("tag", "")).lstrip("#").upper() == tag),
        team[0] if team else {},
    )
    return me.get("cards") or []


def uses_target(battle, tag) -> bool:
    return any(norm(c.get("name")) == TARGET_KEY for c in deck_of(battle, tag))


def inspect_player(entry, token):
    """1人分の対戦ログを調べる。該当しなければ None。"""
    tag = str(entry.get("tag", "")).lstrip("#").upper()
    if not tag:
        return None

    battles = api_get(f"/players/%23{tag}/battlelog", token)
    if not isinstance(battles, list) or not battles:
        return None

    total = len(battles)
    hit = 0
    wins = 0
    decided = 0
    latest_deck = None
    latest_support = None

    for b in battles:
        if not uses_target(b, tag):
            continue
        hit += 1
        if latest_deck is None:
            latest_deck = deck_of(b, tag)
            team = b.get("team") or []
            me = next(
                (p for p in team if str(p.get("tag", "")).lstrip("#").upper() == tag),
                team[0] if team else {},
            )
            latest_support = me.get("supportCards") or []
        r = battle_result(b, tag)
        if r is None:
            continue
        decided += 1
        if r:
            wins += 1

    rate = hit / total if total else 0
    if rate < THRESHOLD:
        return None

    rating, rating_key = entry_rating(entry)
    return {
        "tag": entry.get("tag"),
        "name": entry.get("name"),
        "clan": (entry.get("clan") or {}).get("name"),
        "boardRank": entry.get("rank"),
        "rating": rating,
        "ratingFrom": rating_key,
        "battles": total,
        "targetBattles": hit,
        "usageRate": round(rate * 100, 1),
        "wins": wins,
        "winRate": round(wins / decided * 100, 1) if decided else None,
        "deck": latest_deck or [],
        "supportCards": latest_support or [],
    }


def main():
    token = os.environ.get("CR_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("CR_API_TOKEN を設定してください")

    print(f"対象カード: {TARGET_CARD} / 判定: 直近の{THRESHOLD:.0%}以上で使用")

    loc_id = find_location(token)
    if loc_id is None:
        raise SystemExit("日本のロケーションIDが見つかりませんでした")

    board, source = fetch_leaderboard(loc_id, token, SCAN_LIMIT)
    if not board:
        raise SystemExit(
            "リーダーボードが取得できませんでした。"
            "シーズン切り替え中か、エンドポイントの仕様が変わった可能性があります"
        )

    board = board[:SCAN_LIMIT]
    print(f"{len(board)}人を調べます (1人ずつ対戦ログを見るので時間がかかります)")

    found = []
    started = time.time()
    for i, entry in enumerate(board, 1):
        result = inspect_player(entry, token)
        if result:
            found.append(result)
            print(
                f"  [{i}/{len(board)}] {result['name']}: "
                f"使用率{result['usageRate']}% / レート{result['rating']}"
            )
        if i % 100 == 0:
            elapsed = time.time() - started
            print(f"  --- {i}人完了 ({elapsed/60:.1f}分経過 / 該当{len(found)}人) ---")
        if i < len(board):
            time.sleep(DELAY)

    # レートの高い順。レート不明の人は末尾へ
    found.sort(key=lambda p: p["rating"] if p["rating"] is not None else -1, reverse=True)
    for i, p in enumerate(found, 1):
        p["rank"] = i

    save_json(
        OUT_FILE,
        {
            "updatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "card": TARGET_CARD,
            "country": "JP",
            "source": source,
            "scanned": len(board),
            "threshold": THRESHOLD,
            "players": found,
        },
    )

    print(
        f"完了: {len(board)}人中 {len(found)}人が該当 "
        f"({(time.time()-started)/60:.1f}分)"
    )


if __name__ == "__main__":
    main()
