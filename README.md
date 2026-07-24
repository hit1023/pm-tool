# pm-tool

自宅・オフィスのLAN内にある複数台のサーバーの**シャットダウン(SSH経由)**と**起動(Wake-on-LAN)**を、WebUIから手動操作・スケジュール管理でき、実行結果をログとして確認できるツールです。

Dockerコンテナ1つだけで動作し、特定のホストや環境に依存しない作りになっているため、複数台のサーバー/PCを管理している人であれば誰でも利用できます。

---

## 機能一覧

| 機能 | 概要 |
|---|---|
| サーバー(端末)登録 | 管理したいサーバーをホスト・SSH情報・MACアドレス等でWebUIから登録 |
| 認証情報の使い回し | SSHの秘密鍵/パスワードは「認証情報」タブで個別に登録し、サーバー登録時はセレクトで選ぶだけ(同じ鍵を複数台で共有可能) |
| 手動シャットダウン/起動 | 登録したサーバーをボタン1つでシャットダウン(SSH) / 起動(WOL) |
| スケジュール管理 | 曜日・時刻を選ぶだけ、またはcron形式で「毎日23時にシャットダウン」等を設定 |
| 実行ログ | いつ・誰が(またはどのスケジュールが)・何を実行し・成功したか失敗したかを一覧表示 |
| オンライン/オフライン確認 | SSHポートへの疎通確認で簡易的な稼働状態を表示 |
| Webコンソール | オンライン中のサーバー名をクリックすると、ブラウザ上でSSH対話シェルが開く(xterm.js使用) |
| 認証 | JWTによるログイン認証、ログイン失敗の連続でロックアウト |
| ユーザー管理 | WebUIから利用者アカウントを追加・削除・パスワード変更 |
| 認証情報の暗号化保存 | 登録したSSHパスワード/秘密鍵はDB上で暗号化して保存 |

---

## システム構成

```
┌──────────────────────────────────────────┐
│  ブラウザ  http://<ホスト>:8090          │
└───────────────────┬──────────────────────┘
                    │ JWT認証
┌───────────────────▼──────────────────────┐
│  pm-tool コンテナ (network_mode: host)   │
│  ┌────────────────────────────────────┐ │
│  │ FastAPI + Uvicorn                  │ │
│  │  - 認証/ユーザー管理                │ │
│  │  - サーバー登録CRUD                 │ │
│  │  - スケジュールCRUD                 │ │
│  │  - 実行ログAPI                     │ │
│  ├────────────────────────────────────┤ │
│  │ APScheduler (cronジョブ)           │ │
│  ├────────────────────────────────────┤ │
│  │ SQLite (/data/pm-tool.db)          │ │
│  └────────────────────────────────────┘ │
└───────┬───────────────────────┬──────────┘
       │ SSH(シャットダウン)     │ UDPブロードキャスト(WOL起動)
┌───────▼───────┐        ┌───────▼───────┐
│ 管理対象サーバー │        │ 管理対象サーバー │
│ (例: h-1,h-2…) │        │ (例: h-1,h-2…) │
└───────────────┘        └───────────────┘
```

---

## 必要なもの

- Docker Engine + Docker Compose(または Docker Desktop)
- pm-toolを動かすホストと、管理対象サーバーが**同一LAN(同一ブロードキャストドメイン)** にあること(WOLのため)
- 管理対象サーバーへSSH接続できるユーザーアカウント(公開鍵 または パスワード認証)
- 管理対象サーバーのマザーボード/NICがWake-on-LANに対応していること

---

## セットアップ手順

### 1. リポジトリをクローン

```bash
git clone git@github.com:hit1023/pm-tool.git
cd pm-tool
```

### 2. 環境変数ファイルを作成

```bash
cp .env.example .env
```

`.env` に設定が必要な項目:

| 変数名 | 必須 | 説明 |
|---|---|---|
| `APP_SECRET_KEY` | ✅ | JWT署名 と SSH認証情報の暗号化に使う秘密鍵。下記コマンドで生成 |
| `ADMIN_USERNAME` | - | 初回起動時に作成される初期管理者のユーザー名(既定値: `admin`) |
| `ADMIN_PASSWORD` | ✅ | 初期管理者のパスワード。**初回起動時のみ**使われます |
| `JWT_EXPIRE_HOURS` | - | ログインの有効期限(時間、既定24) |
| `LOG_RETENTION_DAYS` | - | 実行ログの保持日数(既定90、超過分は自動削除) |
| `APP_PORT` | - | 待受ポート(既定8090) |

