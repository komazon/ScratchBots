import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
import html
import random

SITE = "https://scratch.mit.edu"
API = "https://api.scratch.mit.edu"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA}

id = 52162859
zatudoko = f"{API}/studios/{id}/comments/"
ai = "http://localhost:11434/api/generate"

REPLIED_FILE = "replied_comments.json"
name = "to_zeon"
model = "sbot"
MENTION_PREFIX = f"{model}(AIbot)によると:\n"

def init():
        global client, bot_name
        print("ログイン中...")
        env = load_env("account.env")
        client = ScratchClient()
        client.login(env["name"], env["pass"])
        bot_name = client.username
        print(f"ログイン成功: {bot_name}")
class APIError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def scratch_request(url, data=None, headers=None, opener=None, timeout=15):
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise APIError(f"HTTP {exc.code}: {body[:200]}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise APIError(f"通信エラー: {exc.reason}") from exc


class ScratchClient:
    def __init__(self):
        self._lock = threading.Lock()
        self.logged_in = False
        self.username = None
        self.token = None
        self._cookie_jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookie_jar)
        )

    def _cookie(self, name):
        for c in self._cookie_jar:
            if c.name == name:
                return c.value
        return None

    def login(self, username, password):
        with self._lock:
            self.logged_in = False
            req = urllib.request.Request(f"{SITE}/csrf_token/", headers=HEADERS)
            try:
                self.opener.open(req, timeout=15).close()
            except urllib.error.HTTPError as exc:
                raise APIError(f"CSRFトークン取得失敗: HTTP {exc.code}", exc.code) from exc
            token = self._cookie("scratchcsrftoken")
            if not token:
                raise APIError("CSRFトークンを取得できませんでした")
            payload = json.dumps({
                "username": username, "password": password, "useMessages": True
            }).encode("utf-8")
            headers = {
                "User-Agent": UA,
                "X-CSRFToken": token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": SITE + "/",
                "Content-Type": "application/json",
            }
            try:
                raw = scratch_request(f"{SITE}/login/", payload, headers, self.opener)
                response = json.loads(raw.decode("utf-8"))
            except APIError:
                raise
            except (ValueError, UnicodeDecodeError) as exc:
                raise APIError("ログイン応答を解釈できませんでした") from exc
            if isinstance(response, list):
                response = response[0] if response else {}
            if not response.get("success") or not self._cookie("scratchsessionsid"):
                raise APIError(str(response.get("msg") or "ログインに失敗しました"), 401)
            self.username = str(response.get("username") or username)
            self.logged_in = True
            try:
                raw = scratch_request(API, None, {"X-CSRFToken": token}, self.opener)
                self.token = json.loads(raw.decode("utf-8")).get("user", {}).get("token")
            except Exception:
                self.token = None
            return self.username

    def get_json(self, url, headers=None):
        raw = scratch_request(url, None, headers, self.opener)
        return json.loads(raw.decode("utf-8"))

    def post_raw(self, url, payload, headers=None):
        data = json.dumps(payload).encode("utf-8")
        return scratch_request(url, data, headers, self.opener)


# ---------- 返信済み履歴の永続化 ----------

def load_replied():
    try:
        with open(REPLIED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            ids = data.get("replied", [])
        elif isinstance(data, list):
            ids = data
        else:
            ids = []
        return set(int(x) for x in ids if str(x).isdigit())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return set()


def save_replied(ids):
    tmp = REPLIED_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"replied": sorted(ids)}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, REPLIED_FILE)
    except OSError as exc:
        print(f"  履歴保存失敗: {exc}")


# ---------- 既返信チェック（API /replies ベース） ----------

def bot_has_replied(client, studio_id, comment_id, bot_username):
    try:
        replies = client.get_json(
            f"{API}/studios/{studio_id}/comments/{comment_id}/replies?limit=100&offset=0"
        )
    except APIError as exc:
        if exc.code == 404:
            return True
        print(f"  (返信確認失敗: {exc})")
        return False
    except Exception as exc:
        print(f"  (返信確認エラー: {exc})")
        return False
    if not isinstance(replies, list):
        return False
    target = bot_username.lower()
    for r in replies:
        author = (r.get("author") or {}).get("username", "")
        if str(author).lower() == target:
            return True
    return False


# ---------- レスポンスからコメントID抽出 ----------

def extract_new_id(body):
    m = re.search(r'data-comment-id="(\d+)"', body)
    if m:
        return int(m.group(1))
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict):
        v = obj.get("id")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        v = obj[0].get("id")
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
    return None


# ---------- to_zeon 判定 ----------

