# Windows-native release tools（待独立 review / 真实验收）

入口为 `scripts/native_release.py`；`select`、`inspect`、`start`、`backup`、`restore` 默认只读/离线或 dry-run。缺少参数立即 BLOCKED，不提供最终 DB/storage 的隐含默认值。只有明确 `--execute --confirm-target <target_id>` 才读进程中的数据库连接串、探测端口/数据库或产生运行/备份/恢复副作用。`inspect` 没有执行模式。当前工具只完成离线验证，不代表可交付 release。

## 先决条件与明确选择

复用已存在的 Windows venv、D HTML/DOCX native venv、E tessdata、Node、既有 DB 容器和现有前端 dist；不安装、不下载、不启动 Compose、不迁移 DB、不构建 UI。若某项缺失或版本不符，工具拒绝，用户另行决定处理。普通 TXT/MD/PDF/HTML/DOCX/XLSX 检查为依赖/语言文件静态检查，真实 parser 语义需另验；旧 DOC 手工 LibreOffice 另存 DOCX，自动 gate 不开启。

需要用户确认：最终源码目录；主 Python / native Python / Node / Docker 的绝对路径；既有 DB 容器名/完整64位hex ID、明确本机named-pipe Docker endpoint和既有Docker config绝对目录、loopback DB端口和最终 DB名；已实际观察的PG cluster system_identifier与目标DB OID；对应已有 storage绝对路径（含objects目录）；已有 backup根和每次新增backup目录；API/UI端口；用途 release/acceptance/restore；源码对应迁移 revision。工具不创建/迁移目标DB、不把测试库升级为最终数据源。purpose=release拒绝默认rag及test/acceptance/integration/restore命名；保留集成库只能明确选acceptance。18193/14193仅已使用过的示例，不是最终目标批准。

主 venv 的直接依赖必须与选定 pyproject 中 `==` 版本相符；native需要BS4 4.15.0 / python-docx 1.2.0 / defusedxml 0.7.1及已有lxml/soupsieve，Python>=3.11。offline只读取选定venv的pyvenv.cfg/发行包METADATA，不import依赖。Node执行时检查>=22。前端需既有package-lock、Vite和dist；dist全部文件及源码指纹录入target，之后变化必须重新select/审核。模型名暂固定qwen3.5:4b / bge-m3:latest；模型实际可用性/digest及问答质量不由静态检查证明，必须在后续真实验收记录。

从未启动应用的终端开始，先选择这些已有资源。以下命令是未来用户操作，本轮未执行；没有参数中的路径/名称自动获得交付授权。

