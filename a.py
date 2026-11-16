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
from ddgs import DDGS
from datetime import datetime
import dotenv

wait_time = 0.1
SITE = "https://scratch.mit.edu"
API = "https://api.scratch.mit.edu"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
dotenv.load_dotenv("account.env")
id = os.getenv("id")
zatudoko = f"{API}/studios/{id}/comments/"
ai = os.getenv("ai")

REPLIED_FILE = "replied_comments.json"
STUDIO_CONTEXT_FILE = f"studio_context_{id}.json"      # ★NEW
CONTEXT_MAX_ENTRIES = 10                                # ★NEW
name = os.getenv("name_demo")
model = os.getenv("model")
MENTION_PREFIX = f"{model}(AIbot)によると:\n"

bot_uid = None  # ★NEW: ログイン後に取得するボットのユーザーID


# ---------- Web検索・ページ取得 ----------

def local_web_search(query: str) -> str:
    print(f"  🔍 [AI自律検索]: {query}")
    results = []
    for attempt in range(3):
        if attempt > 0:
            print(f"  ↻ 検索結果なしのため再試行 ({attempt}/2)...")
            time.sleep(1.5)
        try:
            with DDGS() as ddgs:
                try:
                    results = list(ddgs.text(query, region="jp-jp", timelimit="w", max_results=3))
                except Exception:
                    pass
                if not results:
                    results = list(ddgs.text(query, region="jp-jp", max_results=3))
            if results:
                break
        except Exception as exc:
            print(f"  検索エラー (試行 {attempt + 1}/3): {exc}")
    if not results:
        return "検索結果が見つかりませんでした。"
    return "\n".join(
        [f"・{r['title']} (URL: {r['href']}): {r['body']}" for r in results]
    )