`APP_SECRET_KEY` の生成コマンド:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

出力された文字列を `.env` の `APP_SECRET_KEY=` の後に貼り付けます。

> **注意:**
> - `.env` は `.gitignore` に含まれており、GitHubにはアップロードされません
> - `APP_SECRET_KEY` を後から変更すると、**それまでログイン済みのトークンが全て無効になり**、かつ**登録済みサーバーのSSHパスワード/秘密鍵が復号できなくなります**(再登録が必要)。運用開始後は変更しないでください
> - `ADMIN_PASSWORD` は初回起動(ユーザーテーブルが空の状態)でのみ有効です。2回目以降の起動には影響しません。パスワードを変更したい場合はWebUIの「ユーザー管理」から行ってください

### 3. コンテナを起動

```bash
docker compose up -d --build
```

### 4. 起動確認

```bash
docker compose ps
docker compose logs -f pm-tool
```

ログに `初期管理者ユーザー 'admin' を作成しました` のような行が出ていれば初回セットアップは成功です。

### 5. ブラウザでアクセス

```
http://<pm-toolを動かしているホストのIP>:8090
```

`.env` に設定した `ADMIN_USERNAME` / `ADMIN_PASSWORD` でログインします。

---

## 認証について(外部公開する場合は必読)

pm-toolは実サーバーの電源を操作できるツールのため、外部(インターネット)に公開する場合は特に以下に注意してください。

- **必ずHTTPSリバースプロキシ配下に置いてください**(nginx-proxy-manager、Caddy、Cloudflare Tunnel等)。pm-tool自体はHTTPで待ち受けるため、素のHTTPで直接インターネットに公開しないでください
- ログインはJWT(`Authorization: Bearer <token>`)で行い、トークンはブラウザの`localStorage`に保存されます(`JWT_EXPIRE_HOURS`で有効期限を設定)
- 同一ユーザー名で**5回連続ログインに失敗すると5分間ロックアウト**されます(総当たり攻撃対策)
- 新規登録画面は存在しません。利用者を増やしたい場合は、ログイン後に「ユーザー管理」タブから追加してください(全ユーザーが同一権限です)
- 登録したサーバーのSSHパスワード/秘密鍵は`APP_SECRET_KEY`から導出した鍵でDB上に暗号化保存され、WebUI/APIから平文で読み出すことはできません

---

## 認証情報とサーバー(端末)登録の手順

SSHの秘密鍵/パスワードと、サーバー自体の登録は別のタブに分かれています。同じ鍵を複数台のサーバーで使い回している場合、認証情報は1回登録するだけで済みます。

### 1. 認証情報を登録する

「認証情報」タブの「+ 認証情報を登録」から登録します。

| 項目 | 説明 |
|---|---|
| 名前 | WebUI上での表示名(任意の文字列、例: `共通SSH鍵(hit)`) |
| 認証方式 | 秘密鍵 または パスワード |
| 秘密鍵/パスワード | 選んだ認証方式に応じた認証情報。編集時、空欄のままなら既存の値は変更されません |
| 秘密鍵のパスフレーズ | 秘密鍵にパスフレーズを設定している場合のみ入力(任意) |

登録した認証情報は、使用中のサーバーが1台でもある間は削除できません。

### 2. サーバーを登録する

「サーバー」タブの「+ サーバーを登録」から、管理したいサーバーを登録します。

| 項目 | 説明 |
|---|---|
| 表示名 | WebUI上での表示名(任意の文字列) |
| ホスト | SSH接続先のIPアドレス/ホスト名 |
| SSHポート | 既定22 |
| SSHユーザー | シャットダウンコマンドを実行するユーザー |
| 認証情報 | 事前に登録した認証情報をセレクトから選択(未設定のままだとシャットダウンは実行できません) |
| MACアドレス | WOL起動に使用(`ip link` や `ifconfig` で確認できます) |
| ブロードキャストIP | 省略可。未指定時は`255.255.255.255`宛に送信。届かない場合はサブネットの直接ブロードキャストアドレス(例: `192.168.0.255`)を指定してください |
| シャットダウンコマンド | 既定 `sudo shutdown -h now`。環境に応じて変更可能 |