def is_to_zeon(content):
    """先頭（空白/改行/@を除く）が to_zeon で、続きが別ユーザー名の文字でない場合のみ True。"""
    head = content.lstrip().lower()
    if head.startswith("@"):
        head = head[1:]
    if not head.startswith(name.lower()):
        return False
    rest = head[len(name):]
    if rest and rest[0] in "abcdefghijklmnopqrstuvwxyz0123456789_-":
        return False
    return True


def load_env(filepath):
    env = {}
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def main():
    
    replied = load_replied()
    print(f"返信済み履歴: {len(replied)} 件")

    headers = {
        "X-CSRFToken": client._cookie("scratchcsrftoken") or "",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{SITE}/studios/{id}/comments/",
        "Content-Type": "application/json",
    }

    comments = client.get_json(zatudoko)
    print(f"コメント数: {len(comments)}\n")

    # 古い順に処理
    for i in range(len(comments) - 1, -1, -1):
        c = comments[i]
        try:
            cid = int(c["id"])
        except (KeyError, TypeError, ValueError):
            print("  (ID不正のためスキップ)")
            continue
        author = c.get("author") or {}
        author_name = str(author.get("username", "?"))
        user_id = author.get("id")
        content = html.unescape(str(c.get("content") or ""))
        reply_count = int(c.get("reply_count") or 0)

        print(f"[{cid}] by {author_name} (返信数:{reply_count})")
        print(f"  内容: {content}")

        # (1) ローカル履歴
        if cid in replied:
            print("  → スキップ（履歴にあり）")
            continue

        # (2) 自分のコメント
        if author_name.lower() == bot_name.lower():
            print("  → スキップ（自分のコメント）")
            continue

        # (3) to_zeon が先頭に無ければ返信しない
        if not is_to_zeon(content):
            print("  → スキップ（先頭に to_zeon なし）")
            continue

        # (4) 既存返信があればAPIで自分の返信を確認
        if reply_count > 0:
            if bot_has_replied(client, id, cid, bot_name):
                print("  → スキップ（APIで既返信を検出）")
                replied.add(cid)
                save_replied(replied)
                continue
            print("  （他人の返信のみ検出 → 返信続行）")

        # (5) AI応答生成
        recomment = ""
        AI = {
            "model": model,
            "prompt": f"以下のコメントに500文字以内で絶対に日本語(Japanese)で返信してください。\n\n{content}",
            "stream": False,
        }
        try:
            req = urllib.request.Request(
                ai,
                data=json.dumps(AI).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                recomment = (json.loads(resp.read().decode("utf-8"))
                             .get("response") or "").strip()
        except Exception as exc:
            print(f"  AIエラー: {exc}")

        if not recomment:
            print("  → スキップ（AI応答が空）")
            time.sleep(3)
            continue

        # to_zeon 先頭なので接頭辞を付与（500字超過を防ぐ）
        budget = max(0, 500 - len(MENTION_PREFIX))
        recomment = MENTION_PREFIX + recomment[:budget]
        print(f"  → to_zeon向け: 「{MENTION_PREFIX}」を先頭に付与")

        # (6) 投稿
        payload = {
            "content": recomment[:500],
            "parent_id": str(cid),
            "commentee_id": str(user_id) if user_id is not None else "",
        }
        try:
            raw = client.post_raw(
                f"{SITE}/site-api/comments/gallery/{id}/add/",
                payload, headers,
            )
        except APIError as exc:
            print(f"  → 投稿エラー: {exc}")
            if exc.code in (403, 429):
                time.sleep(10)
            continue
        except Exception as exc:
            print(f"  → 投稿エラー: {exc}")
            continue

        body = raw.decode("utf-8", "replace")
        new_id = extract_new_id(body)
        if new_id is None:
            print("  → 投稿失敗（コメントID取得不可）")
            snippet = body.strip().replace("\n", " ")[:200]
            print(f"    応答抜粋: {snippet}")
            if re.search(r'error|captcha|blocked|forbidden', body, re.I):
                print("    ※エラー/ブロックの可能性あり")
            time.sleep(5)
            continue

        print(f"  → 投稿成功: 新コメントID={new_id}")

        # 通知のみバグ対策: ローカル履歴を必ず更新
        replied.add(cid)
        save_replied(replied)

        time.sleep(wait_time+0.12)  # 投稿間隔を少し空ける))

    print("\n完了")

init()
while True:
    try:
        main()
    except Exception as exc:
        print(f"エラー発生: {exc}")
    wait_time = 1 + abs(random.gauss(0, 0.12))
    print(f"{wait_time:.1f}秒後に再実行します...\n")
    time.sleep(wait_time)