def fetch_web_page(url: str) -> str:
    print(f"  🌐 [サイト直接閲覧]: {url}")
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_html = resp.read().decode("utf-8", "replace")
        text = re.sub(r"<(script|style).*?>.*?</\1>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "ページの本文を取得できませんでした。"
        return text[:2000]
    except Exception as exc:
        print(f"  閲覧エラー: {exc}")
        return f"ページの読み込みに失敗しました: {exc}"


def ask_ai_with_search(prompt_text: str) -> str:
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    now = datetime.now()
    today_str = f"{now.year}年{now.month}月{now.day}日({weekdays[now.weekday()]}) {now.strftime('%H:%M')}"

    chat_url = ai.replace("/api/generate", "/api/chat")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "最新情報や知らない事実を調べるためにWeb検索を行う(基本的に毎回使う)",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "検索キーワード"}},
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_page",
                "description": "指定されたURLのWebページに直接アクセスして詳細な本文を読み取る",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "閲覧したいWebページのURL"}},
                    "required": ["url"],
                },
            },
        },
    ]

    full_prompt = (
        f"【現在日時: {today_str}】\n"
        f"※最新情報や知らない言葉が含まれる場合は、必ず web_search ツールで検索し、"
        f"詳細な内容が必要な場合やURLが直接指定された場合は fetch_page ツールでページを直接閲覧してから答えてください。\n\n"
        f"{prompt_text}"
    )
    messages = [{"role": "user", "content": full_prompt}]

    def post_chat(msgs, use_tools=True):
        body = {"model": model, "messages": msgs, "stream": False, "options": {"num_ctx": 8192}}
        if use_tools:
            body["tools"] = tools
        req = urllib.request.Request(
            chat_url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8")).get("message", {})

    msg = {}
    for _ in range(3):
        msg = post_chat(messages, use_tools=True)
        if not msg.get("tool_calls"):
            break
        messages.append(msg)
        for call in msg["tool_calls"]:
            fn_name = call.get("function", {}).get("name")
            args = call["function"].get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            if fn_name == "web_search":
                result = local_web_search(args.get("query", ""))
                messages.append({"role": "tool", "content": result})
            elif fn_name == "fetch_page":
                result = fetch_web_page(args.get("url", ""))
                messages.append({"role": "tool", "content": result})
    else:
        msg = post_chat(messages, use_tools=False)

    text = msg.get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------- スタジオ共有コンテキスト ----------  ★NEW

def load_studio_context():
    try:
        with open(STUDIO_CONTEXT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_studio_context(entries):
    tmp = STUDIO_CONTEXT_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries[-CONTEXT_MAX_ENTRIES:], f, ensure_ascii=False, indent=2)
        os.replace(tmp, STUDIO_CONTEXT_FILE)
    except OSError as exc:
        print(f"  コンテキスト保存失敗: {exc}")


def append_studio_context(user, message, reply=None):
    entries = load_studio_context()
    entries.append({
        "time": datetime.now().strftime("%m-%d %H:%M"),
        "user": str(user)[:50],
        "message": str(message)[:200],
        "reply": str(reply)[:200] if reply else "",
    })
    save_studio_context(entries[-CONTEXT_MAX_ENTRIES:])


def format_studio_context(entries):
    if not entries:
        return "（スタジオ内での過去の会話履歴はまだありません）"
    lines = []
    for e in entries:
        lines.append(f"・{e.get('time', '')} @{e.get('user', '?')}: {e.get('message', '')}")
        if e.get("reply"):
            lines.append(f"  → bot: {e['reply']}")
    return "\n".join(lines)


# ---------- スレッド（返信一覧）関連 ----------  ★NEW

def fetch_thread(client, studio_id, root_cid):
    """スレッドの返信一覧を取得(新しい順ではなくAPIの返却順)。"""
    try:
        raw = client.get_json(
            f"{API}/studios/{studio_id}/comments/{root_cid}/replies?limit=25&offset=0"
        )
    except APIError as exc:
        print(f"  返信取得失敗: {exc}")
        return []
    return raw if isinstance(raw, list) else []


def format_thread_context(root_comment, replies, up_to_idx=None):
    """スレッド全体をAIに渡す文字列に整形。up_to_idx 以降は含めない。"""
    lines = []
    r_author = (root_comment.get("author") or {}).get("username", "?")
    r_content = html.unescape(str(root_comment.get("content") or ""))
    lines.append(f"[トップレベル] @{r_author}: {r_content}")
    if up_to_idx is None:
        target = replies
    else:
        target = replies[:up_to_idx + 1]
    for r in target:
        a = (r.get("author") or {}).get("username", "?")
        c = html.unescape(str(r.get("content") or ""))
        lines.append(f"@{a}: {c}")
    return "\n".join(lines)


# ---------- Scratchクライアント ----------

def init():
    global client, bot_name, bot_uid
    print("ログイン中...")
    env = load_env("account.env")
    client = ScratchClient()
    client.login(env["name"], env["pass"])
    bot_name = client.username
    # ボットのユーザーIDを取得（commentee判定用）
    try:
        me = client.get_json(f"{API}/users/{bot_name}")
        bot_uid = me.get("id")
    except Exception:
        bot_uid = None
    print(f"ログイン成功: {bot_name} (uid={bot_uid})")


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


# ---------- name 判定 ----------

def is_name(content):
    """先頭が name で始まるコメント、または @<ボット名> の直後に name が続く形式のみ True。"""
    head = content.lstrip()

    if head.startswith("@"):
        # @botusername 形式のチェック（ASCII ユーザー名部分のみ抽出）
        m = re.match(r'@([A-Za-z0-9_-]+)', head)
        if m:
            mentioned = m.group(1).lower()
            if bot_name and mentioned == bot_name.lower():
                # ボットへのメンション → 以降の本文で name を判定
                head = head[m.end():].lstrip()
            else:
                # 他ユーザーへのメンション → ボット宛てではない
                return False
        else:
            # @荒草 のように ASCII ユーザー名でない場合は @ だけ除去
            head = head[1:].lstrip()

    head_lower = head.lower()
    if not head_lower.startswith(name.lower()):
        return False
    rest = head[len(name):]
    if rest and rest[0].lower() in "abcdefghijklmnopqrstuvwxyz0123456789_-":
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


# ---------- 返信投稿の共通処理 ----------

def post_reply(client, studio_id, parent_id, commentee_id, content, headers):
    """返信を投稿して新コメントIDを返す。失敗時 None。"""
    payload = {
        "content": content[:500],
        "parent_id": str(parent_id),
        "commentee_id": str(commentee_id) if commentee_id is not None else "",
    }
    try:
        raw = client.post_raw(
            f"{SITE}/site-api/comments/gallery/{studio_id}/add/",
            payload, headers,
        )
    except APIError as exc:
        print(f"  → 投稿エラー: {exc}")
        if exc.code in (403, 429):
            time.sleep(10)
        return None
    except Exception as exc:
        print(f"  → 投稿エラー: {exc}")
        return None
    body = raw.decode("utf-8", "replace")
    new_id = extract_new_id(body)
    if new_id is None:
        snippet = body.strip().replace("\n", " ")[:200]
        print(f"  → 投稿失敗（コメントID取得不可）応答: {snippet}")
        if re.search(r'error|captcha|blocked|forbidden', body, re.I):
            print("    ※エラー/ブロックの可能性あり")
        time.sleep(5)
    return new_id


# ---------- メインループ ----------

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

    for i in range(len(comments) - 1, -1, -1):
        c = comments[i]
        try:
            cid = int(c["id"])
        except (KeyError, TypeError, ValueError):
            print("  (ID不正のためスキップ)")
            continue

        root_author_obj = c.get("author") or {}
        root_author = str(root_author_obj.get("username", "?"))
        root_author_id = root_author_obj.get("id")
        content = html.unescape(str(c.get("content") or ""))
        reply_count = int(c.get("reply_count") or 0)

        print(f"[{cid}] by {root_author} (返信数:{reply_count})")
        print(f"  内容: {content[:120]}")

        # スレッド（返信）を一括取得
        replies = fetch_thread(client, id, cid) if reply_count > 0 else []
        for r in replies:
            ra = (r.get("author") or {}).get("username", "?")
            rc = html.unescape(str(r.get("content") or ""))
            print(f"    └ [{r.get('id')}] {ra}: {rc[:80]}")

        studio_ctx_text = format_studio_context(load_studio_context())

        # ==========================================================
        # (A) トップレベルコメントへの返信
        # ==========================================================
        bot_already_in_thread = any(
            (r.get("author") or {}).get("username", "").lower() == bot_name.lower()
            for r in replies
        )

        if (cid not in replied
                and root_author.lower() != bot_name.lower()
                and is_name(content)
                and not bot_already_in_thread):
            print("  → トップレベルに返信します")
            thread_ctx = format_thread_context(c, replies)
            prompt = (
                f"【スタジオの最近の会話履歴(参考)】\n{studio_ctx_text}\n\n"
                f"【現在のスレッド】\n{thread_ctx}\n\n"
                f"上記スレッドのトップレベルコメント (@{root_author}) に対する返信を、"
                f"500文字以内で必ず日本語で書いてください。"
                f"（履歴は参考程度に留め、まず現在のスレッドの文脈に沿ってください）"
            )
            recomment = ""
            try:
                recomment = ask_ai_with_search(prompt)
            except Exception as exc:
                print(f"  AIエラー: {exc}")

            if not recomment:
                print("  → スキップ（AI応答が空）")
                time.sleep(3)
            else:
                budget = max(0, 500 - len(MENTION_PREFIX))
                final_content = (MENTION_PREFIX + recomment[:budget])[:500]
                new_id = post_reply(client, id, cid, root_author_id, final_content, headers)
                if new_id is not None:
                    print(f"  → 投稿成功: 新コメントID={new_id}")
                    replied.add(cid)
                    save_replied(replied)
                    append_studio_context(root_author, content, final_content)
                time.sleep(wait_time + 0.12)

        # ==========================================================
        # (B) 返信への返信
        # ==========================================================
        for idx, r in enumerate(replies):
            try:
                rid = int(r["id"])
            except (KeyError, TypeError, ValueError):
                continue

            if rid in replied:
                continue

            r_author_obj = r.get("author") or {}
            r_author = str(r_author_obj.get("username", "?"))
            r_author_id = r_author_obj.get("id")
            r_content = html.unescape(str(r.get("content") or ""))

            # commentee_id を取得（API実装差を吸収）
            r_commentee_id = r.get("commentee_id")
            if r_commentee_id is None:
                comm = r.get("commentee")
                if isinstance(comm, dict):
                    r_commentee_id = comm.get("id")

            if r_author.lower() == bot_name.lower():
                continue

            mentions_bot = is_name(r_content)
            directed_at_bot = (bot_uid is not None and r_commentee_id == bot_uid)

            if not (mentions_bot or directed_at_bot):
                continue

            # 既にこの返信より後でボットが発言していれば処理済みとみなす
            bot_replied_after = any(
                (rr.get("author") or {}).get("username", "").lower() == bot_name.lower()
                for rr in replies[idx + 1:]
            )
            if bot_replied_after:
                print(f"  → スレッド内で既に返信済みと判断、スキップ (rid={rid})")
                replied.add(rid)
                save_replied(replied)
                continue

            print(f"  → 返信に返信します (rid={rid} by {r_author})")
            thread_ctx = format_thread_context(c, replies, up_to_idx=idx)
            prompt = (
                f"【スタジオの最近の会話履歴(参考)】\n{studio_ctx_text}\n\n"
                f"【現在のスレッド】\n{thread_ctx}\n\n"
                f"上記スレッドの最後のコメント (@{r_author}) への返信を、"
                f"500文字以内で必ず日本語で書いてください。"
                f"（履歴は参考程度に留め、まず現在のスレッドの文脈に沿ってください）"
            )
            recomment = ""
            try:
                recomment = ask_ai_with_search(prompt)
            except Exception as exc:
                print(f"  AIエラー: {exc}")

            if not recomment:
                print("  → スキップ（AI応答が空）")
                time.sleep(3)
                continue

            budget = max(0, 500 - len(MENTION_PREFIX))
            final_content = (MENTION_PREFIX + recomment[:budget])[:500]

            # parent_id はスレッドのルート、commentee_id は返信元ユーザー
            new_id = post_reply(client, id, cid, r_author_id, final_content, headers)
            if new_id is not None:
                print(f"  → 投稿成功: 新コメントID={new_id}")
                replied.add(rid)
                save_replied(replied)
                append_studio_context(r_author, r_content, final_content)
            time.sleep(wait_time + 0.12)

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