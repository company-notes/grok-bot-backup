# Grok Bot Backup｜助理資料備份程式

把共用電腦（box）上可存取的助理資料備份成原始資料封存檔及**人看得懂**的封存檔：人物設定、記憶（本機檔＋平台投放）、筆記、附件、工作區產出，以及**每位助理的完整對話**（含 2026-10-06 之後）。


## 設定

1. `cp config.example.json config.json`，填入要檢查平台記憶投放的助理 ID 與顯示名（`config.json` 已 gitignore，不要提交）。
2. 環境變數（皆可選）：

| 變數 | 預設 | 用途 |
|---|---|---|
| `AGENT_DATA` | `/home/box/agent-data` | 平台助理資料根目錄 |
| `WORKSPACE` | `/workspace` | 工作區（會備份，排除 exports 等） |
| `OUT_ROOT` | `$WORKSPACE/exports` | 匯出位置 |
| `MEMORY_DROP` | `$WORKSPACE/exports/memory-drop` | RecallMemory 投放目錄 |
| `NUWA_BACKUP_CONFIG` | `<repo>/config.json` | 站台設定檔 |
| `NUWA_BACKUP_USER_LABEL` | `使用者` | 對話中使用者一方的標籤 |
| `INCLUDE_SUBAGENTS` | `0` | 設 1 一併備份子代理逐字稿 |
| `INCLUDE_BULKY_DIRS` | `0` | 設 1 一併備份 `.venv*`、`.cache`、`node_modules`、`__pycache__`、`.git` 等大型工作區目錄（原始與可讀包都適用） |

公開的原始碼套件沒有放入實際帳密或 token；但**產出的備份檔會包含所有納入資料夾的內容**，例如完整對話、私密筆記、設定檔、工作區裡可能存在的憑證。**這不是經過去敏或加密的公開資料！** 檔案權限 600/700 只能限制同一系統上的一般存取，無法取代加密。請勿提交、公開分享或上傳未加密的 exports。跨裝置儲存時使用可信任的加密工具。

本工具是針對**可存取 Grok Bot 共用 box 上 `agent-data`、`search-index.db` 的環境**所寫，不是登入任何網頁帳號即可使用的通用匯出 API。`search-index.db` 是平台內部資料格式，版本更新可能需要修改程式。

## 授權

尚未選定授權條款（LICENSE 待定）。公開 GitHub 專案後，如果希望其他人可以合法再利用、修改和散布，請選擇 MIT 或其他合適授權並加入 LICENSE。

## 用法

```bash
# 1) 人可讀包→ /workspace/exports/virtual-office-<UTC>.tar.gz
bin/export-virtual-office.sh

# 2) 完整原始備份（含 SQLite、原始 json）→ /workspace/exports/grok-bot-export-<UTC>.tar.gz
bin/export-grok-bot-backup.sh

# 需要連大型目錄及子代理逐字稿也一起保存：
INCLUDE_BULKY_DIRS=1 INCLUDE_SUBAGENTS=1 bin/export-grok-bot-backup.sh

# 驗證一份匯出（對話則數 vs 平台索引、程式副本、筆記是否齊全）
bin/verify-export.sh /workspace/exports/virtual-office-<UTC> --archive /workspace/exports/virtual-office-<UTC>.tar.gz
# 若要額外要求某天確實有對話才傳入 --date YYYY-MM-DD
```

每次備份都是全量，不需要任何暗號。舊參數 `--include-private`、`--passphrase` 仍可傳入，但會被忽略，只印出一行提示。**不要把 `--passphrase` 當作加密開關**：備份沒有加密。

匯出資料夾與 tar 檔一律 700／600 權限（內容包含私人資料，以及舊對話裡可能貼過的 token）。

## 輸出結構（virtual-office）

```
virtual-office-<UTC>/
├── README.md、對話索引.md（全部助理總覽）、花名冊.md
├── 00-共用/          公司 wiki、共用記憶、技能、工作區產出
│   └── 記憶/         平台共用記憶（scope=user；`共用記憶.md`＋`memory-user.jsonl`）
├── 記憶索引.md       哪些助理的平台記憶已投放、哪些尚未匯出
├── 01-助理/<顯示名>/
│   ├── 人物設定.md、頭像.png、README.md
│   ├── 對話紀錄/
│   │   ├── README.md          索引（則數、期間、每月檔案）
│   │   ├── 2026-09.md、2026-10.md …   每月一檔，台北時間，原文照錄
│   │   ├── 附件清單.md         附件 ↔ 附件與工作檔/ 的 hash 檔對照
│   │   ├── raw/messages.jsonl、raw/media.jsonl
│   │   └── 補充-本機凍結逐字稿-含工具呼叫/（若有）
│   ├── 記憶/（本機凍結檔＋`平台記憶.md` 與 jsonl）、記憶彙整.md、筆記與側寫/、附件與工作檔/、例行工作/
├── 02-群組/<群組名>/  群組設定、群內對話
└── 99-備份程式/       source/（本 repo 該 commit 的完整原始碼）、COMMIT、VERSION.md、對話統計.json
```

grok-bot-export 會在 `meta/workspace-copy.json` 明列被排除的大型目錄及備份輸出路徑；設 `INCLUDE_BULKY_DIRS=1` 可包含大型目錄（但備份本身不得遞迴複製）。