```powershell
$python = Read-Host 'Existing main Python.exe absolute path'
$source = Read-Host 'Reviewed final source absolute directory'
$native = Read-Host 'Existing native table Python.exe absolute path'
$node = Read-Host 'Existing Node.exe absolute path'
$docker = Read-Host 'Existing Docker.exe absolute path'
$container = Read-Host 'Existing database container name (no compose startup)'
$containerId = Read-Host 'Owner observed full immutable container ID'
$dockerEndpoint = Read-Host 'Explicit local npipe endpoint, no default/context guessing'
$dockerConfig = Read-Host 'Existing approved Docker config absolute directory (not read by offline check)'
$systemIdentifier = Read-Host 'Owner observed PG system_identifier for the selected cluster'
$databaseOid = Read-Host 'Owner observed OID of this selected database'
$dbName = Read-Host 'Owner selected database name'
$dbPort = Read-Host 'Existing loopback database port'
$storage = Read-Host 'Matching existing storage absolute directory'
$backupRoot = Read-Host 'Existing backup root absolute directory'
$tessdata = Read-Host 'Existing eng+chi_sim tessdata absolute directory'
$apiPort = Read-Host 'Approved API port'
$uiPort = Read-Host 'Approved UI port'
$revision = Read-Host 'Reviewed migration head (currently 0013_message_run_link)'
$purpose = Read-Host 'release or acceptance (test DB must use acceptance)'
$tool = Join-Path $source 'scripts/native_release.py'
$selectArgs = @('-I','-B',$tool,'select','--source-root',$source,'--python',$python,
  '--native-python',$native,'--node',$node,'--docker',$docker,'--db-container',$container,
  '--db-container-id',$containerId,'--docker-endpoint',$dockerEndpoint,'--docker-config-root',$dockerConfig,
  '--db-system-identifier',$systemIdentifier,'--database-oid',$databaseOid,
  '--database-name',$dbName,'--db-port',$dbPort,'--storage-root',$storage,
  '--backup-root',$backupRoot,'--tessdata-dir',$tessdata,'--api-port',$apiPort,
  '--ui-port',$uiPort,'--purpose',$purpose,'--migration-revision',$revision)
$selection = & $python @selectArgs
if ($LASTEXITCODE -ne 0) { throw 'TARGET_SELECTION_FAILED' }
$targetFile = Read-Host 'New reviewed target JSON absolute filename'
# Exclusive creation: an existing manifest is never replaced.
$file = [IO.File]::Open($targetFile,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
try {
  $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($selection -join "`n") + "`n")
  $file.Write($bytes,0,$bytes.Length)
} finally { $file.Dispose() }
$t = Get-Content -LiteralPath $targetFile -Raw | ConvertFrom-Json
$pair = @('--target-file',$targetFile,'--database-name',$dbName,'--storage-root',$storage)
& $python -I -B $tool inspect @pair
& $python -I -B $tool start @pair --component api
& $python -I -B $tool start @pair --component ui
```

审核输出必须为 `STATIC_PASS_RUNTIME_NOT_RUN` / `DRY_RUN`，且路径、DB名、purpose正确。target JSON只有白名单字段，不含URL/密码/key；不要手工塞凭据或修改已审核target。source/dist指纹变化、DB/storage选择不匹配、资源/版本缺失均拒绝。metadata版本正确不等于import成功；真正execute会在启动前做隔离import探针。

## 单独批准后启动，并完成一次问答

在两个终端各自读取同一个审核后的target并定义python/tool/pair。连接串仅以`RAG_NATIVE_DATABASE_URL`从本机安全渠道注入当前进程：必须是postgresql或postgresql+psycopg URL，host精确127.0.0.1、显式端口/DB与target一致，不允许query参数/远程host。URL不经命令行、不写文件、不回传聊天；需要交互时可用隐藏输入：

```powershell
$env:RAG_NATIVE_DATABASE_URL = [Net.NetworkCredential]::new('',(Read-Host 'Local database URL' -AsSecureString)).Password
& $python -I -B $tool start @pair --component api --execute --confirm-target $t.target_id
# 另一终端同样设置target/pair和进程连接串，再运行：
& $python -I -B $tool start @pair --component ui --execute --confirm-target $t.target_id
```

每个start检查选定schema及原始/normalized/asset对象键和hash与资料根一致，然后检查该组件端口；占用即拒绝，不自动换端口/host。实际DB一致性查询只读，但DB权限可能是superuser：loopback是应用监听边界，**不代表DB最小权限或OS安全隔离已完成**。容器内pg_dump/restore只在备份恢复执行时使用，启动不改变容器生命周期。

子进程环境仅继承Windows必要路径/TEMP，显式设置所有本地路由/数据路径；API `--no-env`绕过原.env loader，UI独立Vite config `envDir:false`且强制127.0.0.1/strictPort，cloud/Langfuse关闭。默认inline ingestion=true。API/UI均在当前命令前台等待，错误只报固定code，应用stdout/stderr不回显；Ctrl+C只终止当前入口创建的子进程句柄，无taskkill/全局进程查找/停止既有服务。子进程正常退出也不表示ready/问答通过。任何失败均保留既有DB/storage；错误详情如需诊断另行授权安全日志。

人工打开`http://127.0.0.1:<选定UI端口>`：创建或选中审核KB，UI选择/拖放一份经批准TXT/MD/PDF/HTML/DOCX/XLSX，等待ready，再在限定范围发一次实际问题、核对答案与引用原文，刷新确认历史/引用。记录源码/模型digest、调用预算、DB/storage身份和实际结果。六格式API历史证据不替代六格式UI导入，后者仍NOT_RUN；不得用healthz或进程存在代替完整问答验收。

