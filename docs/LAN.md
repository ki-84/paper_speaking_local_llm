# 別のPCから使う

1. Linuxで `./studio start`、`./studio access` を実行します。
2. 表示された `data/tls/pki/authorities/local/root.crt` を信頼できる方法（USB、SSH/SCPなど）で利用PCにコピーします。
3. `studio access` に表示されるSHA-256指紋と、コピーした証明書の指紋を照合します。
4. 利用PCで証明書を信頼します。
   - Windows: `certmgr.msc` → 現在のユーザー →「信頼されたルート証明機関」→ 証明書 → インポート。
   - macOS: キーチェーンアクセスでログインキーチェーンに追加 → 証明書を開く →「信頼」→ SSLを常に信頼。
   - Ubuntu: `sudo cp root.crt /usr/local/share/ca-certificates/paperspeak.crt`、`sudo update-ca-certificates`。Firefox独自ストアの場合は設定→証明書→認証局で追加。
5. ブラウザを再起動し、`https://192.168.10.112:8443` を開きます。パスワード設定が有効ならサインインし、練習時にマイクの使用を許可します。個人用LANでパスワードを外すにはLinuxで `./studio auth-off`、戻すには `./studio auth-on` を実行します。

HTTPS警告の一時的な回避だけでは、ブラウザによってマイクが使えません。[getUserMediaは安全なコンテキストを必要とします](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)。Caddyの[ローカルHTTPS](https://caddyserver.com/docs/automatic-https#local-https)のCAを信頼する手順です。

MacのChromeで録音中になっても声が入らない場合は、教材画面の **Check microphone** を押し、話したときに **Input level** が動くか確認します。動かなければ画面の **Microphone** で「MacBook Air Microphone」など実際に使う入力を選び、再度確認してください。[macOSの「システム設定 → サウンド → 入力」](https://support.apple.com/en-asia/guide/mac-help/mchl9777ee30/mac)でも入力デバイスと入力レベルを確認できます。[「プライバシーとセキュリティ → マイク」](https://support.apple.com/en-my/guide/mac-help/mchla1b1e1fe/mac)でChromeを許可し、必要ならChromeを再起動します。[Chromeの「設定 → プライバシーとセキュリティ → サイトの設定 → マイク」](https://support.google.com/chrome/answer/2693767?co=GENIE.Platform%3DDesktop&hl=en-IO)でも標準マイクとこのサイトの許可を確認できます。入力レベルが動いてから録音します。

マイクを接続した後に選択欄へ出ないときは **Refresh microphones** を押します。Chromeがマイクを認識し直せない場合はChromeを完全に終了して起動し直し、ページを再読み込みします。「System default」はMacの既定入力を使う設定で、選択欄に機器名がなくても既定マイクが正常なら録音できます。

LinuxのIPをルーターで固定することを勧めます。IP/名前が変わる場合は `PAPERSPEAK_LAN_HOST=新しいIP ./studio start` とします。実行中のプロセスには反映されないため先に停止してください。Caddyは新しい名前/IPの証明書を発行します。同じCAを保持していれば再インポートは不要です。

接続できない場合は `./studio doctor` を確認します。ホストのファイアウォールを使用している場合、利用LANからTCP8443への接続を許可します。ルーターのポート転送は不要です。APIの8190と推論の8191はLANに公開しません。

このLinuxではUFWが有効でした。管理者がLANからの接続を許可する場合は、Linuxの端末で `sudo ufw allow from 192.168.10.0/24 to any port 8443 proto tcp` を実行します。現在のルールは管理者権限が必要なため、自動検証では未確認です。

CAの信頼を解除する場合は、インポートしたPaperSpeak/Caddyの認証局証明書だけを利用PCから削除します。
