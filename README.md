# ScratchBots
Scratchのスタジオ上で動作する自動ボット  
<a href="https://scratch.mit.edu/studios/52162859/comments/">すぐに試したいですか？</a>  
利用する場合に**任意で**このリポジトリへのリンクをお願いしています。
## 初期設定
### 入れるやつ:
- Ollama
  -Qwen3:14b  
- Python3.14.4
### account.envの設定:
1. アカウント名とパスワードをbotとして使うScratchアカウントのものに設定します。  
1. 『`id`』を自分のスタジオのIDにします
3. 『`name_demo`』を呼び出し名にします
4. 『`model`』を使うモデル名にします
5. 『`ai`』に関しては必要な人だけ別のエンドポイントにしてください

## 実行
a.pyとaccount.envを一つのフォルダに展開して、`python a.py`します。
## 呼び出し
初期設定で設定したスタジオのコメント欄で、『name_demo変数の値 文』のようにコメントします。