## 备份与只向新空目标恢复

备份入口复用原manifest构造/验证函数。现有 `backup.ps1` 已改为显式参数的安全包装；不再自动compose up db，不再默认dump rag/拷贝cwd var/storage。每次output-dir必须是选定backup-root下尚不存在的直接子目录；存在就拒绝。verify-release.ps1已替换为最早拒绝legacy/Fresh/缺参并生成显式native计划的包装，不再运行旧默认DB/build/service链。该计划不是release PASS，不能替代真实验收。

单独批准且用户已暂停选定应用的所有写入/导入后，显式确认quiescent；工具不自行停止任何服务。先dry-run，审核后执行：

```powershell
$output = Read-Host 'New backup absolute directory under selected backup root'
& $python -I -B $tool backup @pair --output-dir $output
& $python -I -B $tool backup @pair --output-dir $output --execute --confirm-target $t.target_id --quiescent-confirmed
# 同等包装：backup.ps1 -TargetFile ... -DatabaseName ... -StorageRoot ... -OutputDir ... -Python ...
# 只有同时给 -Execute -ConfirmTarget <id> -QuiescentConfirmed 才真的备份。
```

执行前验证host DSN的current_database/system_identifier/DB OID与target一致，再检查明确endpoint上的完整容器ID、名称和127.0.0.1:<DB端口>发布；inspect仅格式化Id/Name/Ports，不读取容器env。容器内psql观察同一DB名/cluster ID/DB OID并比对，匹配后才传输。inspect/psql/dump/restore共用显式--host/--config与同一个白名单环境，无DOCKER_HOST/DOCKER_CONTEXT/USERPROFILE继承，exec仅用完整immutable容器ID。使用现有Docker exec流式pg_dump custom到新目录，复制明确storage、记录数据库dump/逐文件hash、源码/依赖/语言身份和target。凭据只经子进程环境，argv/输出无秘密。PGPASSWORD仅在执行时临时供容器中的PG工具使用，不保存到磁盘。备份文件/manifest含私人资料，只留本机，不能上传；target JSON上限64KiB、frontend lock上限4MiB、backup manifest上限16MiB。备份先写manifest.pending.json，用自身verified_backup读取/完整核对，再独占写manifest.json并再次自身核对，才返回成功；超限或往返失败写failure.json、保留未完成目录，restore拒绝failure标记。此命令验证备份文件但不宣称已恢复。失败目录/对象保留、无manifest完成标志，不自动删除/重试/覆盖。

恢复必须另外select一个purpose=restore target，DB名与备份原DB不同、storage是尚不存在的绝对目录且不与原storage重叠。新DB由用户另行批准创建且无用户表/视图/序列，工具不createdb/dropdb。使用相同受审核源码与迁移revision；先dry-run验证原备份manifest/全部hash和新目标，随后另行批准执行：

```powershell
# 按上述select流程，用明确的NEW_DB/NEW_STORAGE和purpose=restore创建新的targetFile。
# 重新读取该新target、定义pair并安全设置新DB的RAG_NATIVE_DATABASE_URL。
& $python -I -B $tool restore @pair --input-dir $output
& $python -I -B $tool restore @pair --input-dir $output --execute --confirm-target $t.target_id
```

restore检查备份完整性、目标不同且DB空，copytree只向新storage，pg_restore --single-transaction --exit-on-error只恢复新DB，随后核对schema/引用对象并写本机receipt。期间失败不自动清理新目录/数据库，需负责人核对后另行处理；不自动恢复/覆盖生产。旧SQL备份无native_target不能用此入口，会拒绝，需单独核对和迁移。新备份manifest JSON读取/写入使用同一16MiB容量策略，超限在完成之前拒绝，不静默截断；pending/failure证据保留。旧schema1 target/backup身份没有新增字段，不能自动猜测升级，须负责人另行审核。