grok-bot-export 則保留原本的 `agents/<id>/`、`shared/`、`workspace/`，另外新增 `conversations/<顯示名>/`（同上格式）、`platform-memory/`（與 virtual-office 相同的人可讀記憶）、`agents/<id>/platform-memory/`（原始 jsonl）與 `meta/backup-program/`。

每則訊息格式：

```
<!-- entry:t12u seq:34 -->
### 2026-10-08 23:42:10 · 使用者

（訊息原文）
```

解壓後可直接 `grep -rn "關鍵字" 01-助理/*/對話紀錄/`。

## 資料來源

| 內容 | 來源 | 狀態 |
|---|---|---|
| 完整對話（所有助理） | `/home/box/agent-data/search-index.db`（`messages`、`media`） | **平台持續同步**，含 10/6 之後；匯出時先做一致性快照 |
| 補充逐字稿（含工具呼叫） | `agent-transcripts/<id>/<id>.jsonl`、`agents/<id>/store.db` | 本機凍結，內容約到 2026-09-16 |
| 人物設定、本機記憶、筆記、附件 | `agents/<id>/…` | 本機記憶停在 2026-10-06 15:10 |
| 平台記憶（phase 1） | `/workspace/exports/memory-drop/` | RecallMemory 投放：共用 `shared-user/memory-user.jsonl`，各助理 `agents/<id>/*.jsonl`。沒有投放就在索引標「尚未匯出」，不中斷備份 |
| 本機共用記憶、技能 | `user-memory/`、`workflows/` | 本機凍結 |
| 工作區產出 | `/workspace`（預設略過備份輸出目錄及大型依賴目錄；可設定 `INCLUDE_BULKY_DIRS=1`） | 即時 |

- 索引裡沒有訊息的助理（例如 New Bot `ba9f350c`）列在 `對話索引.md` 的「沒有訊息」區。
- 已刪除的助理（`deleted-agents.json`）：若索引還有訊息會以「（已刪除助理）」資料夾匯出；平台刪除助理時通常會一併清掉索引，這種情況只會在索引頁註明「無可備份內容」。

## 私密資料

**2026-10-09 決定移除私密規則，備份一律全量。** 程式裡已沒有暗號 hash、`--include-private` 閘門、路徑含「私密」的過濾，也沒有附件副本排除。所有筆記（包括資料夾或檔名含「私密」的）都跟其他筆記一樣，依原本的資料夾結構匯出。助理之後怎麼處理私密資料，由各組織自行規定。

因此備份檔本身就是敏感資料：只在本機保存（700／600），要傳出時請用一次性、端對端加密的方式。

## 已知限制

- **記憶 phase 1（2026-10-09）**：平台記憶不在本機檔裡。備份時若 `memory-drop/` 存在，會抄進人可讀 Markdown（依 tier：常駐 profile／自有 log／note，含日期與全文）並保留 jsonl。各助理只能投放自己的自有記憶；共用（scope=user）與常駐由讀得到的人投放。沒投放的助理是「尚未匯出」，不是備份失敗。本機 `memory/` 仍停在 2026-10-06，兩份並存。
- 平台索引只有雙方看得到的訊息文字，沒有工具呼叫或工具結果；這兩者只在補充逐字稿裡，而且只到約 9/16。
- 「使用者」是使用者一方，也可能是其他人或其他助理轉來的訊息（標籤可用 `NUWA_BACKUP_USER_LABEL` 改）。
- 群組對話裡的助理訊息，索引沒有標示是哪位助理。
- 附件與訊息的對應：本機逐字稿涵蓋的部分是精確對應，其他部分依副檔名推測（會註明），對不到的會另外列出。
- `search-index.db` 是平台的內部快取，格式日後可能改變；改了要更新 `nuwa_backup/conversations.py`。

## 程式結構

```
bin/export-virtual-office.sh    → python3 -m nuwa_backup.virtual_office
bin/export-grok-bot-backup.sh   原始備份（bash），內部呼叫 conversations / program
bin/verify-export.sh            → python3 -m nuwa_backup.verify
nuwa_backup/common.py           路徑、助理名冊、舊參數相容
nuwa_backup/conversations.py    對話匯出（Markdown／索引／附件清單／補充逐字稿）
nuwa_backup/program.py          把本 repo（HEAD）+ commit hash 放進備份
nuwa_backup/memory_drop.py      把 memory-drop 抄進人可讀記憶（phase 1）
nuwa_backup/verify.py           匯出驗證（含投放 jsonl 是否進了備份）
```

修改程式後請先 commit 再跑備份，這樣備份裡的 `source/` 才會跟實際執行的程式一致（有未提交修改時，`VERSION.md` 會出現警告）。

## 實機回歸測試

原始版本曾在助理 box 上運行；本次整理後的開源版本尚待於相同環境重新測試。請先以預設模式執行、核對 `meta/MANIFEST.json` 與 `meta/workspace-copy.json`，再設 `INCLUDE_BULKY_DIRS=1 INCLUDE_SUBAGENTS=1` 執行一次。完整性必須核對實際可取得的資料來源；缺少平台記憶投放不代表資料不存在。此工具是備份匯出，不提供一鍵還原。