### SSHユーザー側で必要な設定

シャットダウンコマンドの実行には`sudo`権限が必要です。実行のたびにパスワード入力を求められないよう、**シャットダウンコマンドに限定したNOPASSWD設定**を推奨します。

```bash
# 対象サーバー側で /etc/sudoers.d/pm-tool-shutdown として保存
echo 'hit ALL=(ALL) NOPASSWD: /sbin/shutdown -h now' | sudo tee /etc/sudoers.d/pm-tool-shutdown
sudo chmod 440 /etc/sudoers.d/pm-tool-shutdown
```

(`hit`の部分は実際のSSHユーザー名に置き換えてください。`ALL=(ALL) NOPASSWD: ALL`のような広い許可は避け、必要なコマンドだけに絞ることを推奨します。)

> **macOSの場合**: シャットダウンコマンドに`sudo pmset sleepnow`(スリープ、WOLでの復帰に対応)を使う場合は、NOPASSWD対象もそれに合わせてください。
> ```bash
> echo 'hit ALL=(ALL) NOPASSWD: /usr/bin/pmset sleepnow' | sudo tee /etc/sudoers.d/pm-tool-shutdown
> sudo chmod 440 /etc/sudoers.d/pm-tool-shutdown
> ```
> (macOSの詳細・完全シャットダウンとの違いは後述の「Wake-on-LANが届かない場合 > 対象機がmacOSの場合の補足」を参照)

### MACアドレスの確認方法

```bash
# 対象サーバー側で、WOLを使うオンボードNICのMACアドレスを確認
ip link show <インターフェース名>

# macOSの場合
ifconfig en0 | grep ether
```

---

## スケジュール設定

「スケジュール」タブから、サーバーごとに「いつ」「何を(シャットダウン/起動)」実行するかを登録できます。

- 通常は曜日チェックボックス+時刻を選ぶだけで設定できます(内部的にcron式に変換されます)
- 「詳細設定」をONにすると、cron形式(`分 時 日 月 曜日`)を直接入力できます(例: 平日23時なら `0 23 * * 1-5`)
- スケジュールは「無効化」しておくことで、削除せずに一時停止できます

---

## 実行ログ

「実行ログ」タブで、手動操作・スケジュール実行の両方の履歴を確認できます。サーバー・アクション種別・成功/失敗で絞り込み可能です。

- シャットダウンは、SSHでコマンドを送信できた時点で「成功」として記録します(シャットダウン処理自体は接続を切断するため、その後の完了までは確認できません)
- 起動(WOL)は、マジックパケットの送信に成功した時点で「成功」として記録します(対象機が実際に起動するかはハードウェア側のWOL設定に依存します)
- ログは`LOG_RETENTION_DAYS`で指定した日数を超えると毎日AM3:30に自動削除されます

---

## Webコンソール