## 后续必做的真实交付验收（本轮全部NOT_RUN）

1. 独立review代码与参数边界，预算gate等最后修改结束后冻结源码/模型/依赖/前端bundle身份，重新select最终target。
2. 按README/本文从无应用进程的状态复用已有资源启动，核对API/UI loopback、occupied拒绝和原18086/14186服务不受干扰，完成UI导入→索引→一次真实问答→引用/历史回读；补足六格式UI导入和OCR实际资源链，不把mock冒充运行证据。
3. 按原需求执行已批准的最终冻结回归，记录预算/版本/结果，与旧snapshot分开；verify-release.ps1的新入口仅生成明确native计划；旧-Fresh调用即时BLOCKED，不能将计划当实际最终回归结果。
4. 真实显式备份，在另外批准的新空DB+新storage做恢复演练并核对资料/引用，再演练回滚：只停止该入口自有进程、切回保留的上一已验证源码/bundle和其匹配DB/storage/target；绝不重置dirty worktree或把旧schema恢复覆盖生产。跨schema回滚必须先批准配套副本，禁止自动down -v/DROP。

运行/恢复/问答质量/最终回归/回滚尚未验收。当前源码仍dirty且预算gate另有修改，本工具不是最终release签发。Docker新依赖、重build和全多模态图表语义/手写/扫描表格结构不在本轮。


## 明确native gate计划与DB/Docker身份前提（review P1修正）

身份字段没有默认值。缺失endpoint/容器完整ID/cluster ID/DB OID或没有确认最终DB/storage时select拒绝。只允许明确的本机`npipe:////./pipe/<已确认pipe名>`，不自动选择context，不修改Docker系统context/env/安全设置，不支持未经批准的远程TCP/TLS目标。docker config目录由使用者明确选择，offline只查目录存在，不读取config/凭据内容；后续真实Docker CLI对该配置的使用需随执行单独批准。

在后续单独授权的目标观察中，可在host DSN和选定容器内分别只读取得：`SELECT current_database(), (SELECT system_identifier::text FROM pg_control_system()), (SELECT oid FROM pg_database WHERE datname=current_database())`。本轮没有执行该SQL，不能猜造实际值。若角色没有pg_control_system读取权限，执行拒绝；不会自动提权/授权或修改安全配置。loopback仍不代表superuser/DB权限隔离。

还需owner另外创建并观察恢复DB，其DB名和观察身份必须与原目标不同、storage为新目录；仍不用工具createdb/DROP。选择完两套target后：

```powershell
# 以下变量均为owner明确选定的真实target与新路径，不使用测试库/相对var默认。
& (Join-Path $source 'scripts/verify-release.ps1') -Python $python -TargetFile $targetFile `
  -DatabaseName $dbName -StorageRoot $storage -OutputDir $newBackupDir `
  -RestoreTargetFile $restoreTargetFile -RestoreDatabaseName $newDbName -RestoreStorageRoot $newStorage
```

缺参或旧`-Fresh`/Report/WithServer/HelperPython参数在任何建目录/默认DSN/构建/服务动作前exit2。完整native调用将参数以argv数组传入`native_release.py gate`，输出`NATIVE_GATE_PLAN_REAL_ACCEPTANCE_NOT_RUN`和inspect/start api/start ui/backup/restore命令，每条均保留明确target、DB/storage和新backup目录；它们默认dry-run。按返回的execution_confirmations分别批准各真实阶段，加`--execute --confirm-target <对应id>`；backup另需quiescent，restore前切换为其对应的本机安全DSN。restore命令只能在前一步备份已完成之后核验其input-dir。gate本身不读runtime DSN、不做真实操作，也没有自动release PASS选项。

仍必做：独立复核合并补丁，冻结源码/bundle/model身份，README从零启动并真实UI导入/一次问答/引用历史，最终冻结回归，明确静止备份、另批新空目标恢复与回滚。观察身份比对减少错目标风险，不是签名/唯一storage证书或并发锁；使用者仍需确认quiescent、保护原服务。全部真实动作本轮NOT_RUN。
