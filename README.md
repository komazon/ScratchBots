# ScratchBots
Scratchのスタジオ上で動作する自動ボット  
すぐに試したいですか？:
https://scratch.mit.edu/studios/52162859/comments/
## 初期設定
### a.py内の設定:
1. 『`id`』を自分のスタジオのIDにします
2. 『`name`』の呼び出し名を変更します
### account.envの設定:
アカウント名とパスワードをbotとして使うScratchアカウントのものに設定します。
### モデル設定:
1. `modelfile`を`ollama create`するか、独自モデルを作ります  
(独自の場合は必要に応じてa.pyの`model`変数を変更します)

## 実行
a.pyとaccount.envを一つのフォルダに展開して、`python a.py`します。
## 呼び出し
初期設定で設定したスタジオのコメント欄で、『name変数の値 文』のようにコメントします。