「サーバー」タブで状態が**オンライン**と表示されているサーバーは、そのバッジをクリックするとブラウザ上にターミナルが開き、SSH経由で対話的にログインできます([xterm.js](https://xtermjs.org/)を使用、外部CDNには依存せずリポジトリに同梱)。

- サーバー登録時に選択した「認証情報」でSSH接続します(認証情報が未設定、またはサーバーが無効化されている場合は開けません)
- 接続の仕組み: WebUIから短命(30秒・1回限り)の接続チケットを発行してもらい、それを使って`/ws/console/{id}`のWebSocketへ接続 → サーバー側でSSHの対話シェル(pty)を開いて双方向に中継します。JWTトークン自体をWebSocket接続に使い回さないための仕組みです
- OS問わず(Linux/macOS/Windowsの OpenSSH Server)同じ仕組みで動作します。ただしWindowsの古い`cmd.exe`は端末制御シーケンスの扱いが異なるため表示が乱れることがあります(PowerShellや最近のWindows Terminal相当の環境であれば問題ありません)
- ウィンドウをリサイズすると、ターミナルのサイズ(行数・列数)も自動的に追従します
- タブを切り替えたり他のサーバーを操作しても接続は維持されますが、ページを再読み込みすると切断されます

---

## Wake-on-LANが届かない場合

`network_mode: host`で起動していても、以下のような要因でWOLが届かないことがあります。

1. **対象機のOS/ドライバでWOLが無効になっている** — `ethtool <iface> | grep Wake-on` が `d`(disabled)になっていないか確認し、`g`(magic packetで有効)にする
2. **有線LANが複数枚刺さっており、意図しない方(増設NIC等)に接続されている** — オンボードNICは対応していても、増設NICは完全シャットダウン(S5)からのWOLに対応していない場合があります
3. **BIOS/UEFI側の「WOL起動時専用のブートソース」設定がNetwork(PXE)になっている** — 通常の起動順序とは別に、WOLで起動したときだけ参照される設定がある機種があります。「Local Hard Drive」相当に変更してください
4. **pm-toolとブロードキャストが届かないセグメントに分かれている**(VLAN、別サブネット等) — サーバー登録時の「ブロードキャストIP」に、対象機と同じセグメントの直接ブロードキャストアドレス(例: `192.168.0.255`)を指定してください

### 対象機がmacOSの場合の補足

Macを管理対象に含める場合、Windows/LinuxのPCとは勝手が異なるので注意してください。

- **有線LAN(Ethernet)接続が前提**です。Wi-Fi接続のMacはマジックパケットによる通常のWOLには対応していません(Wi-Fi経由の復帰には別途Bonjourスリーププロキシ対応ルーター等が必要で、本ツールの想定外です)
- **有効化手順**:
  - GUI: `システム設定 → バッテリー`(ノートブックは`バッテリー`、Mac mini/Studioは`エネルギー`)`→ オプション → 「ネットワークアクセスのためのスリープ解除」をオン`
  - ターミナル: `sudo pmset -a womp 1` で有効化、`pmset -g | grep womp` で `womp 1` になっていることを確認(`womp`は"Wake On Magic Packet"の略)
- **完全シャットダウンからは基本的に起動できません** — MacのWOLはWindows/Linuxと異なり、電源を落とした状態(シャットダウン)からの復帰には正式対応していません。**スリープ状態からの復帰のみ**が対象です。そのため、pm-toolでMacを管理する場合は、サーバー登録時の「シャットダウンコマンド」を`sudo shutdown -h now`ではなく`sudo pmset sleepnow`(スリープ)に変更することを推奨します。これならpm-toolの「シャットダウン」→「起動(WOL)」のサイクルが実際に機能します

### 対象機がWindowsの場合の補足

WindowsもLinuxと同様、コード変更なしで管理できます(シャットダウンコマンドが自由に設定できる作りのため)。ただし事前準備が必要です。

- **OpenSSH Serverの有効化**(既定では入っていません)。PowerShell(管理者)で以下を実行:
  ```powershell
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Start-Service sshd
  Set-Service -Name sshd -StartupType 'Automatic'
  ```
  (GUIからの場合は `設定 → アプリ → オプション機能 → 機能の追加 → OpenSSH サーバー`)
- **シャットダウンコマンド**: サーバー登録時に `shutdown /s /t 0` を指定してください(Linuxの`sudo shutdown -h now`とは書式が異なります)。ローカルコンピュータのシャットダウンには管理者権限は不要な場合が多く、Linuxのsudoers設定に相当するものは基本的に不要です
- **公開鍵認証を使う場合の注意**: Windows版OpenSSHは`authorized_keys`の配置場所とACL(アクセス権)がLinuxと異なります。管理者グループのアカウントでは`%ProgramData%\ssh\administrators_authorized_keys`に公開鍵を配置し、SYSTEM/Administratorsのみアクセス可能なACLに`icacls`で設定する必要があります(一般ユーザーでは`~\.ssh\authorized_keys`で通常のLinuxと同様の扱い)。設定を誤るとsshdが鍵を無視してパスワード認証にフォールバックするか拒否するので、うまくいかない場合はまずパスワード認証で動作確認することを推奨します
- **WOLの有効化**:
  - BIOS/UEFIで「Wake on LAN」相当の設定を有効化
  - デバイスマネージャー → ネットワークアダプター → プロパティ →「電源の管理」タブで「このデバイスで、コンピューターのスタンバイ状態を解除できるようにする」をオン、「詳細設定」タブで「Wake on Magic Packet」を有効化
  - **高速スタートアップを無効化することを推奨** — `コントロールパネル → 電源オプション → 電源ボタンの動作を選択 → 現在利用可能ではない設定を変更する →「高速スタートアップを有効にする」のチェックを外す`。有効なままだとシャットダウン後にNICがWOL待受状態にならず、起動できないことがあります

---

## データの永続化

| データ | 保存先 |
|---|---|
| サーバー登録情報・スケジュール・実行ログ・ユーザー | SQLite(`./data/pm-tool.db`、Dockerボリュームマウント) |
| APSchedulerのジョブ情報 | 同じSQLiteファイル内(再起動後もスケジュールが復元されます) |
| ログイン認証トークン | ブラウザの`localStorage` |

`docker compose down` してもデータは消えません。完全に削除する場合は `./data` ディレクトリを削除してください。

---

## 技術スタック

| レイヤー | 使用技術 |
|---|---|
| フロントエンド | HTML / CSS / Vanilla JavaScript(SPA、ビルド不要) |
| バックエンド | FastAPI + Uvicorn(Python 3.11) |
| DB | SQLite(SQLAlchemy ORM) |
| スケジューラ | APScheduler(`SQLAlchemyJobStore`) |
| SSH実行 | paramiko |
| WOL送信 | Python標準`socket`によるマジックパケット自前生成 |
| Webコンソール | FastAPI WebSocket + paramiko対話シェル、フロントエンドは`xterm.js`(MITライセンス、リポジトリに同梱) |
| 認証 | JWT(`python-jose`) + bcrypt(`passlib`) |
| 認証情報暗号化 | `cryptography`(Fernet) |

---

## 主なコマンド

```bash
# 起動(初回・コード変更後)
docker compose up -d --build

# 起動(通常時)
docker compose up -d

# 停止
docker compose down

# ログ確認
docker compose logs -f pm-tool

# .envの変更を反映(再ビルド不要)
docker compose up -d --force-recreate pm-tool

# コンテナ内のSQLiteに直接アクセス
docker exec -it pm-tool python3 -c "import sqlite3; print(sqlite3.connect('/data/pm-tool.db').execute('select name from sqlite_master').fetchall())"
```

---

## トラブルシューティング

### ログインできない / 初期管理者が作成されない

```bash
docker compose logs pm-tool | grep -i admin
```

`ADMIN_PASSWORDが未設定のため初期管理者を作成できません` と出ている場合は `.env` に `ADMIN_PASSWORD` を設定し、`docker compose up -d --force-recreate pm-tool` を実行してください。

初期管理者は**ユーザーテーブルが空のときのみ**作成されます。一度でも作成された後は`.env`の値を変えても反映されません(WebUIの「ユーザー管理」から変更してください)。

### シャットダウンが失敗する

実行ログのメッセージ欄に接続エラーの詳細が出力されます。よくある原因:

- SSHの秘密鍵/パスワードが間違っている、または未登録
- SSHユーザーに、シャットダウンコマンドをNOPASSWDで実行する`sudo`権限がない(上記「SSHユーザー側で必要な設定」を参照)
- ファイアウォールでSSHポートがブロックされている

### WOLで起動しない

上記「Wake-on-LANが届かない場合」の4項目を順に確認してください。

### `APP_SECRET_KEY`を変更したら登録済みサーバーの認証情報が使えなくなった

`APP_SECRET_KEY`はSSH認証情報の暗号化鍵を兼ねているため、変更すると復号できなくなります。変更前の値が分かる場合は元に戻すか、分からない場合は影響を受けたサーバーの認証情報を再登録してください。

---

## ディレクトリ構成

```
pm-tool/
├── docker-compose.yml       # network_mode: host, ポート8090, dataボリューム
├── .env.example              # 環境変数サンプル
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                # FastAPIルーティング
│   ├── database.py            # DB接続設定
│   ├── models.py              # User/Credential/Server/Schedule/ExecutionLog
│   ├── schemas.py             # Pydanticスキーマ
│   ├── auth.py                # JWT/パスワードハッシュ/ログイン制限
│   ├── crypto.py              # SSH認証情報の暗号化/復号
│   ├── ssh_actions.py         # SSH経由のシャットダウン実行
│   ├── wol.py                 # WOLマジックパケット送信
│   ├── scheduler.py           # APScheduler管理
│   └── console.py             # Webコンソール(WebSocket + SSH対話シェル)
└── webui/
    ├── index.html              # フロントエンドSPA
    └── vendor/xterm/           # xterm.js(同梱、MITライセンス)
```

---

## ライセンス

MIT
